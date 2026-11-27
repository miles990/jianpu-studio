"""The symbol palette (wow/design.md §2): search, instrument filter, categories, cells, detail.

Everything shown comes from the registry (``symbols`` / ``symbols_ui``). Whether a cell
can be used, and why not, comes from ``notation_edit.symbol_state`` through the
``state`` callback the window provides, so the palette never has rules of its own.
The grid and the search results are one hand-painted widget (no button per symbol).
"""
from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QFontMetricsF, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from . import glyphs, icons, symbols, symbols_ui
from .i18n import N_, tr
from .symbols_ui import CATEGORY_ORDER, CATEGORY_SHORT
from .widgets import label, tool

PREVIEW_DIR = Path(__file__).parent / "assets" / "symbols"
INSTRUMENT_NAMES = {"erhu": N_("二胡"), "guzheng": N_("古箏"), "pipa": N_("琵琶"), "dizi": N_("笛子"),
                    "common": N_("通用"), "ruan": N_("中阮"), "hulusi": N_("葫蘆絲"), "yangqin": N_("揚琴"),
                    "all": symbols_ui.ALL_GROUP}  # catalog sources; show tr(value)
FAVORITES, RECENTS = "favorites", "recents"
LABEL_MIN_PX = 10           # smallest cell-label type before a name would be clipped
SECTION_ORDER = (FAVORITES, RECENTS, *CATEGORY_ORDER)
SECTION_SHORT = {FAVORITES: N_("常用"), RECENTS: N_("最近"), **CATEGORY_SHORT}  # catalog sources; show tr(value)
STATE_BUDGET = 0.035     # seconds of validation per paint; the rest is computed on the next tick


def base_sid(sid: str) -> str:
    """A 「加上文字「…」」 result (``text.free:中速``) is the free-text symbol."""
    return "text.free" if symbols_ui.text_word(sid) else sid


def preview_path(symbol_id: str) -> Path:
    return PREVIEW_DIR / f"{symbol_id}.png"


# Unavailable cells stay readable (review d1/10): the name is a text colour with at least
# 4.5:1 on the panel and the drawn mark keeps 3:1; the dashed border and the missing fill
# say "unavailable", not a faded name. Checked in tests from the real tokens.
DISABLED_NAME_ROLE = "text2"
DISABLED_INK_ALPHA = 0.5


class Entry:
    """One painted item: a cell, a chip (常用/最近), a result row or a section heading."""
    __slots__ = ("kind", "rect", "sid", "section", "text", "key")

    def __init__(self, kind, rect, sid="", section="", text="", key=""):
        self.kind, self.rect, self.sid, self.section, self.text, self.key = kind, rect, sid, section, text, key


class SymbolGrid(QAbstractScrollArea):
    """Hand-painted cells (browse) or rows (search), with hover, focus, keyboard and states."""

    activated = Signal(str, str)        # symbol id, how: click | stay | continuous
    hovered = Signal(str)               # "" when the mouse leaves
    focusChanged = Signal(str)
    sectionChanged = Signal(str)
    escapePressed = Signal()
    contextRequested = Signal(str, QPoint)

    def __init__(self, palette):
        super().__init__()
        self.palette = palette
        self.setObjectName("symbol_grid")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.viewport().setMouseTracking(True)
        self.entries: list[Entry] = []
        self.sections: dict[str, int] = {}
        self.focus_index = -1
        self.hover_index = -1
        self.mode = "grid"
        self.results: list = []
        self.content_height = 0
        self.compact = False
        self.setAccessibleName(tr("記號格子"))
        self.retry = QTimer(self)
        self.retry.setSingleShot(True)
        self.retry.setInterval(30)
        self.retry.timeout.connect(self.viewport().update)
        self.verticalScrollBar().valueChanged.connect(self._scrolled)

    # ---- layout
    def sizeHint(self):
        return QSize(260, 400)

    def relayout(self):
        width = self.viewport().width()
        if width <= 0:
            return
        pal = self.palette
        self.entries, self.sections = [], {}
        y, pad = 6, 8
        fm = QFontMetricsF(self.font())
        if self.mode == "results":
            for sid in self.results:
                self.entries.append(Entry("row", QRectF(4, y, width - 8, 48), sid))
                y += 52
            self.content_height = y + 6
            self._finish()
            return
        cols = 3 if width >= 228 and not self.compact else 2
        gap = 4
        cell_w = (width - 2 * pad - gap * (cols - 1)) / cols
        cell_h = 62 if cols == 3 else 38
        for section in SECTION_ORDER:
            ids = pal.section_ids(section)
            if not ids and section != FAVORITES:
                continue
            self.sections[section] = len(self.entries)
            title = pal.section_title(section, len(ids))
            self.entries.append(Entry("head", QRectF(pad, y, width - 2 * pad, 24), section=section, text=title))
            y += 26
            if section in (FAVORITES, RECENTS):
                x = pad
                for index, sid in enumerate(ids):
                    name = symbols_ui.short_name(symbols.SYMBOLS[sid])
                    key = str(index + 1) if section == FAVORITES else ""
                    w = 34 + fm.horizontalAdvance(name) + (12 if key else 0) + 10
                    w = min(w, width - 2 * pad)
                    if x + w > width - pad and x > pad:
                        x, y = pad, y + 32
                    self.entries.append(Entry("chip", QRectF(x, y, w, 28), sid, section, name, key))
                    x += w + gap
                if not ids:
                    self.entries.append(Entry("empty", QRectF(pad, y, width - 2 * pad, 28), section=section,
                                              text=tr("右鍵點記號可加到常用")))
                y += 36
                continue
            for index, sid in enumerate(ids):
                col = index % cols
                if col == 0 and index:
                    y += cell_h + gap
                rect = QRectF(pad + col * (cell_w + gap), y, cell_w, cell_h)
                self.entries.append(Entry("cell", rect, sid, section, symbols_ui.short_name(symbols.SYMBOLS[sid])))
            y += cell_h + 12
        self.content_height = y + 8
        self._finish()

    def _finish(self):
        bar = self.verticalScrollBar()
        bar.setPageStep(self.viewport().height())
        bar.setRange(0, max(0, int(self.content_height - self.viewport().height())))
        items = self.items()
        if self.focus_index >= len(self.entries) or (self.focus_index >= 0 and self.entries[self.focus_index].kind == "head"):
            self.focus_index = items[0] if items else -1
        self.viewport().update()

    def items(self) -> list[int]:
        return [i for i, e in enumerate(self.entries) if e.kind in ("cell", "chip", "row")]

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.relayout()

    def show_results(self, ids):
        self.mode = "results"
        self.results = list(ids)
        self.focus_index = 0 if ids else -1
        self.verticalScrollBar().setValue(0)
        self.relayout()
        self._announce()

    def show_grid(self):
        self.mode = "grid"
        self.results = []
        self.relayout()

    def scroll_to_section(self, section):
        if section in self.sections:
            self.verticalScrollBar().setValue(int(self.entries[self.sections[section]].rect.top()) - 4)

    def _scrolled(self, value):
        if self.mode != "grid":
            return
        current = None
        for section, index in self.sections.items():
            if self.entries[index].rect.top() <= value + 30:
                current = section
        if current:
            self.sectionChanged.emit(current)
        self.viewport().update()

    # ---- painting
    def paintEvent(self, event):
        pal = self.palette
        c = pal.colors
        p = QPainter(self.viewport())
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.viewport().rect(), c.panel)
        offset = self.verticalScrollBar().value()
        p.translate(0, -offset)
        top, bottom = offset - 70, offset + self.viewport().height() + 70
        deadline = time.perf_counter() + STATE_BUDGET
        pending = False
        for index, entry in enumerate(self.entries):
            if entry.rect.bottom() < top or entry.rect.top() > bottom:
                continue
            if entry.kind == "head":
                self._paint_head(p, entry, c)
                continue
            if entry.kind == "empty":
                p.setPen(c.text3)
                p.setFont(self.font())
                p.drawText(entry.rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, entry.text)
                continue
            state = pal.cached_state(entry.sid)
            if state is None:
                if time.perf_counter() < deadline:
                    state = pal.state_of(entry.sid)
                else:
                    pending = True
            self._paint_item(p, index, entry, state, c)
        if not self.entries and self.mode == "results":
            p.setPen(c.text2)
            p.drawText(QRectF(12, 12, self.viewport().width() - 24, 80), Qt.TextFlag.TextWordWrap,
                       pal.empty_text())
        p.end()
        if pending:
            self.retry.start()

    def _paint_head(self, p, entry, c):
        font = self.font()
        font.setPixelSize(12)
        font.setWeight(font.Weight.DemiBold)
        p.setFont(font)
        p.setPen(c.text2)
        p.drawText(entry.rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, entry.text)

    def _paint_item(self, p, index, entry, state, c):
        rect = entry.rect
        hovered, focused = index == self.hover_index, index == self.focus_index and self.hasFocus()
        stamped = self.palette.continuous == entry.sid
        disabled = state is not None and not state.available
        present = state is not None and state.available and state.present > 0
        radius = 6 if entry.kind != "chip" else 14
        background = c.raised
        border = QPen(c.border, 1)
        if present and state.all_present:
            background = c.accentsoft
            border = QPen(c.accent, 1)
        if disabled:
            background = Qt.GlobalColor.transparent
            border = QPen(c.control, 1, Qt.PenStyle.DashLine)
        if hovered or focused or stamped:
            border = QPen(c.accent, 1.5)
        if index == self.focus_index and self.mode == "results" and not disabled:
            background = c.accentsoft
        p.setBrush(background)
        p.setPen(border)
        p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
        if focused or stamped:
            halo = QPen(c.halo, 2)
            p.setPen(halo)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(rect.adjusted(-2, -2, 2, 2), radius + 2, radius + 2)
        word = symbols_ui.text_word(entry.sid)
        symbol = symbols.SYMBOLS[base_sid(entry.sid)]
        ink, faint = QColor(c.ink), QColor(c.faint)
        if disabled:
            ink.setAlphaF(DISABLED_INK_ALPHA)
            faint.setAlphaF(0.18)
        p.save()
        name_color = getattr(c, DISABLED_NAME_ROLE) if disabled else c.text
        font = self.font()
        if entry.kind in ("cell", "chip"):
            if entry.kind == "cell" and rect.height() > 50:
                glyphs.symbol_preview(p, symbol, QRectF(rect.left(), rect.top() + 2, rect.width(), rect.height() - 22),
                                      ink, faint, 0.82, self.palette.variant)
            else:
                icon_w = 30 if entry.kind == "cell" else 26
                glyphs.symbol_preview(p, symbol, QRectF(rect.left() + 2, rect.top(), icon_w, rect.height()),
                                      ink, faint, 0.55 if entry.kind == "chip" else 0.62, self.palette.variant, True)
            text_rect, align = self.label_rect(entry)
            size, wrap = self.label_fit(entry)
            font.setPixelSize(size)
            p.setFont(font)
            p.setPen(name_color)
            flags = align | (Qt.TextFlag.TextWordWrap if wrap else Qt.AlignmentFlag(0))
            text = entry.text if size >= LABEL_MIN_PX or wrap else \
                QFontMetricsF(font).elidedText(entry.text, Qt.TextElideMode.ElideRight, text_rect.width())
            p.drawText(text_rect, flags, text)
            if entry.key:
                small = self.font()
                small.setPixelSize(11)
                p.setFont(small)
                p.setPen(c.text3)
                p.drawText(QRectF(rect.right() - 16, rect.top(), 12, rect.height()), Qt.AlignmentFlag.AlignCenter, entry.key)
        else:   # result row
            box = QRectF(rect.left() + 8, rect.top() + 4, 40, 40)
            p.setPen(QPen(c.border, 1))
            p.setBrush(c.paper)
            p.drawRoundedRect(box, 5, 5)
            glyphs.symbol_preview(p, symbol, box, ink, faint, 0.62, self.palette.variant, True)
            font.setPixelSize(14)
            p.setFont(font)
            name = tr("加上文字「{word}」").format(word=word) if word else symbol.display_name
            self._paint_highlighted(p, QRectF(rect.left() + 58, rect.top() + 4, rect.width() - 64, 22),
                                    name, self.palette.query, name_color, c.accenttext, font)
            font.setPixelSize(12)
            p.setFont(font)
            second = (("⚠ " + state.reason) if state is not None and not state.available else
                      tr("印在音符上方；加在曲首第一個音上時，印在調號速度那一行")) if word else \
                self.palette.result_line(symbol, state)
            p.setPen(c.warn if disabled else c.accenttext if present else c.text2)
            area = QRectF(rect.left() + 58, rect.top() + 25, rect.width() - 64, 20)
            p.drawText(area, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                       QFontMetricsF(font).elidedText(second, Qt.TextElideMode.ElideRight, area.width()))
        if present and entry.kind != "row":
            badge = "✓" if state.all_present else f"{state.present}/{state.total}"
            small = self.font()
            small.setPixelSize(11)
            small.setWeight(small.Weight.Bold)
            p.setFont(small)
            p.setPen(c.accenttext)
            p.drawText(QRectF(rect.right() - 30, rect.top() + 2, 27, 14),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop, badge)
        if symbol.kind == "bar" and symbol.print_only and entry.kind == "cell" and rect.height() > 50:
            small = self.font()
            small.setPixelSize(11)
            p.setFont(small)
            p.setPen(c.text2)
            p.drawText(QRectF(rect.left() + 4, rect.top() + 2, 30, 14), Qt.AlignmentFlag.AlignLeft, tr("只印"))
        p.restore()

    def label_rect(self, entry):
        """Where a cell's / chip's name is drawn, and its alignment."""
        rect = entry.rect
        if entry.kind == "cell" and rect.height() > 50:
            return QRectF(rect.left() + 2, rect.bottom() - 21, rect.width() - 4, 20), Qt.AlignmentFlag.AlignCenter
        icon_w = 30 if entry.kind == "cell" else 26
        right = rect.right() - (16 if entry.key else 6)
        return (QRectF(rect.left() + icon_w + 4, rect.top() + 1, right - rect.left() - icon_w - 4, rect.height() - 2),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)

    def label_fit(self, entry) -> tuple[int, bool]:
        """(pixel size, wrap) so the whole name shows: never clipped with … at the default size
        (design: names are not elided); smaller type first, then two lines where there is room."""
        text_rect, _ = self.label_rect(entry)
        base = 13 if entry.kind == "chip" else 12
        font = self.font()
        for size in range(base, LABEL_MIN_PX - 1, -1):
            font.setPixelSize(size)
            metrics = QFontMetricsF(font)
            if metrics.horizontalAdvance(entry.text) <= text_rect.width():
                return size, False
            if metrics.height() * 2 <= text_rect.height() + 1 and metrics.boundingRect(
                    text_rect, int(Qt.TextFlag.TextWordWrap), entry.text).height() <= text_rect.height() + 1:
                return size, True
        return LABEL_MIN_PX - 1, False

    @staticmethod
    def _paint_highlighted(p, rect, text, query, color, accent, font):
        """Draw ``text`` with the part matching ``query`` in bold accent (search rows)."""
        from .symbols_ui import fold
        folded, needle = fold(text), fold(query).strip()
        start = folded.find(needle) if needle else -1
        metrics = QFontMetricsF(font)
        x = rect.left()
        pieces = [(text, False)] if start < 0 else [(text[:start], False), (text[start:start + len(needle)], True),
                                                    (text[start + len(needle):], False)]
        for piece, strong in pieces:
            if not piece:
                continue
            f = type(font)(font)
            f.setWeight(f.Weight.Bold if strong else f.Weight.Medium)
            p.setFont(f)
            p.setPen(accent if strong else color)
            width = QFontMetricsF(f).horizontalAdvance(piece)
            p.drawText(QRectF(x, rect.top(), rect.right() - x, rect.height()),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, piece)
            x += width
        del metrics

    # ---- interaction
    def entry_at(self, point: QPoint) -> int:
        pos = QPointF(point.x(), point.y() + self.verticalScrollBar().value())
        for index, entry in enumerate(self.entries):
            if entry.kind in ("cell", "chip", "row") and entry.rect.contains(pos):
                return index
        return -1

    def mouseMoveEvent(self, event):
        index = self.entry_at(event.position().toPoint())
        if index != self.hover_index:
            self.hover_index = index
            self.hovered.emit(self.entries[index].sid if index >= 0 else "")
            self.viewport().update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        if self.hover_index >= 0:
            self.hover_index = -1
            self.hovered.emit("")
            self.viewport().update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        index = self.entry_at(event.position().toPoint())
        if index < 0:
            return super().mousePressEvent(event)
        self.focus_index = index
        sid = self.entries[index].sid
        if event.button() == Qt.MouseButton.RightButton:
            if not symbols_ui.text_word(sid):
                self.contextRequested.emit(sid, event.globalPosition().toPoint())
            return
        if event.button() == Qt.MouseButton.LeftButton:
            how = "continuous" if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else "click"
            self.activated.emit(sid, how)
        self.viewport().update()

    def mouseDoubleClickEvent(self, event):
        index = self.entry_at(event.position().toPoint())
        if index >= 0 and event.button() == Qt.MouseButton.LeftButton:
            self.activated.emit(self.entries[index].sid, "continuous")

    def viewportEvent(self, event):
        if event.type() == QEvent.Type.ToolTip:
            index = self.entry_at(event.pos())
            if index >= 0:
                QToolTip.showText(event.globalPos(), self.palette.tooltip(self.entries[index].sid), self)
            else:
                QToolTip.hideText()
            return True
        return super().viewportEvent(event)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        items = self.items()
        if self.focus_index not in items and items:
            self.focus_index = items[0]
        self._announce()
        self.viewport().update()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.viewport().update()

    def move_focus(self, key) -> bool:
        items = self.items()
        if not items:
            return False
        if self.focus_index not in items:
            self.focus_index = items[0]
        current = self.entries[self.focus_index].rect
        if key in (Qt.Key.Key_Home, Qt.Key.Key_End):
            self.focus_index = items[0] if key == Qt.Key.Key_Home else items[-1]
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            position = items.index(self.focus_index) + (1 if key == Qt.Key.Key_Right else -1)
            self.focus_index = items[max(0, min(len(items) - 1, position))]
        else:
            down = key == Qt.Key.Key_Down
            candidates = [i for i in items if (self.entries[i].rect.top() > current.top() + 2 if down
                                               else self.entries[i].rect.top() < current.top() - 2)]
            if not candidates:
                return False
            row_y = min(candidates, key=lambda i: abs(self.entries[i].rect.top() - current.top()))
            row_top = self.entries[row_y].rect.top()
            same_row = [i for i in candidates if abs(self.entries[i].rect.top() - row_top) < 2]
            self.focus_index = min(same_row, key=lambda i: abs(self.entries[i].rect.center().x() - current.center().x()))
        self.ensure_visible(self.focus_index)
        self._announce()
        self.viewport().update()
        return True

    def ensure_visible(self, index):
        rect = self.entries[index].rect
        bar = self.verticalScrollBar()
        if rect.top() < bar.value():
            bar.setValue(int(rect.top()) - 30)
        elif rect.bottom() > bar.value() + self.viewport().height():
            bar.setValue(int(rect.bottom() - self.viewport().height()) + 6)

    def focused_sid(self) -> str:
        if 0 <= self.focus_index < len(self.entries) and self.entries[self.focus_index].kind in ("cell", "chip", "row"):
            return self.entries[self.focus_index].sid
        return ""

    def _announce(self):
        sid = self.focused_sid()
        self.focusChanged.emit(sid)
        if sid:
            self.setAccessibleDescription(self.palette.accessible_text(sid))

    def keyPressEvent(self, event):
        key = event.key()
        if key in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Home, Qt.Key.Key_End):
            if not self.move_focus(key) and key == Qt.Key.Key_Up:
                self.palette.search.setFocus()
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            sid = self.focused_sid()
            if sid:
                mods = event.modifiers()
                how = ("continuous" if mods & Qt.KeyboardModifier.ShiftModifier else
                       "stay" if mods & Qt.KeyboardModifier.ControlModifier or key == Qt.Key.Key_Space else "click")
                self.activated.emit(sid, how)
            return
        if key == Qt.Key.Key_Escape:
            self.escapePressed.emit()
            return
        if event.text() and event.text().isprintable() and not event.modifiers() & (
                Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier):
            self.palette.search.setFocus()
            self.palette.search.insert(event.text())
            return
        super().keyPressEvent(event)


class SearchField(QLineEdit):
    """The palette search: ↓ moves into the results, Enter applies the highlighted one."""

    def __init__(self, palette):
        super().__init__()
        self.palette = palette
        self.setObjectName("search")
        self.setClearButtonEnabled(True)

    def keyPressEvent(self, event):
        grid = self.palette.grid
        key = event.key()
        if key in (Qt.Key.Key_Down, Qt.Key.Key_Up) and grid.items():
            if key == Qt.Key.Key_Down:
                grid.setFocus()
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            sid = grid.focused_sid() if grid.mode == "results" else ""
            if sid:
                mods = event.modifiers()
                how = ("continuous" if mods & Qt.KeyboardModifier.ShiftModifier else
                       "stay" if mods & Qt.KeyboardModifier.ControlModifier else "click")
                grid.activated.emit(sid, how)
            return
        if key == Qt.Key.Key_Escape:
            if self.text():
                self.clear()
            else:
                self.palette.backToScore.emit()
            return
        super().keyPressEvent(event)


class SymbolPalette(QWidget):
    """Search + instrument chips + categories + grid + detail strip."""

    applyRequested = Signal(str, str)     # symbol id, how (click | stay | continuous)
    backToScore = Signal()
    hoverChanged = Signal(str)
    continuousChanged = Signal(str)
    showHiddenChanged = Signal(bool)

    def __init__(self, settings, colors, state, parent=None):
        super().__init__(parent)
        self.setObjectName("palette")
        self.settings = settings
        self.colors = colors
        self.state = state                  # callable(sid) -> SymbolState
        self.variant = "tc"
        self.query = ""
        self.continuous = ""
        self.last_applied = ""
        self.detail_sid = ""
        self.show_hidden = False
        self.where = None                   # callable(sid) -> "會加在：…"
        self._states: dict[str, object] = {}
        saved = str(settings.value("palette/instrument", "erhu"))
        self.instrument = saved if saved in INSTRUMENT_NAMES else "erhu"
        self.favorites = self._load_list("palette/favorites", list(symbols_ui.DEFAULT_FAVORITES))
        self.recents = self._load_list("palette/recents", [])
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        top = QVBoxLayout()
        top.setContentsMargins(12, 10, 12, 6)
        top.setSpacing(8)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.search = SearchField(self)
        self.search.setPlaceholderText(tr("搜尋記號：滑、hua、slide"))
        self.search.setAccessibleName(tr("搜尋記號"))
        self.search.setToolTip(tr("/ 或 Ctrl+F 搜尋；Enter 套用並回到譜面；Esc 清除"))
        self.search.addAction(icons.icon("search", "text2"), QLineEdit.ActionPosition.LeadingPosition)
        self.search.textChanged.connect(self.set_query)
        row.addWidget(self.search, 1)
        self.result_count = label("", "result_count", role="hint")
        row.addWidget(self.result_count)
        self.stamp_button = tool("", "stamp", tr("連續套用（Shift＋點格子）：點音符就加上，Esc 結束"), "ghost",
                                 name="stamp_button", checkable=True)
        self.stamp_button.setFixedSize(30, 30)
        self.stamp_button.toggled.connect(self._stamp_toggled)
        row.addWidget(self.stamp_button)
        top.addLayout(row)
        chips = QHBoxLayout()
        chips.setSpacing(4)
        self.instrument_group = QButtonGroup(self)
        self.instrument_buttons = {}
        for code in symbols_ui.INSTRUMENTS:
            button = tool(tr(INSTRUMENT_NAMES[code]), checkable=True)
            button.setProperty("chip", True)
            button.setToolTip(tr("{name}：通用記號加上{name}的記號").format(name=tr(INSTRUMENT_NAMES[code]))
                              if code != "common" else tr("只顯示各樂器共用的記號"))
            button.clicked.connect(lambda checked=False, c=code: self.set_instrument(c))
            self.instrument_group.addButton(button)
            self.instrument_buttons[code] = button
            chips.addWidget(button)
        self.more_button = tool(tr("更多…"))
        self.more_button.setProperty("chip", True)
        self.more_button.setCheckable(True)
        self.more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.more_menu = QMenu(self.more_button)
        for code in symbols_ui.MORE_INSTRUMENTS:
            self.more_menu.addAction(tr(INSTRUMENT_NAMES[code]), lambda c=code: self.set_instrument(c))
        self.more_button.setMenu(self.more_menu)
        self.instrument_group.addButton(self.more_button)
        chips.addWidget(self.more_button)
        chips.addStretch()
        top.addLayout(chips)
        root.addLayout(top)
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self.categories = QListWidget()
        self.categories.setObjectName("categories")
        self.categories.setFixedWidth(66)
        self.categories.setAccessibleName(tr("記號分類"))
        self.categories.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.categories.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        for section in SECTION_ORDER:
            item = QListWidgetItem(tr(SECTION_SHORT[section]))
            item.setData(Qt.ItemDataRole.UserRole, section)
            item.setToolTip(self.section_full(section))
            self.categories.addItem(item)
        self.categories.itemClicked.connect(self._category_clicked)
        body.addWidget(self.categories)
        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        self.grid = SymbolGrid(self)
        self.grid.activated.connect(self._activated)
        self.grid.hovered.connect(self._hovered)
        self.grid.focusChanged.connect(self._focus_changed)
        self.grid.sectionChanged.connect(self._section_changed)
        self.grid.escapePressed.connect(self._escape)
        self.grid.contextRequested.connect(self._context_menu)
        column.addWidget(self.grid, 1)
        self.hidden_row = QWidget()
        hidden = QVBoxLayout(self.hidden_row)
        hidden.setContentsMargins(12, 6, 12, 8)
        hidden.setSpacing(2)
        self.hidden_label = label("", "hidden_label", True, "hint")
        hidden.addWidget(self.hidden_label)
        self.show_hidden_button = QPushButton(tr("暫時顯示"))
        self.show_hidden_button.setProperty("kind", "link")
        self.show_hidden_button.setObjectName("show_hidden_button")
        self.show_hidden_button.clicked.connect(lambda: self.set_show_hidden(not self.show_hidden))
        hidden.addWidget(self.show_hidden_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.hidden_row.hide()
        column.addWidget(self.hidden_row)
        body.addLayout(column, 1)
        root.addLayout(body, 1)
        self.detail = DetailStrip(self)
        root.addWidget(self.detail)
        self.set_instrument(self.instrument, save=False)

    def set_narrow(self, narrow: bool):
        """≤ 1180 px: 4 instrument chips, 通用 moves into 更多 (design §1.3)."""
        self.instrument_buttons["common"].setVisible(not narrow)
        self.more_menu.clear()
        for code in (("common",) if narrow else ()) + symbols_ui.MORE_INSTRUMENTS:
            self.more_menu.addAction(tr(INSTRUMENT_NAMES[code]), lambda c=code: self.set_instrument(c))
        self.narrow = narrow
        self.search.setPlaceholderText(tr("搜尋記號") if narrow else tr("搜尋記號：滑、hua、slide"))
        self.set_instrument(self.instrument, save=False)

    # ---- data
    def _load_list(self, key, default):
        raw = self.settings.value(key, None)
        if raw is None:
            return list(default)
        values = [raw] if isinstance(raw, str) else list(raw)
        return [v for v in values if v in symbols.SYMBOLS and v not in symbols_ui.NOT_IN_PALETTE]

    def _save_lists(self):
        self.settings.setValue("palette/favorites", self.favorites or [""])
        self.settings.setValue("palette/recents", self.recents or [""])

    def visible_symbols(self):
        return symbols_ui.palette_symbols(include_hidden=self.show_hidden)

    def section_ids(self, section) -> list[str]:
        if section == FAVORITES:
            return [s for s in self.favorites if self._visible(s)][:symbols_ui.MAX_FAVORITES]
        if section == RECENTS:
            return [s for s in self.recents if self._visible(s)][:symbols_ui.MAX_RECENTS]
        return [s.id for s in self.visible_symbols()
                if s.category == section and symbols_ui.group_matches(s, self.instrument)]

    def _visible(self, sid) -> bool:
        symbol = symbols.SYMBOLS.get(sid)
        return symbol is not None and (self.show_hidden or not symbol.hidden)

    def section_full(self, section) -> str:
        if section == FAVORITES:
            return tr("常用")
        if section == RECENTS:
            return tr("最近")
        return tr(symbols.CATEGORY_NAMES[section])

    def section_title(self, section, count) -> str:
        if section == FAVORITES:
            return tr("常用 · Alt+1…9")
        if section == RECENTS:
            return tr("最近")
        return "{} · {} {}".format(self.section_full(section), tr(INSTRUMENT_NAMES[self.instrument]), count)

    def all_ids(self) -> list[str]:
        """Every symbol id the grid currently shows (categories only, no 常用/最近 duplicates)."""
        return [s.id for s in self.visible_symbols() if symbols_ui.group_matches(s, self.instrument)]

    # ---- state cache (invalidated whenever the selection or the score changes)
    def invalidate(self):
        self._states.clear()
        self.grid.viewport().update()
        self.detail.refresh()

    def cached_state(self, sid):
        return self._states.get(base_sid(sid))

    def state_of(self, sid):
        sid = base_sid(sid)
        if sid not in self._states:
            self._states[sid] = self.state(sid)
        return self._states[sid]

    # ---- filters and search
    def set_instrument(self, code, save=True):
        self.instrument = code
        if save:
            self.settings.setValue("palette/instrument", code)
        for value, button in self.instrument_buttons.items():
            button.setChecked(value == code)
        more = code in symbols_ui.MORE_INSTRUMENTS or (code == "common" and getattr(self, "narrow", False))
        self.more_button.setChecked(more)
        self.more_button.setText(tr(INSTRUMENT_NAMES[code]) if more else tr("更多…"))
        self.grid.relayout()
        self._update_categories()

    def set_query(self, text):
        self.query = text
        ids = [s.id for s in symbols_ui.search(text, instrument=self.instrument if self.instrument != "all" else "erhu",
                                               include_hidden=self.show_hidden)] if text.strip() else []
        # Tempo/expression words, tunings and bow sections are text: offer 「加上文字「…」」
        # (first when the query is exactly such a word), and any query that finds nothing.
        words = symbols_ui.text_suggestions(text, found=bool(ids))
        rows = [symbols_ui.TEXT_PREFIX + w for w in words]
        exact = bool(words) and words[0] == text.strip()
        ids = rows + ids if exact or not ids else ids + rows
        if text.strip():
            self.grid.show_results(ids)
            self.result_count.setText(tr("{count} 個結果").format(count=len(ids)))
            hidden = [] if self.show_hidden else symbols_ui.hidden_matches(text, self.instrument)
            if hidden:
                names = "、".join(s.display_name for s in hidden[:5])
                self.hidden_label.setText(tr("另有 {count} 個「待老師確認」的記號沒有列出：{names}。").format(
                    count=len(hidden), names=names))
            self.hidden_row.setVisible(bool(hidden) or self.show_hidden)
            if self.show_hidden:
                self.hidden_label.setText(tr("正在顯示「待老師確認」的記號；印法尚未經過核對。"))
            self.show_hidden_button.setText(tr("恢復隱藏") if self.show_hidden else tr("暫時顯示"))
            self.categories.setEnabled(False)
        else:
            self.grid.show_grid()
            self.result_count.setText("")
            self.hidden_row.setVisible(self.show_hidden)
            self.hidden_label.setText(tr("正在顯示「待老師確認」的記號；印法尚未經過核對。"))
            self.show_hidden_button.setText(tr("恢復隱藏"))
            self.categories.setEnabled(True)
        self.detail.refresh()

    def empty_text(self) -> str:
        return tr("找不到「{query}」。可以試試：滑音、指法、hua。按 Esc 清除。").format(query=self.query.strip())

    def set_show_hidden(self, value: bool):
        self.show_hidden = bool(value)
        self.showHiddenChanged.emit(self.show_hidden)
        self.set_query(self.search.text())

    def _update_categories(self):
        for row in range(self.categories.count()):
            item = self.categories.item(row)
            section = item.data(Qt.ItemDataRole.UserRole)
            item.setHidden(section not in (FAVORITES, RECENTS) and not self.section_ids(section))

    def _category_clicked(self, item):
        self.grid.scroll_to_section(item.data(Qt.ItemDataRole.UserRole))

    def _section_changed(self, section):
        for row in range(self.categories.count()):
            item = self.categories.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == section:
                self.categories.blockSignals(True)
                self.categories.setCurrentItem(item)
                self.categories.blockSignals(False)
                break

    # ---- actions
    def _activated(self, sid, how):
        self.applyRequested.emit(sid, how)

    def record_use(self, sid):
        self.last_applied = sid
        self.recents = [sid] + [s for s in self.recents if s != sid]
        del self.recents[symbols_ui.MAX_RECENTS:]
        self._save_lists()
        self.grid.relayout()

    def favorites_full(self) -> bool:
        return len(self.favorites) >= symbols_ui.MAX_FAVORITES

    def toggle_favorite(self, sid, replace=None):
        """Star / unstar. 「常用」 holds 9 (Alt+1…9): when it is full, ``replace`` names the one
        to swap out; without it the user is asked which one (never a silent no-op)."""
        if sid in self.favorites:
            self.favorites.remove(sid)
        elif len(self.favorites) < symbols_ui.MAX_FAVORITES:
            self.favorites.append(sid)
        elif replace in self.favorites:
            self.favorites[self.favorites.index(replace)] = sid
        else:
            self.choose_replacement(sid)
            return
        self._save_lists()
        self.grid.relayout()
        self.detail.refresh()

    def favorite_key(self, sid) -> str:
        visible = self.section_ids(FAVORITES)
        return str(visible.index(sid) + 1) if sid in visible else ""

    def _replacement_menu(self, menu, sid):
        menu.setToolTipsVisible(True)
        title = menu.addAction(tr("常用已滿（最多 {count} 個，對應 Alt+1…9）。選一個換成「{name}」：").format(
            count=symbols_ui.MAX_FAVORITES, name=symbols.SYMBOLS[sid].display_name))
        title.setEnabled(False)
        for index, old in enumerate(self.favorites):
            menu.addAction(tr("取代 {n}. {name}").format(n=index + 1, name=symbols.SYMBOLS[old].display_name),
                           lambda old=old: self.toggle_favorite(sid, replace=old))

    def choose_replacement(self, sid, position=None):
        menu = QMenu(self)
        self._replacement_menu(menu, sid)
        menu.exec(position or QCursor.pos())

    def _context_menu(self, sid, position):
        menu = QMenu(self)
        if sid in self.favorites:
            menu.addAction(tr("移出常用"), lambda: self.toggle_favorite(sid))
        elif self.favorites_full():
            self._replacement_menu(menu.addMenu(tr("加到常用（已滿，取代一個）")), sid)
        else:
            menu.addAction(tr("加到常用"), lambda: self.toggle_favorite(sid))
        if sid in self.favorites:
            index = self.favorites.index(sid)
            up = menu.addAction(tr("往前移"), lambda: self._move_favorite(sid, -1))
            up.setEnabled(index > 0)
            down = menu.addAction(tr("往後移"), lambda: self._move_favorite(sid, 1))
            down.setEnabled(index < len(self.favorites) - 1)
        menu.addSeparator()
        menu.addAction(tr("連續套用"), lambda: self.applyRequested.emit(sid, "continuous"))
        menu.exec(position)

    def _move_favorite(self, sid, step):
        index = self.favorites.index(sid)
        target = max(0, min(len(self.favorites) - 1, index + step))
        self.favorites.insert(target, self.favorites.pop(index))
        self._save_lists()
        self.grid.relayout()

    def set_continuous(self, sid: str):
        self.continuous = sid
        self.stamp_button.blockSignals(True)
        self.stamp_button.setChecked(bool(sid))
        self.stamp_button.blockSignals(False)
        self.grid.viewport().update()
        self.detail.refresh()

    def _stamp_toggled(self, checked):
        sid = self.grid.focused_sid() or self.detail_sid or self.last_applied
        if checked and sid and not symbols_ui.text_word(sid):
            self.applyRequested.emit(sid, "continuous")
        elif not checked:
            self.continuousChanged.emit("")
        else:
            self.stamp_button.setChecked(False)

    def _hovered(self, sid):
        self.hoverChanged.emit(base_sid(sid))
        self.detail.refresh()

    def _focus_changed(self, sid):
        self.detail.refresh()

    def _escape(self):
        if self.continuous:
            self.continuousChanged.emit("")
        elif self.search.text():
            self.search.clear()
            self.search.setFocus()
        else:
            self.backToScore.emit()

    def focus_search(self):
        self.search.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.search.selectAll()

    def current_detail(self) -> str:
        hover = self.grid.entries[self.grid.hover_index].sid if 0 <= self.grid.hover_index < len(self.grid.entries) else ""
        return base_sid(hover or self.grid.focused_sid() or self.continuous or self.last_applied)

    # ---- texts
    def tooltip(self, sid) -> str:
        if word := symbols_ui.text_word(sid):
            return tr("加上文字「{word}」").format(word=word)
        symbol = symbols.SYMBOLS[sid]
        state = self.state_of(sid)
        lines = [symbol.display_name, symbols_ui.usage(sid)]
        if not state.available:
            lines.append("⚠ " + state.reason)
        return "\n".join(lines)

    def accessible_text(self, sid) -> str:
        if word := symbols_ui.text_word(sid):
            return tr("加上文字「{word}」").format(word=word)
        symbol = symbols.SYMBOLS[sid]
        state = self.state_of(sid)
        category = tr(CATEGORY_SHORT.get(symbol.category, symbol.category))
        if state.available:
            return tr("{name}，{category}，可用").format(name=symbol.display_name, category=category)
        return tr("{name}，停用：{reason}").format(name=symbol.display_name, reason=state.reason)

    def result_line(self, symbol, state) -> str:
        where = "{} · {}".format(tr(CATEGORY_SHORT.get(symbol.category, symbol.category)),
                              tr(symbols.GROUP_NAMES.get(symbol.group, symbol.group)))
        if state is None:
            return where
        if not state.available:
            return "⚠ " + state.reason
        if state.removes:
            return where + "　" + tr("✓ 已加上（再按一次移除）")
        if state.replaces:
            return where + "　" + tr("會取代{names}").format(names="、".join(state.replaces))
        return where + "　" + symbols_ui.usage(symbol.id)

    def retranslate(self):
        """Painted texts follow tr() on repaint; rebuild the few cached strings."""
        for row in range(self.categories.count()):
            item = self.categories.item(row)
            section = item.data(Qt.ItemDataRole.UserRole)
            item.setText(tr(SECTION_SHORT[section]))
            item.setToolTip(self.section_full(section))
        for code, button in self.instrument_buttons.items():
            button.setText(tr(INSTRUMENT_NAMES[code]))
        self.set_instrument(self.instrument, save=False)
        self.invalidate()
        self.set_query(self.search.text())

    def set_colors(self, colors):
        self.colors = colors
        self.grid.viewport().update()
        self.detail.refresh()


class DetailStrip(QFrame):
    """Bottom strip: 印出來長這樣 preview, name, one-line usage, reason, effect and favourite."""

    def __init__(self, palette: SymbolPalette):
        super().__init__()
        self.palette = palette
        self.setObjectName("detail")
        self.setMinimumHeight(104)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(12)
        left = QVBoxLayout()
        left.setSpacing(4)
        self.preview = QLabel()
        self.preview.setObjectName("previewbox")
        self.preview.setFixedSize(86, 64)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        left.addWidget(self.preview)
        self.caption = label(tr("印出來長這樣"), "preview_caption", role="hint")
        self.caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        left.addWidget(self.caption)
        left.addStretch()
        layout.addLayout(left)
        right = QVBoxLayout()
        right.setSpacing(4)
        self.name = label("", "detail_name", role="title")
        right.addWidget(self.name)
        self.usage = label("", "detail_usage", True)
        right.addWidget(self.usage)
        self.reason = label("", "detail_reason", True, "warn")
        self.reason.hide()
        right.addWidget(self.reason)
        bottom = QHBoxLayout()
        bottom.setSpacing(6)
        self.mute = QLabel()
        self.mute.setObjectName("detail_mute")
        self.mute.hide()
        bottom.addWidget(self.mute, 0, Qt.AlignmentFlag.AlignTop)
        self.effect = label("", "detail_effect", True, "hint")
        self.effect.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        bottom.addWidget(self.effect, 1)
        self.favorite = tool("", "star", tr("加到常用"), "ghost", name="favorite_button")
        self.favorite.setFixedSize(26, 26)
        self.favorite.clicked.connect(lambda: self.palette.toggle_favorite(self.palette.detail_sid)
                                      if self.palette.detail_sid else None)
        bottom.addWidget(self.favorite)
        right.addLayout(bottom)
        right.addStretch()
        layout.addLayout(right, 1)
        self.compact = False

    def set_compact(self, compact: bool):
        """≤ 1180 px: no preview picture (design §1.3)."""
        self.compact = compact
        self.preview.setVisible(not compact)
        self.caption.setVisible(not compact)
        self.setMinimumHeight(72 if compact else 104)

    def refresh(self):
        pal = self.palette
        sid = pal.current_detail()
        pal.detail_sid = sid
        if not sid:
            self.name.setText(tr("記號說明"))
            self.usage.setText(tr("滑鼠停在記號上會顯示用法；先在譜上點一個音符，再點記號加上。"))
            self.reason.hide()
            self.effect.setText("")
            self.preview.clear()
            self.mute.hide()
            self.favorite.setEnabled(False)
            return
        symbol = symbols.SYMBOLS[sid]
        state = pal.state_of(sid)
        self.name.setText(symbol.display_name)
        self.usage.setText(symbols_ui.usage(sid))
        self.reason.setVisible(not state.available)
        if not state.available:
            self.reason.setText(state.reason)
        effects = []
        self.mute.setVisible(symbols_ui.silent(symbol))
        if symbols_ui.silent(symbol):
            effects.append(tr("試聽不表現，只印出"))
        if state.available:
            if state.removes:
                effects.append(tr("已加上，再點一次移除"))
            elif state.replaces:
                effects.append(tr("會取代：{names}").format(names="、".join(state.replaces)))
            elif state.present:
                effects.append(tr("{total} 個中有 {present} 個已有；點一下全部加上").format(
                    total=state.total, present=state.present))
            else:
                where = pal.where(sid) if pal.where else ""
                if where:
                    effects.append(where)
        key = pal.favorite_key(sid)
        if key:
            effects.append(tr("常用 Alt+{key}").format(key=key))
        if symbol.hidden:
            effects.append(tr("待老師確認"))
        self.effect.setText("　".join(effects))
        self.effect.setToolTip("　".join(effects))
        self.mute.setPixmap(icons.pixmap("mute", 13, pal.colors.tokens["text2"]))
        self.favorite.setEnabled(True)
        starred = sid in pal.favorites
        icons.attach(self.favorite, "star-filled" if starred else "star", "accent" if starred else "text2", 16)
        self.favorite.setToolTip(tr("移出常用") if starred else tr("加到常用（已滿 9 個：點了選一個取代）")
                                 if pal.favorites_full() else tr("加到常用"))
        self._set_preview(symbol)

    def _set_preview(self, symbol):
        if self.compact:
            return
        c = self.palette.colors
        ratio = self.devicePixelRatioF()
        image = QPixmap(round(86 * ratio), round(64 * ratio))
        image.setDevicePixelRatio(ratio)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = preview_path(symbol.id)
        if path.is_file():
            source = QPixmap(str(path))
            if not source.isNull():
                # LilyPond renders black on transparent: recolour to the ink colour for dark mode.
                tinted = QPixmap(source.size())
                tinted.fill(Qt.GlobalColor.transparent)
                tp = QPainter(tinted)
                tp.drawPixmap(0, 0, source)
                tp.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
                tp.fillRect(tinted.rect(), c.ink)
                tp.end()
                size = QSize(80, 58)
                scaled = tinted.scaled(size * ratio, Qt.AspectRatioMode.KeepAspectRatio,
                                       Qt.TransformationMode.SmoothTransformation)
                scaled.setDevicePixelRatio(ratio)
                w, h = scaled.width() / ratio, scaled.height() / ratio
                painter.drawPixmap(QPointF((86 - w) / 2, (64 - h) / 2), scaled)
                painter.end()
                self.preview.setPixmap(image)
                self.preview.setToolTip(tr("LilyPond 實際排出的樣子"))
                return
        glyphs.symbol_preview(painter, symbol, QRectF(0, 4, 86, 56), QColor(c.ink), QColor(c.faint), 1.1,
                              self.palette.variant)
        painter.end()
        self.preview.setPixmap(image)
        self.preview.setToolTip(tr("譜面草稿的畫法（尚無 LilyPond 預覽圖）"))

