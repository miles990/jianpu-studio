"""Measure-aware editor canvas: one model per edited part, cached geometry, paint visible music only.

The view shows every part of the score as braced systems; the item model holds the notes
of the part being edited (``ScoreModel.part``), so selection rows always refer to that
part. Clicking another part's note asks the window to switch parts. Layout lives in
``score_layout``, painting in ``score_paint``, shared mark drawing in ``glyphs``.
"""
from __future__ import annotations

from bisect import bisect_right

from PySide6.QtCore import (
    QAbstractListModel,
    QItemSelection,
    QItemSelectionModel,
    QModelIndex,
    QPoint,
    QRect,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QKeySequence, QRegion
from PySide6.QtWidgets import QAbstractItemView, QToolTip

from . import symbols, theme
from .i18n import tr
from .model import TICKS_PER_QUARTER, Score
from .score_layout import LayoutMixin, grace_tokens  # noqa: F401  (grace_tokens: public helper)
from .score_paint import PaintMixin

# Old articulation names (0.3.x) map to registry ids for accessible descriptions.
SELECT_NOTES, SELECT_BARS, SELECT_BARLINE = "notes", "bars", "barline"


def describe_marks(note) -> list[str]:
    """Readable names of a note's marks and lines, for tooltips and screen readers."""
    marks = []
    for mark in note.marks:
        symbol = symbols.SYMBOLS.get(mark)
        if symbol is None:
            continue
        if symbol.slot == "dyn":
            marks.append(tr("力度 {name}").format(name=symbol.canvas_text()))
        elif mark == "finger.0":
            marks.append(tr("空弦"))
        else:
            marks.append(symbol.display_name)
    if note.accidental == "n":
        marks.append(tr("還原記號"))
    if note.tuplet:
        marks.append(tr("三連音"))
    for sid in note.spans_start:
        symbol = symbols.SYMBOLS[sid]
        if symbol.family == "slur":
            marks.append(tr("連弓起點"))
        elif symbol.family == "hairpin":
            marks.append(tr("漸強起點") if "cresc" in sid else tr("漸弱起點"))
        else:
            marks.append(tr("{name}起點").format(name=symbol.display_name))
    for sid in note.spans_end:
        symbol = symbols.SYMBOLS[sid]
        if symbol.family == "slur":
            marks.append(tr("連弓終點"))
        elif symbol.family == "hairpin":
            marks.append(tr("漸強弱終點"))
        else:
            marks.append(tr("{name}終點").format(name=symbol.display_name))
    if note.grace_before:
        marks.append(tr("前倚音 {notes}").format(notes=note.grace_before))
    if note.grace_after:
        marks.append(tr("後倚音 {notes}").format(notes=note.grace_after))
    if note.tie:
        marks.append(tr("延音線"))
    for item in (*note.bar_start, *note.bar_end):
        marks.append(symbols.SYMBOLS[item].display_name)
    return marks


class ScoreModel(QAbstractListModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.score = Score()
        self.part = 0
        self.positions = []
        self.bars = []
        self.links = []
        self.tuplets = []
        self.hairpins = []
        self.total_ticks = 0
        self._notes = ()
        self._meter = None

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._notes)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._notes):
            return None
        row = index.row()
        note = self._notes[row]
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.AccessibleTextRole,
                    Qt.ItemDataRole.AccessibleDescriptionRole, Qt.ItemDataRole.ToolTipRole):
            bar = self.bar_of(self.positions[row]) + (0 if self.score.pickup else 1)
            marks = describe_marks(note)
            description = "，" + "、".join(marks) if marks else ""
            return tr("第 {bar} 小節，第 {n} 個音符：{token}，{beats} 拍{description}").format(
                bar=bar, n=row + 1, token=note.token(), beats=note.beats, description=description)
        if role == Qt.ItemDataRole.UserRole:
            return note
        return None

    @property
    def bar_ticks(self):
        return int(self.score.bar_length * TICKS_PER_QUARTER)

    def bar_of(self, position):
        """Bar index of a tick position (bar 0 is the pickup when the score has one)."""
        return self.score.bar_index(position)

    def set_score(self, score, part=None):
        # The editor mutates the same Score instance. Keep an immutable snapshot
        # for change detection instead of comparing aliases of its notes list.
        part = self.part if part is None else part
        part = max(0, min(part, len(score.parts) - 1))
        notes = tuple(score.parts[part].notes)
        key = (score.meter, score.pickup, part)
        changed = self._notes != notes or self._meter != key
        self.score = score
        if not changed:
            return False
        self.beginResetModel()
        self._notes, self._meter, self.part = notes, key, part
        self.positions, self.bars = [], []
        position = 0
        for i, note in enumerate(notes):
            bar = self.bar_of(position)
            if not self.bars or self.bars[-1][0] != bar:
                self.bars.append((bar, []))
            self.bars[-1][1].append(i)
            self.positions.append(position)
            position += note.ticks
        self.total_ticks = position
        self.endResetModel()
        return True


class NoteList(LayoutMixin, PaintMixin, QAbstractItemView):
    pitch = Signal(int)
    delete = Signal()
    command = Signal(str)
    currentRowChanged = Signal(int)
    selectionEdited = Signal()
    measureClicked = Signal(int)
    measureContextRequested = Signal(int, QPoint)
    partClicked = Signal(int, int)        # part, row: a note of another part was clicked
    barlineClicked = Signal(int)
    stampClicked = Signal(int)            # continuous apply: row clicked

    def __init__(self, parent=None):
        super().__init__(parent)
        # Initialize geometry before installing the model: Qt may immediately
        # request a visual rectangle or scroll offset during a model reset.
        self.scale = 1.0
        self.zoom_percent = 100
        self.insertion_mode = "after"
        self.colors = theme.canvas_colors(theme.LIGHT)
        self.systems, self.rects, self.part_rects, self.part_centers = [], [], [], []
        self.bar_rects, self.bar_bottoms, self.bar_frames = [], [], {}
        self.line_rects, self.line_bottoms, self.line_links, self.line_tuplets = [], [], [], []
        self.line_hairpins, self.line_spans, self.line_baselines, self.line_dynamic_offsets = [], [], [], []
        self.part_ranges, self.part_graces, self.part_positions, self.note_graces = [], [], [], []
        self.bar_items_cache, self.bar_onset_x = [], {}
        self.header_rect, self.header_blocks = QRect(), []
        self.paper_rect = QRect()
        self.empty_bar_rect = QRect()
        self.empty_button_rect = QRect()
        self.note_lines = []
        self.names_x = 0
        self.content_width = 0
        self.content_height = 0
        self.selection_kind = SELECT_NOTES
        self.selected_bars: list[int] = []
        self.selected_barline = -1
        self.hover_row = -1
        self.flash_row = -1
        self.play_note = None
        self.preview_target = None
        self.stamp = ""
        self.problems: dict = {}           # (part, bar) -> short text
        self.problem_bars: set = set()
        self.problem_barlines: set = set()
        self._arc_rows: dict = {}
        self._keep_kind = False
        self._layout_options = None
        self._selection_anchor = -1
        self.setObjectName("notes")
        self.score_model = ScoreModel(self)
        self.setModel(self.score_model)
        self.setSelectionMode(self.SelectionMode.ExtendedSelection)
        self.setHorizontalScrollMode(self.ScrollMode.ScrollPerPixel)
        self.setVerticalScrollMode(self.ScrollMode.ScrollPerPixel)
        self.setEditTriggers(self.EditTrigger.NoEditTriggers)
        self.setAccessibleName(tr("簡譜編輯譜面"))
        self.setAccessibleDescription(tr("數字輸入音符；Shift 連選、Ctrl 多選；M 選小節；空白鍵切換插入與修改；Enter 播放。"))
        self.setMinimumSize(320, 180)
        self.viewport().setMouseTracking(True)
        self.selectionModel().currentChanged.connect(lambda current, _: self.currentRowChanged.emit(current.row()))
        self.selectionModel().selectionChanged.connect(self._selection_changed)
        self._flash = QTimer(self)
        self._flash.setSingleShot(True)
        self._flash.setInterval(300)
        self._flash.timeout.connect(self._end_flash)

    # ---- data
    def _selection_changed(self, *_):
        if not self._keep_kind:
            self.selection_kind = SELECT_NOTES
            self.selected_bars = []
            self.selected_barline = -1
        self.selectionEdited.emit()
        self.viewport().update()

    def set_colors(self, colors):
        self.colors = colors
        self.viewport().update()

    @property
    def part(self) -> int:
        return self.score_model.part

    def set_score(self, score, part=None):
        changed = self.score_model.set_score(score, part)
        if changed:
            self._selection_anchor = -1
            self._arc_rows = {}
        options = (score.staff_size, score.bars_per_line, score.title, score.composer, score.key, score.meter,
                   score.tempo, score.pickup, tuple(p.name for p in score.parts),
                   tuple(tuple(p.notes) for p in score.parts) if len(score.parts) > 1 else len(score.parts),
                   score.print_variant)
        if changed or options != self._layout_options:
            self._layout_options = options
            self._arc_rows = {}
            self.scale = self.zoom_percent / 100 * score.staff_size / 20
            self.relayout()
        else:
            self.viewport().update()
        return changed

    def refresh_language(self):
        """Texts drawn on the canvas or read by assistive tools follow the language."""
        self.setAccessibleName(tr("簡譜編輯譜面"))
        self.setAccessibleDescription(tr("數字輸入音符；Shift 連選、Ctrl 多選；M 選小節；空白鍵切換插入與修改；Enter 播放。"))
        count = self.score_model.rowCount()
        if count:
            self.score_model.dataChanged.emit(self.score_model.index(0), self.score_model.index(count - 1))
        self.relayout()

    def set_zoom(self, percent):
        self.zoom_percent = max(25, min(400, percent))
        self.scale = self.zoom_percent / 100 * self.score_model.score.staff_size / 20
        self.relayout()
        self.scrollTo(self.currentIndex())

    def flash(self, row):
        self.flash_row = row
        self._flash.start()
        self.viewport().update()

    def _end_flash(self):
        self.flash_row = -1
        self.viewport().update()

    # ---- geometry API used by Qt and the window
    def updateGeometries(self):
        if not hasattr(self, "content_height"):
            return
        self.verticalScrollBar().setPageStep(self.viewport().height())
        self.verticalScrollBar().setRange(0, max(0, self.content_height - self.viewport().height()))
        self.horizontalScrollBar().setPageStep(self.viewport().width())
        self.horizontalScrollBar().setRange(0, max(0, self.content_width - self.viewport().width()))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "score_model"):
            self.relayout()

    def horizontalOffset(self):
        return self.horizontalScrollBar().value()

    def verticalOffset(self):
        return self.verticalScrollBar().value()

    def isIndexHidden(self, index):
        return False

    def visible_bars(self):
        first = bisect_right(self.bar_bottoms, self.verticalOffset() - 1)
        bottom = self.verticalOffset() + self.viewport().height()
        # Do not slice the tail of a 10k-note score on each paint.
        for position in range(first, len(self.bar_rects)):
            entry = self.bar_rects[position]
            if entry[0].top() > bottom:
                break
            if entry[0].right() >= self.horizontalOffset() and entry[0].left() <= self.horizontalOffset() + self.viewport().width():
                yield entry

    def _content(self, point):
        return point + QPoint(self.horizontalOffset(), self.verticalOffset())

    def indexAt(self, point):
        if not self.viewport().rect().contains(point):
            return QModelIndex()
        position = self._content(point)
        for frame, _, indices in self.visible_bars():
            if frame.contains(position):
                for i in indices:
                    if self.rects[i].contains(position):
                        return self.model().index(i, 0)
        return QModelIndex()

    def other_part_at(self, point):
        """(part, row) of a note of another part under ``point``, else None."""
        if not self.viewport().rect().contains(point):
            return None
        position = self._content(point)
        for number in self._visible_systems():
            system = self.systems[number]
            if not system.rect.adjusted(-4, 0, 4, 0).contains(position):
                continue
            for part in system.rows:
                if part == self.part:
                    continue
                ranges = self.part_ranges[part]
                for bar in system.bars:
                    if bar < len(ranges):
                        a, b = ranges[bar]
                        for r in range(a, b):
                            if self.part_rects[part][r].contains(position):
                                return part, r
        return None

    def part_name_at(self, point):
        """A click on the part names left of a system switches parts (design §3.3)."""
        position = self._content(point)
        for number in self._visible_systems():
            system = self.systems[number]
            if not (self.names_x <= position.x() < system.rect.left()):
                continue
            for part, row in system.rows.items():
                if row.top + 8 <= position.y() <= row.bottom:
                    return part
        return None

    def measure_at(self, point):
        """Return a zero-based measure number for a viewport position."""
        if not self.viewport().rect().contains(point):
            return None
        position = self._content(point)
        for frame, number, _ in self.visible_bars():
            if frame.contains(position):
                return number
        return None

    def barline_at(self, point):
        """Boundary k (between bar k-1 and k) within ±6 px of a bar line, below the number strip."""
        if not self.viewport().rect().contains(point):
            return None
        position = self._content(point)
        s = self.scale
        for number in self._visible_systems():
            system = self.systems[number]
            if not (system.rect.top() + 18 * s <= position.y() <= system.rect.bottom()):
                continue
            for bar in system.bars:
                frame = self.bar_frames[bar]
                if abs(position.x() - frame.left()) <= 6:
                    return bar
                if abs(position.x() - frame.right()) <= 6:
                    return bar + 1
        return None

    def reset_selection(self):
        """Nothing selected, back to note selection (a new score or part)."""
        self._keep_kind = True
        try:
            self.selectionModel().clearSelection()
        finally:
            self._keep_kind = False
        self.selection_kind, self.selected_bars, self.selected_barline = SELECT_NOTES, [], -1
        self.preview_target = None
        self.play_note = None

    def select_bar(self, number, current=None):
        return self.select_bars([number], current)

    def select_bars(self, numbers, current=None):
        rows = []
        for bar, indices in self.score_model.bars:
            if bar in numbers:
                rows += indices
        if not rows:
            return False
        self._keep_kind = True
        try:
            self.select_rows(rows, current if current in rows else rows[0])
        finally:
            self._keep_kind = False
        self.selection_kind = SELECT_BARS
        self.selected_bars = sorted(set(numbers))
        self.selected_barline = -1
        self.selectionEdited.emit()
        self.viewport().update()
        return True

    def select_barline(self, boundary):
        self._keep_kind = True
        try:
            self.selectionModel().clearSelection()
        finally:
            self._keep_kind = False
        self.selection_kind = SELECT_BARLINE
        self.selected_barline = boundary
        self.selected_bars = []
        self.selectionEdited.emit()
        self.viewport().update()

    def visualRect(self, index):
        if not index.isValid() or index.model() is not self.model() or not 0 <= index.row() < len(self.rects):
            return QRect()
        return self.rects[index.row()].translated(-self.horizontalOffset(), -self.verticalOffset())

    def scrollTo(self, index, hint=QAbstractItemView.ScrollHint.EnsureVisible):
        if not index.isValid() or not 0 <= index.row() < len(self.rects):
            return
        rect = self.rects[index.row()]
        vertical = self.verticalScrollBar()
        if hint == self.ScrollHint.PositionAtTop:
            vertical.setValue(rect.top() - int(30 * self.scale))
        elif hint == self.ScrollHint.PositionAtBottom:
            vertical.setValue(rect.bottom() + int(12 * self.scale) - self.viewport().height())
        elif hint == self.ScrollHint.PositionAtCenter:
            vertical.setValue(rect.center().y() - self.viewport().height() // 2)
        else:
            start, end = rect.top() - int(40 * self.scale), rect.bottom() + int(16 * self.scale)
            if start < vertical.value():
                vertical.setValue(start)
            elif end >= vertical.value() + self.viewport().height():
                vertical.setValue(end - self.viewport().height() + 1)
        horizontal = self.horizontalScrollBar()
        if rect.left() < horizontal.value():
            horizontal.setValue(rect.left())
        elif rect.right() >= horizontal.value() + self.viewport().width():
            horizontal.setValue(rect.right() - self.viewport().width() + 1)

    def scroll_to_system(self, number):
        """Keep a system in the upper half of the view (playback)."""
        if not 0 <= number < len(self.systems):
            return
        rect = self.systems[number].rect
        bar = self.verticalScrollBar()
        if rect.top() < bar.value() or rect.bottom() > bar.value() + self.viewport().height() * 0.75:
            bar.setValue(rect.top() - int(20 * self.scale))

    def moveCursor(self, action, modifiers):
        count = self.model().rowCount()
        row = self.currentRow()
        if not count:
            return QModelIndex()
        if action == self.CursorAction.MoveHome:
            row = 0
        elif action == self.CursorAction.MoveEnd:
            row = count - 1
        elif action in (self.CursorAction.MovePageUp, self.CursorAction.MovePageDown):
            if row < 0:
                row = 0
            direction = -1 if action == self.CursorAction.MovePageUp else 1
            line = self.note_lines[row]
            target_y = self.line_rects[line].top() + direction * self.viewport().height()
            target_line = max(0, min(len(self.line_rects) - 1, bisect_right(self.line_bottoms, target_y)))
            if target_line == line:
                target_line = max(0, min(len(self.line_rects) - 1, line + direction))
            candidates = [i for _, _, indices in self.bar_rects if self.note_lines[indices[0]] == target_line for i in indices]
            if candidates:
                row = min(candidates, key=lambda i: abs(self.note_center(i) - self.note_center(row)))
        elif action in (self.CursorAction.MoveLeft, self.CursorAction.MovePrevious, self.CursorAction.MoveUp):
            row = max(0, row - 1)
        else:
            row = min(count - 1, row + 1)
        return self.model().index(row, 0)

    def setSelection(self, rect, flags):
        rect = rect.normalized().translated(self.horizontalOffset(), self.verticalOffset())
        selection = QItemSelection()
        for frame, _, indices in self.visible_bars():
            if frame.intersects(rect):
                for i in indices:
                    if self.rects[i].intersects(rect):
                        index = self.model().index(i, 0)
                        selection.select(index, index)
        self.selectionModel().select(selection, flags)

    def visualRegionForSelection(self, selection):
        # Repainting the viewport is bounded by visible bars, even for Select All.
        return QRegion(self.viewport().rect())

    def currentRow(self):
        return self.currentIndex().row()

    def setCurrentRow(self, row):
        self.setCurrentIndex(self.model().index(row, 0))

    def selected_rows(self):
        return sorted(i.row() for i in self.selectionModel().selectedIndexes())

    def select_rows(self, rows, current=None):
        selection = QItemSelection()
        rows = sorted(set(r for r in rows if 0 <= r < self.model().rowCount()))
        # Coalesce adjacent ranges so select-all/copy does not create 10,000
        # separate QItemSelectionRange objects.
        if rows:
            start = previous = rows[0]
            for row in rows[1:]:
                if row != previous + 1:
                    selection.select(self.model().index(start, 0), self.model().index(previous, 0))
                    start = row
                previous = row
            selection.select(self.model().index(start, 0), self.model().index(previous, 0))
        self.selectionModel().select(selection, QItemSelectionModel.SelectionFlag.ClearAndSelect)
        row = current if current in rows else (rows[-1] if rows else -1)
        self.selectionModel().setCurrentIndex(self.model().index(row, 0), QItemSelectionModel.SelectionFlag.NoUpdate)
        self._selection_anchor = row
        self.viewport().update()

    def _extend_selection(self, row, additive=False):
        anchor = self._selection_anchor
        if not 0 <= anchor < self.model().rowCount():
            anchor = max(0, self.currentRow())
            self._selection_anchor = anchor
        first, last = sorted((anchor, row))
        selection = QItemSelection(self.model().index(first, 0), self.model().index(last, 0))
        flag = QItemSelectionModel.SelectionFlag.Select if additive else QItemSelectionModel.SelectionFlag.ClearAndSelect
        self.selectionModel().select(selection, flag)
        index = self.model().index(row, 0)
        self.selectionModel().setCurrentIndex(index, QItemSelectionModel.SelectionFlag.NoUpdate)
        self.scrollTo(index)

    # ---- mouse
    def mousePressEvent(self, event):
        point = event.position().toPoint()
        position = self._content(point)
        left = event.button() == Qt.MouseButton.LeftButton
        if left and self.empty_button_rect.contains(position):
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self.command.emit("append_bar")
            event.accept()
            return
        if left:
            name_part = self.part_name_at(point)
            if name_part is not None and name_part != self.part:
                self.setFocus(Qt.FocusReason.MouseFocusReason)
                self.partClicked.emit(name_part, -1)
                event.accept()
                return
            other = self.other_part_at(point)
            if other is not None:
                self.setFocus(Qt.FocusReason.MouseFocusReason)
                self.partClicked.emit(*other)
                event.accept()
                return
        index = self.indexAt(point)
        if left and index.isValid() and self.stamp and not event.modifiers():
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self.select_rows([index.row()], index.row())
            self.stampClicked.emit(index.row())
            event.accept()
            return
        if left and not index.isValid():
            boundary = self.barline_at(point)
            if boundary is not None:
                self.setFocus(Qt.FocusReason.MouseFocusReason)
                self.select_barline(boundary)
                self.barlineClicked.emit(boundary)
                event.accept()
                return
        bar = self.measure_at(point)
        if not index.isValid() and bar is not None and event.button() in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton):
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            indices = next((indices for number, indices in self.score_model.bars if number == bar), None)
            if indices is None:
                event.accept()
                return
            selected = set(self.selected_rows())
            if event.button() == Qt.MouseButton.RightButton and all(i in selected for i in indices):
                event.accept()
                return
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                anchor = self._selection_anchor if self._selection_anchor >= 0 else self.currentRow()
                if anchor < 0:
                    anchor = indices[0]
                anchor_bar = self.score_model.bar_of(self.score_model.positions[anchor])
                low, high = sorted((anchor_bar, bar))
                bars = list(range(low, high + 1))
                if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                    bars = sorted(set(bars) | set(self.selected_bars))
                self.select_bars(bars, indices[-1] if anchor <= indices[0] else indices[0])
                self._selection_anchor = anchor
            elif event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                bars = set(self.selected_bars) if self.selection_kind == SELECT_BARS else set()
                bars ^= {bar}
                rows = set(selected)
                if all(i in rows for i in indices):
                    rows.difference_update(indices)
                else:
                    rows.update(indices)
                self._keep_kind = True
                try:
                    self.select_rows(rows, indices[0])
                finally:
                    self._keep_kind = False
                self.selection_kind = SELECT_BARS
                self.selected_bars = sorted(bars)
                self.selectionEdited.emit()
            else:
                self.select_bar(bar)
            if event.button() == Qt.MouseButton.LeftButton:
                self.measureClicked.emit(bar)
            event.accept()
            return
        if (event.button() == Qt.MouseButton.LeftButton and index.isValid()
                and event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            # Musical order is linear even when the score wraps. Qt's default
            # rectangle would select only columns between the two endpoints.
            self._extend_selection(index.row(), bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier))
            event.accept()
            return
        super().mousePressEvent(event)
        if index.isValid():
            self._selection_anchor = index.row()

    def mouseMoveEvent(self, event):
        index = self.indexAt(event.position().toPoint())
        row = index.row() if index.isValid() else -1
        if row != self.hover_row:
            self.hover_row = row
            self.viewport().update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        if self.hover_row != -1:
            self.hover_row = -1
            self.viewport().update()
        super().leaveEvent(event)

    def select_current_bar(self):
        row = self.currentRow()
        if row < 0:
            return
        bar = self.score_model.bar_of(self.score_model.positions[row])
        self.select_bar(bar, row)

    def mouseDoubleClickEvent(self, event):
        position = self._content(event.position().toPoint())
        for frame, bar, indices in self.visible_bars():
            if frame.contains(position):
                self.select_bar(bar)
                return
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event):
        # MainWindow may instead use Qt's CustomContextMenu policy and call
        # measure_at(), which avoids opening two menus for one gesture.
        bar = self.measure_at(event.pos())
        if bar is not None:
            self.measureContextRequested.emit(bar, event.globalPos())
            event.accept()
            return
        super().contextMenuEvent(event)

    def viewportEvent(self, event):
        if event.type() == event.Type.ToolTip:
            index = self.indexAt(event.pos())
            if index.isValid():
                text = index.data(Qt.ItemDataRole.ToolTipRole)
                note = self.score_model._notes[index.row()]
                from .symbols_ui import silent
                quiet = [symbols.SYMBOLS[m].display_name for m in note.marks if silent(symbols.SYMBOLS[m])]
                if quiet:
                    text += "\n" + tr("只印出，試聽不會表現：{names}").format(names="、".join(quiet))
                QToolTip.showText(event.globalPos(), text, self)
            else:
                other = self.other_part_at(event.pos())
                bar = self.measure_at(event.pos())
                if other is not None:
                    name = self.score_model.score.parts[other[0]].name or tr("第 {n} 聲部").format(n=other[0] + 1)
                    QToolTip.showText(event.globalPos(), tr("點一下切換到「{name}」並選取這個音").format(name=name), self)
                elif self.barline_at(event.pos()) is not None:
                    QToolTip.showText(event.globalPos(), tr("小節線 · 點選後可加小節線、反覆或跳躍記號"), self)
                elif bar is not None:
                    number = bar + (0 if self.score_model.score.pickup else 1)
                    QToolTip.showText(event.globalPos(), tr("第 {bar} 小節 · 點選整小節；右鍵開啟小節操作").format(bar=number), self)
                else:
                    QToolTip.hideText()
            return True
        return super().viewportEvent(event)

    # ---- keyboard
    def keyPressEvent(self, event):
        modifiers = event.modifiers()
        if modifiers == Qt.KeyboardModifier.ControlModifier:
            measures = {Qt.Key.Key_Insert: "insert_bars", Qt.Key.Key_B: "append_bar",
                        Qt.Key.Key_Delete: "delete_bars", Qt.Key.Key_Backspace: "delete_bars"}
            if event.key() in measures:
                self.command.emit(measures[event.key()])
                return
        if modifiers == Qt.KeyboardModifier.NoModifier and event.key() == Qt.Key.Key_Insert:
            self.command.emit("insert_bar_before")
            return
        if modifiers == Qt.KeyboardModifier.AltModifier and event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            self.command.emit("part_up" if event.key() == Qt.Key.Key_Up else "part_down")
            return
        if modifiers & Qt.KeyboardModifier.AltModifier and Qt.Key.Key_1 <= event.key() <= Qt.Key.Key_9:
            self.command.emit(f"favorite_{event.key() - Qt.Key.Key_0}")
            return
        navigation = {Qt.Key.Key_Left: self.CursorAction.MoveLeft, Qt.Key.Key_Right: self.CursorAction.MoveRight,
                      Qt.Key.Key_Up: self.CursorAction.MoveUp, Qt.Key.Key_Down: self.CursorAction.MoveDown,
                      Qt.Key.Key_Home: self.CursorAction.MoveHome, Qt.Key.Key_End: self.CursorAction.MoveEnd,
                      Qt.Key.Key_PageUp: self.CursorAction.MovePageUp, Qt.Key.Key_PageDown: self.CursorAction.MovePageDown}
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier and event.key() in navigation:
            index = self.moveCursor(navigation[event.key()], event.modifiers())
            if index.isValid():
                self._extend_selection(index.row(), bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier))
            return
        for key, command in ((QKeySequence.StandardKey.Copy, "copy"), (QKeySequence.StandardKey.Cut, "cut"),
                             (QKeySequence.StandardKey.Paste, "paste"), (QKeySequence.StandardKey.Find, "search")):
            if event.matches(key):
                self.command.emit(command)
                return
        if event.modifiers() in (Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.KeypadModifier):
            if len(event.text()) == 1 and event.text() in "01234567":
                self.pitch.emit(int(event.text()))
                return
            if event.text() == "/":
                self.command.emit("search")
                return
        if event.modifiers() == Qt.KeyboardModifier.NoModifier:
            if event.key() == Qt.Key.Key_M:
                self.select_current_bar()
                return
            commands = {Qt.Key.Key_Delete: "delete", Qt.Key.Key_Backspace: "delete", Qt.Key.Key_Up: "raise",
                        Qt.Key.Key_Down: "lower", Qt.Key.Key_BracketLeft: "shorter", Qt.Key.Key_BracketRight: "longer",
                        Qt.Key.Key_Period: "dot", Qt.Key.Key_Space: "toggle_mode", Qt.Key.Key_Escape: "clear",
                        Qt.Key.Key_Return: "play", Qt.Key.Key_Enter: "play", Qt.Key.Key_Q: "duration_1",
                        Qt.Key.Key_W: "duration_2", Qt.Key.Key_E: "duration_4", Qt.Key.Key_R: "duration_8",
                        Qt.Key.Key_A: "duration_16", Qt.Key.Key_S: "duration_32"}
            if event.key() in commands:
                self.command.emit(commands[event.key()])
                return
        if event.modifiers() == Qt.KeyboardModifier.ControlModifier and event.key() == Qt.Key.Key_D:
            self.command.emit("duplicate")
            return
        if event.modifiers() == Qt.KeyboardModifier.AltModifier and event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            self.command.emit("previous" if event.key() == Qt.Key.Key_Left else "next")
            return
        super().keyPressEvent(event)
        if event.key() in navigation:
            self._selection_anchor = self.currentRow()

    def set_problems(self, problems: dict, bars: set, barlines: set):
        self.problems, self.problem_bars, self.problem_barlines = problems, bars, barlines
        self.viewport().update()
