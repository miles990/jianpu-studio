"""Shared appearance helpers; every colour comes from ``theme`` tokens."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QToolButton

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
