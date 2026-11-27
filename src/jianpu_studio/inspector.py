"""The 「選取」 inspector (wow/design.md §2.8): what the selection carries, removable in one click."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import glyphs, i18n, symbols, symbols_ui
from .i18n import N_, tr
from .widgets import label, tool

OCTAVE_NAMES = {-2: N_("倍低音區"), -1: N_("低音區"), 0: N_("中音區"), 1: N_("高音區"), 2: N_("倍高音區")}
DURATION_NAMES = {1: N_("全音符"), 2: N_("二分音符"), 4: N_("四分音符"), 8: N_("八分音符"), 16: N_("十六分音符"),
                  32: N_("三十二分音符")}  # catalog sources; show tr(value)


class FlowLayout(QLayout):
    """Wraps children like text (for the mark chips)."""

    def __init__(self, parent=None, spacing=6):
        super().__init__(parent)
        self.items = []
        self.setSpacing(spacing)
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self.items.append(item)

    def count(self):
        return len(self.items)

    def itemAt(self, index):
        return self.items[index] if 0 <= index < len(self.items) else None

    def takeAt(self, index):
        return self.items.pop(index) if 0 <= index < len(self.items) else None

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self.items:
            size = size.expandedTo(item.minimumSize())
        return size

    def _do(self, rect, test):
        x, y, line = rect.x(), rect.y(), 0
        gap = self.spacing()
        for item in self.items:
            hint = item.sizeHint()
            width = min(hint.width(), rect.width())
            if x + width > rect.right() + 1 and line:
                x, y, line = rect.x(), y + line + gap, 0
            if not test:
                item.setGeometry(QRect(QPoint(x, y), QSize(width, hint.height())))
            x += width + gap
            line = max(line, hint.height())
        return y + line - rect.y()


def mark_pixmap(symbol, colors, size=22, ratio=2.0) -> QPixmap:
    image = QPixmap(round(size * ratio), round(size * ratio))
    image.setDevicePixelRatio(ratio)
    image.fill(Qt.GlobalColor.transparent)
    p = QPainter(image)
    glyphs.symbol_preview(p, symbol, QRectF(0, 0, size, size), QColor(colors.ink), QColor(colors.faint), 0.5,
                          compact=True)
    p.end()
    return image


class MarkChip(QFrame):
    """A mark on the selection: picture, name (🔇 if print-only), × remove button."""

    def __init__(self, inspector, sid, present, total, colors):
        super().__init__()
        self.sid = sid
        symbol = symbols.SYMBOLS[sid]
        self.setObjectName("mark_chip")
        self.setProperty("partial", present < total)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 2, 2, 2)
        layout.setSpacing(4)
        picture = QLabel()
        picture.setPixmap(mark_pixmap(symbol, colors))
        layout.addWidget(picture)
        name = symbols_ui.short_name(symbol)
        if present < total:
            name += f" {present}/{total}"
        text = label(name, "chip_name")
        text.setToolTip(symbol.display_name + ("\n" + tr("只印出，試聽不會表現") if symbols_ui.silent(symbol) else ""))
        layout.addWidget(text)
        if symbols_ui.silent(symbol):
            from . import icons
            mute = QLabel()
            mute.setPixmap(icons.pixmap("mute", 12, colors.tokens["text2"]))
            mute.setToolTip(tr("只印出，試聽不會表現"))
            layout.addWidget(mute)
        self.remove = tool("", "close", tr("移除{name}（Delete）").format(name=symbol.display_name), "ghost",
                           name="chip_remove", size=14)
        self.remove.setFixedSize(24, 24)
        self.remove.clicked.connect(lambda: inspector.removeRequested.emit(sid))
        self.remove.installEventFilter(inspector)
        layout.addWidget(self.remove)
        self.setAccessibleName(symbol.display_name)


class SelectionInspector(QFrame):
    removeRequested = Signal(str)
    measureAction = Signal(str)       # before | after | duplicate | clear | delete
    graceRequested = Signal()
    clearRequested = Signal()

    def __init__(self, colors, parent=None):
        super().__init__(parent)
        self.setObjectName("inspector")
        self.colors = colors
        self.compact = False
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(4)
        head = QHBoxLayout()
        self.heading = label(tr("選取"), "inspector_heading", role="heading")
        head.addWidget(self.heading)
        head.addStretch()
        self.escape_hint = label(tr("Esc 取消選取"), "escape_hint", role="hint")
        head.addWidget(self.escape_hint)
        root.addLayout(head)
        self.title = label("", "inspector_title", True, "title")
        self.title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        root.addWidget(self.title)
        self.props = label("", "inspector_props", True, "muted")
        self.props.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        root.addWidget(self.props)
        self.chips_host = QWidget()
        self.chips = FlowLayout(self.chips_host, 6)
        root.addWidget(self.chips_host)
        self.actions = QWidget()
        actions = QHBoxLayout(self.actions)
        actions.setContentsMargins(0, 4, 0, 0)
        actions.setSpacing(4)
        self.measure_buttons = {}
        for key, text, tip in (("before", N_("前插"), N_("在這個小節前面插入一個空白小節（Insert）")),
                               ("after", N_("後插"), N_("在這個小節後面插入一個空白小節")),
                               ("duplicate", N_("重複"), N_("複製選取的小節，插在後面（Ctrl+D）")),
                               ("clear", N_("清空"), N_("改成休止符，保留拍數")),
                               ("delete", N_("刪除"), N_("移除小節，後面的音樂往前移（Ctrl+Delete）"))):
            button = tool(tr(text), tip=tr(tip), name=f"inspector_{key}")
            button.clicked.connect(lambda checked=False, k=key: self.measureAction.emit(k))
            actions.addWidget(button)
            self.measure_buttons[key] = button
        actions.addStretch()
        root.addWidget(self.actions)
        self.grace_button = tool(tr("＋倚音"), tip=tr("設定前倚音或後倚音"), name="grace_button")
        self.grace_button.clicked.connect(self.graceRequested.emit)
        root.addWidget(self.grace_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.chip_widgets: list[MarkChip] = []

    def eventFilter(self, watched, event):
        if event.type() == event.Type.KeyPress and event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            for chip in self.chip_widgets:
                if chip.remove is watched:
                    self.removeRequested.emit(chip.sid)
                    return True
        return super().eventFilter(watched, event)

    def set_compact(self, compact):
        self.compact = compact
        self.props.setVisible(not compact)
        self.escape_hint.setVisible(not compact)

    def show_content(self, title, props, marks, *, measure=False, grace=False, empty_hint=""):
        """``marks``: [(sid, present, total)]."""
        self.title.setText(title)
        self.props.setText(props if not empty_hint else empty_hint)
        self.props.setVisible(bool(props or empty_hint) and not self.compact)
        # Chip names come from the symbol registry, not the catalog: rebuild them when the
        # language (or the theme) changes, not only when the marks do.
        key = (tuple(marks), self.colors.tokens.get("ink"), i18n.current_language())
        if key != getattr(self, "_chip_key", None):
            self._chip_key = key
            for chip in self.chip_widgets:
                self.chips.removeWidget(chip)
                chip.hide()
                chip.setParent(None)
                chip.deleteLater()
            self.chip_widgets = []
            for sid, present, total in marks:
                chip = MarkChip(self, sid, present, total, self.colors)
                self.chips.addWidget(chip)
                self.chip_widgets.append(chip)
        self.chips_host.setVisible(bool(self.chip_widgets))
        self.chips_host.updateGeometry()
        self.actions.setVisible(measure)
        self.grace_button.setVisible(grace)
        self.escape_hint.setVisible(bool(marks or measure or grace) and not self.compact)

    @staticmethod
    def note_props(note) -> str:
        if note.degree == 0:
            return tr("休止符 · {duration}").format(duration=tr(DURATION_NAMES[note.duration]) + ("·" if note.dotted else ""))
        accidental = {"#": "♯", "b": "♭", "n": "♮"}.get(note.accidental, "")
        duration = tr(DURATION_NAMES[note.duration]) + (tr("（附點）") if note.dotted else "")
        return " · ".join((accidental + str(note.degree), duration, tr(OCTAVE_NAMES[note.octave])))

