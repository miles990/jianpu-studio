"""Measure-aware editor: one model, cached geometry, paint visible music only."""
from __future__ import annotations

from bisect import bisect_right

from PySide6.QtCore import (
    QAbstractListModel,
    QItemSelection,
    QItemSelectionModel,
    QModelIndex,
    QPoint,
    QPointF,
    QRect,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QKeySequence, QPainter, QPainterPath, QPen, QRegion
from PySide6.QtWidgets import QAbstractItemView, QToolTip

from .model import Score


class ScoreModel(QAbstractListModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.score = Score()
        self.positions = []
        self.bars = []
        self.links = []
        self.total_ticks = 0
        self._notes = ()
        self._meter = self.score.meter

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._notes)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._notes):
            return None
        row = index.row()
        note = self._notes[row]
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.AccessibleTextRole,
                    Qt.ItemDataRole.AccessibleDescriptionRole, Qt.ItemDataRole.ToolTipRole):
            bar = self.positions[row] // self.bar_ticks + 1
            marks = []
            if note.finger >= 0:
                marks.append("空弦" if note.finger == 0 else f"{note.finger} 指")
            if note.bow:
                marks.append({"upbow": "推弓", "downbow": "拉弓"}[note.bow])
            if note.dynamic:
                marks.append("力度 " + note.dynamic)
            if note.slide:
                marks.append("上滑音" if note.slide == "up" else "下滑音")
            if note.articulation:
                marks.append({"staccato": "斷奏", "tenuto": "保持音", "accent": "重音", "fermata": "延長記號",
                              "trill": "顫音", "mordent": "波音"}[note.articulation])
            marks.extend(label for field, label in (("slur_start", "連弓起點"), ("slur_end", "連弓終點"),
                                                   ("tie", "延音線"), ("repeat_start", "反覆起點"),
                                                   ("repeat_end", "反覆終點")) if getattr(note, field))
            description = "，" + "、".join(marks) if marks else ""
            return f"第 {bar} 小節，第 {row + 1} 個音符：{note.token()}，{note.beats} 拍{description}"
        if role == Qt.ItemDataRole.UserRole:
            return note
        return None

    @property
    def bar_ticks(self):
        return int(self.score.bar_length * 16)

    def set_score(self, score):
        # The editor mutates the same Score instance. Keep an immutable snapshot
        # for change detection instead of comparing aliases of its notes list.
        notes = tuple(score.notes)
        changed = self._notes != notes or self._meter != score.meter
        self.score = score
        if not changed:
            return False
        self.beginResetModel()
        self._notes, self._meter = notes, score.meter
        self.positions, self.bars, self.links = [], [], []
        position, slur_start = 0, None
        bar_ticks = self.bar_ticks
        for i, note in enumerate(notes):
            bar = position // bar_ticks
            if not self.bars or self.bars[-1][0] != bar:
                self.bars.append((bar, []))
            self.bars[-1][1].append(i)
            self.positions.append(position)
            position += note.ticks
            if note.slur_end and slur_start is not None:
                self.links.append((slur_start, i, False))
                slur_start = None
            if note.slur_start:
                slur_start = i
            if note.tie and i + 1 < len(notes):
                self.links.append((i, i + 1, True))
        if slur_start is not None:
            self.links.append((slur_start, len(notes) - 1, False))
        self.total_ticks = position
        self.endResetModel()
        return True


class NoteList(QAbstractItemView):
    pitch = Signal(int)
    delete = Signal()
    command = Signal(str)
    currentRowChanged = Signal(int)
    selectionEdited = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        # Initialize geometry before installing the model: Qt may immediately
        # request a visual rectangle or scroll offset during a model reset.
        self.scale = 1.0
        self.zoom_percent = 100
        self.insertion_mode = "after"
        self.rects = []
        self.bar_rects = []
        self.bar_bottoms = []
        self.line_rects = []
        self.line_bottoms = []
        self.line_links = []
        self.note_lines = []
        self.content_width = 0
        self.content_height = 0
        self._layout_options = None
        self._selection_anchor = -1
        self.setObjectName("notes")
        self.score_model = ScoreModel(self)
        self.setModel(self.score_model)
        self.setSelectionMode(self.SelectionMode.ExtendedSelection)
        self.setHorizontalScrollMode(self.ScrollMode.ScrollPerPixel)
        self.setVerticalScrollMode(self.ScrollMode.ScrollPerPixel)
        self.setEditTriggers(self.EditTrigger.NoEditTriggers)
        self.setAccessibleName("簡譜編輯譜面")
        self.setAccessibleDescription("數字輸入音符；Shift 連選、Ctrl 多選；M 選小節；空白鍵切換插入與修改；Enter 播放。")
        self.setMinimumSize(320, 180)
        self.viewport().setMouseTracking(True)
        self.selectionModel().currentChanged.connect(lambda current, _: self.currentRowChanged.emit(current.row()))
        self.selectionModel().selectionChanged.connect(self._selection_changed)

    def _selection_changed(self, *_):
        self.selectionEdited.emit()
        self.viewport().update()

    def set_score(self, score):
        changed = self.score_model.set_score(score)
        if changed:
            self._selection_anchor = -1
        options = score.staff_size, score.bars_per_line
        if changed or options != self._layout_options:
            self._layout_options = options
            self.scale = self.zoom_percent / 100 * score.staff_size / 20
            self.relayout()
        else:
            self.viewport().update()
        return changed

    def set_zoom(self, percent):
        self.zoom_percent = max(25, min(400, percent))
        self.scale = self.zoom_percent / 100 * self.score_model.score.staff_size / 20
        self.relayout()
        self.scrollTo(self.currentIndex())

    def relayout(self):
        s = self.scale
        margin, gap, height = max(4, int(16*s)), max(3, int(12*s)), int(172*s)
        available = max(1, self.viewport().width() - margin * 2)
        rows, line, used = [], [], 0
        notes = self.score_model._notes
        per_line = self.score_model.score.bars_per_line
        for bar, indices in self.score_model.bars:
            weights = [max(70 if (notes[i].finger >= 0 or notes[i].bow or notes[i].articulation) else 48,
                           notes[i].ticks * 3) * s for i in indices]
            width = max(180*s, sum(weights) + 38*s)
            if line and ((per_line and len(line) >= per_line) or
                         (not per_line and used + gap + width > available)):
                rows.append(line)
                line, used = [], 0
            line.append((bar, indices, weights, width))
            used += width + (gap if len(line) > 1 else 0)
        if line:
            rows.append(line)
        self.rects = [QRect() for _ in notes]
        self.note_lines = [0] * len(notes)
        self.bar_rects, self.bar_bottoms = [], []
        self.line_rects, self.line_bottoms, self.line_links = [], [], [[] for _ in rows]
        y = margin
        self.content_width = self.viewport().width()
        for line_number, line in enumerate(rows):
            extra = max(0, (available - sum(b[3] for b in line) - gap*(len(line)-1)) / len(line))
            x = margin
            for bar, indices, weights, base in line:
                width = int(base + extra)
                frame = QRect(int(x), y, width, height)
                self.bar_rects.append((frame, bar, indices))
                self.bar_bottoms.append(frame.bottom())
                left = x + 19*s
                usable = width - 38*s
                total = sum(weights)
                for i, weight in zip(indices, weights):
                    cell = usable * weight / total
                    self.rects[i] = QRect(int(left), y + int(30*s), max(1, int(cell)), int(130*s))
                    self.note_lines[i] = line_number
                    left += cell
                x += width + gap
            self.line_rects.append(QRect(margin, y, int(x) - gap - margin, height))
            self.line_bottoms.append(y + height - 1)
            self.content_width = max(self.content_width, int(x) + margin - gap)
            y += height + gap
        self.content_height = y + margin
        for start, end, tie in self.score_model.links:
            first, last = self.note_lines[start], self.note_lines[end]
            for line_number in range(first, last + 1):
                frame = self.line_rects[line_number]
                x1 = self.note_center(start) + 7*s if line_number == first else frame.left() + 10*s
                x2 = self.note_center(end) - 7*s if line_number == last else frame.right() - 10*s
                if x2 <= x1:
                    x2 = x1 + 12*s
                baseline = frame.top() + (80 if tie else 36)*s
                path = QPainterPath(QPointF(x1, baseline))
                lift = (10 if tie else 15)*s
                path.cubicTo(x1 + (x2-x1)*0.25, baseline-lift,
                             x1 + (x2-x1)*0.75, baseline-lift, x2, baseline)
                self.line_links[line_number].append(path)
        self.updateGeometries()
        self.viewport().update()

    def note_center(self, row):
        note, rect = self.score_model._notes[row], self.rects[row]
        beats = max(1, int(note.beats)) if note.duration <= 2 else 1
        return rect.left() + rect.width() / (2 * beats)

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

    def indexAt(self, point):
        position = point + QPoint(self.horizontalOffset(), self.verticalOffset())
        for frame, _, indices in self.visible_bars():
            if frame.contains(position):
                for i in indices:
                    if self.rects[i].contains(position):
                        return self.model().index(i, 0)
        return QModelIndex()

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
            vertical.setValue(rect.top() - int(30*self.scale))
        elif hint == self.ScrollHint.PositionAtBottom:
            vertical.setValue(rect.bottom() + int(12*self.scale) - self.viewport().height())
        elif hint == self.ScrollHint.PositionAtCenter:
            vertical.setValue(rect.center().y() - self.viewport().height() // 2)
        else:
            start, end = rect.top() - int(30*self.scale), rect.bottom() + int(12*self.scale)
            if start < vertical.value():
                vertical.setValue(start)
            elif end >= vertical.value() + self.viewport().height():
                vertical.setValue(end - self.viewport().height() + 1)
        horizontal = self.horizontalScrollBar()
        if rect.left() < horizontal.value():
            horizontal.setValue(rect.left())
        elif rect.right() >= horizontal.value() + self.viewport().width():
            horizontal.setValue(rect.right() - self.viewport().width() + 1)

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
            target_y = self.line_rects[line].top() + direction*self.viewport().height()
            target_line = max(0, min(len(self.line_rects)-1, bisect_right(self.line_bottoms, target_y)))
            if target_line == line:
                target_line = max(0, min(len(self.line_rects)-1, line + direction))
            candidates = [i for _, _, indices in self.bar_rects if self.note_lines[indices[0]] == target_line for i in indices]
            row = min(candidates, key=lambda i: abs(self.note_center(i) - self.note_center(row)))
        elif action in (self.CursorAction.MoveLeft, self.CursorAction.MovePrevious, self.CursorAction.MoveUp):
            row = max(0, row - 1)
        else:
            row = min(count-1, row+1)
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

    def mousePressEvent(self, event):
        index = self.indexAt(event.position().toPoint())
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

    def select_current_bar(self):
        row = self.currentRow()
        if row < 0:
            return
        bar = self.score_model.positions[row] // self.score_model.bar_ticks
        for number, indices in self.score_model.bars:
            if number == bar:
                self.select_rows(indices, row)
                break

    def mouseDoubleClickEvent(self, event):
        position = event.position().toPoint() + QPoint(self.horizontalOffset(), self.verticalOffset())
        for frame, _, indices in self.visible_bars():
            if frame.contains(position):
                self.select_rows(indices)
                return
        super().mouseDoubleClickEvent(event)

    def viewportEvent(self, event):
        if event.type() == event.Type.ToolTip:
            index = self.indexAt(event.pos())
            if index.isValid():
                QToolTip.showText(event.globalPos(), index.data(Qt.ItemDataRole.ToolTipRole), self)
            else:
                QToolTip.hideText()
            return True
        return super().viewportEvent(event)

    def keyPressEvent(self, event):
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
                             (QKeySequence.StandardKey.Paste, "paste")):
            if event.matches(key):
                self.command.emit(command)
                return
        if event.modifiers() == Qt.KeyboardModifier.NoModifier:
            if len(event.text()) == 1 and event.text() in "01234567":
                self.pitch.emit(int(event.text()))
                return
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

    def _draw_repeat(self, painter, x, cy, start):
        s = self.scale
        painter.setPen(QPen(QColor("#344c59"), 2*s))
        painter.drawLine(QPointF(x, cy-20*s), QPointF(x, cy+20*s))
        second = x + (4 if start else -4)*s
        painter.setPen(QPen(QColor("#344c59"), s))
        painter.drawLine(QPointF(second, cy-20*s), QPointF(second, cy+20*s))
        painter.setBrush(QColor("#344c59"))
        dot_x = x + (10 if start else -10)*s
        for dot_y in (-6, 6):
            painter.drawEllipse(QPointF(dot_x, cy+dot_y*s), 1.7*s, 1.7*s)

    def _draw_marks(self, p, note, cx, cy, color):
        s = self.scale
        p.setPen(QPen(color, 1.5*s))
        p.setBrush(Qt.BrushStyle.NoBrush)
        mark_y = cy - 43*s
        if note.finger >= 0:
            p.setFont(QFont("Microsoft JhengHei", max(6, int(10*s))))
            p.drawText(QRect(int(cx-9*s), int(mark_y-12*s), int(18*s), int(20*s)),
                       Qt.AlignmentFlag.AlignCenter, "○一二三四"[note.finger])
        if note.bow:
            x = cx - (21*s if note.finger >= 0 else 0)
            if note.bow == "downbow":
                p.drawLine(QPointF(x-5*s, mark_y+4*s), QPointF(x-5*s, mark_y-6*s))
                p.drawLine(QPointF(x-5*s, mark_y-6*s), QPointF(x+5*s, mark_y-6*s))
                p.drawLine(QPointF(x+5*s, mark_y-6*s), QPointF(x+5*s, mark_y+4*s))
            else:
                p.drawLine(QPointF(x-5*s, mark_y-6*s), QPointF(x, mark_y+5*s))
                p.drawLine(QPointF(x, mark_y+5*s), QPointF(x+5*s, mark_y-6*s))
        if note.articulation:
            x = cx + (21*s if note.finger >= 0 or note.bow else 0)
            mark = {"staccato": "•", "tenuto": "—", "accent": ">", "fermata": "𝄐", "trill": "tr", "mordent": "𝆝"}[note.articulation]
            p.setFont(QFont("DejaVu Sans", max(7, int(11*s))))
            p.drawText(QRect(int(x-12*s), int(mark_y-13*s), int(24*s), int(22*s)), Qt.AlignmentFlag.AlignCenter, mark)
        if note.slide:
            x, y = cx - 23*s, cy - 18*s
            direction = -1 if note.slide == "up" else 1
            end = QPointF(x+10*s, y+direction*9*s)
            p.drawLine(QPointF(x, y), end)
            p.drawLine(end, QPointF(end.x()-5*s, end.y()))
            p.drawLine(end, QPointF(end.x(), end.y()-direction*5*s))
        if note.dynamic:
            font = QFont("DejaVu Serif", max(7, int(12*s)), QFont.Weight.Bold)
            font.setItalic(True)
            p.setFont(font)
            p.drawText(QRect(int(cx-22*s), int(cy+47*s), int(44*s), int(22*s)), Qt.AlignmentFlag.AlignCenter, note.dynamic)

    def paintEvent(self, event):
        p = QPainter(self.viewport())
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.viewport().rect(), QColor("#edf1f3"))
        if not self.rects:
            p.setPen(QColor("#465d69"))
            p.setFont(QFont("Microsoft JhengHei", 14))
            p.drawText(self.viewport().rect().adjusted(20, 20, -20, -20), Qt.AlignmentFlag.AlignCenter,
                       "從第一個音符開始\n\n按 1–7 加入音符，0 加入休止符\n也可以使用上方的音符按鈕")
            return
        p.translate(-self.horizontalOffset(), -self.verticalOffset())
        s = self.scale
        notes = self.score_model._notes
        bar_ticks = self.score_model.bar_ticks
        visible = list(self.visible_bars())
        for frame, bar, indices in visible:
            overflow = any(self.score_model.positions[i] % bar_ticks + notes[i].ticks > bar_ticks for i in indices)
            incomplete = indices[-1] == len(notes)-1 and self.score_model.total_ticks % bar_ticks != 0
            p.setBrush(QColor("#ffffff"))
            p.setPen(QPen(QColor("#c25b35" if overflow else "#d5dde2"), 1))
            p.drawRoundedRect(frame, 5*s, 5*s)
            p.setFont(QFont("Microsoft JhengHei", max(6, int(9*s))))
            p.setPen(QColor("#9b431f" if overflow else "#536a76"))
            p.drawText(frame.adjusted(int(12*s), int(7*s), -int(10*s), 0), Qt.AlignmentFlag.AlignTop,
                       f"{bar+1:02d}" + ("  · 跨小節，請調整" if overflow else "  · 待補拍" if incomplete else ""))
            if notes[indices[0]].repeat_start:
                self._draw_repeat(p, frame.left()+5*s, frame.top()+95*s, True)
            if notes[indices[-1]].repeat_end:
                self._draw_repeat(p, frame.right()-5*s, frame.top()+95*s, False)
            for i in indices:
                note, rect = notes[i], self.rects[i]
                selected = self.selectionModel().isSelected(self.model().index(i, 0))
                if selected:
                    p.setBrush(QColor("#d9efe9"))
                    p.setPen(QPen(QColor("#147b65"), 1.5))
                    p.drawRoundedRect(rect.adjusted(1, 0, -2, -1), 4*s, 4*s)
                color = QColor("#10644f" if selected else "#162f40")
                p.setPen(QPen(color, max(1, s)))
                long_beats = max(1, int(note.beats)) if note.duration <= 2 else 1
                cx, cy = self.note_center(i), frame.top() + 95*s
                p.setFont(QFont("DejaVu Sans", max(7, int(22*s)), QFont.Weight.DemiBold))
                number_rect = QRect(int(cx-14*s), int(cy-22*s), int(28*s), int(40*s))
                p.drawText(number_rect, Qt.AlignmentFlag.AlignCenter, str(note.degree))
                p.setFont(QFont("DejaVu Sans", max(6, int(13*s))))
                if note.accidental:
                    p.drawText(QPointF(cx-24*s, cy+5*s), "♯" if note.accidental == "#" else "♭")
                if note.dotted and note.duration > 2:
                    p.setBrush(color)
                    p.drawEllipse(QPointF(cx+14*s, cy), 1.7*s, 1.7*s)
                beams = {8: 1, 16: 2, 32: 3}.get(note.duration, 0)
                for beam in range(beams):
                    y = cy + (17 + beam*4)*s
                    p.drawLine(QPointF(cx-9*s, y), QPointF(cx+9*s, y))
                p.setBrush(color)
                for octave in range(abs(note.octave)):
                    y = cy - (23+6*octave)*s if note.octave > 0 else cy + (25+4*beams+6*octave)*s
                    p.drawEllipse(QPointF(cx, y), 1.5*s, 1.5*s)
                for beat in range(1, long_beats):
                    x = cx + rect.width()*beat/long_beats
                    p.drawLine(QPointF(x-7*s, cy), QPointF(x+7*s, cy))
                self._draw_marks(p, note, cx, cy, color)
        # Draw connections after all measure backgrounds so spans survive a
        # bar boundary. Cache one path per system, including wrapped spans.
        p.setPen(QPen(QColor("#344c59"), max(1, 1.3*s)))
        p.setBrush(Qt.BrushStyle.NoBrush)
        first = bisect_right(self.line_bottoms, self.verticalOffset() - 1)
        for line in range(first, len(self.line_rects)):
            if self.line_rects[line].top() > self.verticalOffset() + self.viewport().height():
                break
            for path in self.line_links[line]:
                p.drawPath(path)
        selection = self.selectionModel().selection()
        if self.insertion_mode != "edit" and not selection.isEmpty():
            if self.insertion_mode == "end":
                row = len(self.rects)-1
            elif self.insertion_mode == "before":
                row = min(r.top() for r in selection)
            else:
                row = max(r.bottom() for r in selection)
            rect = self.rects[row]
            x = rect.left() if self.insertion_mode == "before" else rect.right()
            p.setPen(QPen(QColor("#147b65"), 2))
            p.drawLine(x, rect.top()+3, x, rect.bottom()-3)
