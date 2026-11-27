"""Shared appearance helpers; every colour comes from ``theme`` tokens."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QAbstractButton, QFrame, QLabel, QPushButton, QToolButton

from . import i18n, icons, theme


def stylesheet(language: str = "zh_TW", scheme: str = "light") -> str:
    """The application style sheet for an interface language (CJK font stack) and scheme."""
    return theme.build_qss(theme.tokens(scheme), language)


def label(text: str, name: str = "", wrap: bool = False, role: str = "") -> QLabel:
    item = QLabel(text)
    item.setObjectName(name)
    item.setWordWrap(wrap)
    item.setTextFormat(Qt.TextFormat.PlainText)
    if role:
        item.setProperty("role", role)
    return item


def tool(text: str = "", icon_name: str = "", tip: str = "", kind: str = "", fn=None, *,
         name: str = "", checkable: bool = False, text_beside: bool = True, icon_role: str = "text",
         size: int = 18) -> QToolButton:
    """A tool button with an optional themed icon; tooltip text should name the shortcut."""
    button = QToolButton()
    if text:
        button.setText(text)
    if icon_name:
        icons.attach(button, icon_name, icon_role, size)
    button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon if text and text_beside and icon_name
                              else Qt.ToolButtonStyle.ToolButtonIconOnly if icon_name and not text
                              else Qt.ToolButtonStyle.ToolButtonTextOnly)
    if tip:
        button.setToolTip(tip)
        if not text:
            button.setAccessibleName(tip)
    if kind:
        button.setProperty("kind", kind)
    if name:
        button.setObjectName(name)
    button.setCheckable(checkable)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    if fn is not None:
        button.clicked.connect(fn)
    return button


def push(text: str, fn=None, kind: str = "", name: str = "") -> QPushButton:
    button = QPushButton(text)
    if kind:
        button.setProperty("kind", kind)
    if name:
        button.setObjectName(name)
    if fn is not None:
        button.clicked.connect(fn)
    return button


def rule(vertical: bool = False) -> QFrame:
    line = QFrame()
    line.setObjectName("vrule" if vertical else "rule")
    return line


def segment(buttons) -> None:
    """Join buttons into one segmented control (rounded only at the ends)."""
    buttons = list(buttons)
    for index, button in enumerate(buttons):
        position = "first" if index == 0 else "last" if index == len(buttons) - 1 else "middle"
        button.setProperty("position", position if len(buttons) > 1 else "")


class ElidedLabel(QLabel):
    """A label that ends with … instead of being cut, with the full text as tooltip."""

    def __init__(self, text: str = ""):
        super().__init__(text)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setMinimumWidth(40)

    def minimumSizeHint(self):
        hint = super().minimumSizeHint()
        hint.setWidth(40)
        return hint

    def paintEvent(self, event):
        from PySide6.QtGui import QPainter
        painter = QPainter(self)
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.setFont(self.font())
        text = self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, self.width())
        painter.drawText(self.rect(), int(self.alignment()) | int(Qt.AlignmentFlag.AlignVCenter), text)
        painter.end()


def repolish(widget) -> None:
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


STYLE = None  # 0.3.x name; the style sheet now depends on the theme (see theme.ThemeManager)
__all__ = ["stylesheet", "label", "tool", "push", "rule", "segment", "repolish", "i18n"]


REASON_MARK = "\n⊘ "     # tooltip line that says why a control cannot be used right now


def tooltip_base(text: str) -> str:
    """A tooltip without the line ``DisabledReasons`` appended."""
    return text.split(REASON_MARK, 1)[0]


class DisabledReasons(QObject):
    """Every disabled button says why (design §7 item 30): 「重複小節 ⊘ 請先點小節號選取小節」.

    ``reason(widget)`` returns the reason in the current language (or ""). The tooltip is
    recomposed whenever the control is enabled or disabled, just before a tooltip shows (the
    reason can change while the control stays disabled) and after a language switch.
    """

    def __init__(self, reason, parent=None):
        super().__init__(parent)
        self.reason = reason
        self.watched: list = []
        self._busy = False

    def watch_tree(self, root) -> None:
        for button in root.findChildren(QAbstractButton):
            self.watch(button)

    def watch(self, widget) -> None:
        if any(w is widget for w in self.watched):
            return
        self.watched.append(widget)
        if isinstance(widget, QAction):        # no events: follow its changed() signal
            widget.changed.connect(lambda w=widget: self.refresh(w))
        else:
            widget.installEventFilter(self)
        self.refresh(widget)

    def refresh(self, widget) -> None:
        if self._busy:
            return
        self._busy = True
        try:
            tip = widget.toolTip()
            base = tooltip_base(tip)
            reason = "" if widget.isEnabled() else self.reason(widget)
            wanted = base + REASON_MARK + reason if reason else base
            if wanted != tip:
                widget.setToolTip(wanted)
        except RuntimeError:          # the C++ object is gone
            self.watched = [w for w in self.watched if w is not widget]
        finally:
            self._busy = False

    def refresh_all(self) -> None:
        for widget in list(self.watched):
            self.refresh(widget)

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.EnabledChange, QEvent.Type.ToolTip):
            self.refresh(watched)
        return False
