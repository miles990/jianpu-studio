"""Design tokens: the only place colours are defined (wow/design.md §4).

One token table per appearance generates the Qt palette, the application style sheet
and the colours the score canvas and the palette grid paint with, so the window, the
dialogs and the drawn music always agree. ``ThemeManager`` follows the system light or
dark setting (``QStyleHints.colorScheme``) unless the user picked one, and re-applies
everything in place when either changes.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication

from . import i18n

LIGHT = {
    "window": "#f3f2ee", "panel": "#fbfaf7", "raised": "#ffffff", "desk": "#e6e4dd", "paper": "#fffefa",
    "ink": "#1a1d1c", "text": "#1d2321", "text2": "#535c59", "text3": "#6b7471", "border": "#d8d5cc",
    "control": "#8b877c", "disabled": "#8d9491", "accent": "#0d7560", "onaccent": "#ffffff",
    "accentsoft": "#d9eee6", "accenttext": "#0a6552", "sel": "#cfe9df", "hover": "#ecebe5",
    "warn": "#9a5600", "warnsoft": "#fbefd8", "danger": "#b42318", "dangersoft": "#fbe4e1",
    "shadow": "#000000",
}
DARK = {
    "window": "#131615", "panel": "#191d1c", "raised": "#212625", "desk": "#0e1110", "paper": "#1c201f",
    "ink": "#ebe8e0", "text": "#e7e9e7", "text2": "#a9b2af", "text3": "#8e9895", "border": "#2f3533",
    "control": "#69736f", "disabled": "#6f7875", "accent": "#43c4a3", "onaccent": "#08201a",
    "accentsoft": "#163a31", "accenttext": "#62d6b8", "sel": "#1d4a3e", "hover": "#262b2a",
    "warn": "#f1b35a", "warnsoft": "#3a2a10", "danger": "#ff8d80", "dangersoft": "#3f1b17",
    "shadow": "#000000",
}
# "深色時譜紙仍用白色": the paper keeps the light colours, everything else stays dark.
WHITE_PAPER = {"paper": "#fffefa", "ink": "#1a1d1c", "sel": "#cfe9df"}
TOKENS = {"light": LIGHT, "dark": DARK}

APPEARANCES = ("system", "light", "dark")
SETTINGS_KEY = "appearance"
PAPER_KEY = "white_paper_in_dark"
SPACING = (2, 4, 6, 8, 12, 16, 24, 32)


def tokens(scheme: str, white_paper: bool = False) -> dict[str, str]:
    values = dict(TOKENS[scheme])
    if scheme == "dark" and white_paper:
        values.update(WHITE_PAPER)
        values["paper_light"] = "1"
    return values


def qcolor(value: str, alpha: float | None = None) -> QColor:
    color = QColor(value)
    if alpha is not None:
        color.setAlphaF(alpha)
    return color


class CanvasColors:
    """Colours for everything painted by hand (score, symbol cells, chips); from the tokens."""

    def __init__(self, t: dict[str, str]):
        self.tokens = t
        for name, value in t.items():
            if name.startswith("paper_light"):
                continue
            setattr(self, name, QColor(value))
        paper_dark = t["paper"] == DARK["paper"]
        self.dark = paper_dark
        self.faint = qcolor(t["ink"], 0.28)          # the grey "5" under a symbol preview
        self.part_tint = qcolor(t["accent"], 0.06)   # the row of the part being edited
        self.sel_measure = qcolor(t["accent"], 0.10)
        self.halo = qcolor(t["accent"], 0.28)
        self.playline = QColor(t["accent"])
        self.shadow_near = qcolor(t["shadow"], 0.10 if not paper_dark else 0.45)
        self.shadow_far = qcolor(t["shadow"], 0.06 if not paper_dark else 0.30)
        self.paper_edge = QColor(t["border"])


def canvas_colors(t: dict[str, str]) -> CanvasColors:
    return CanvasColors(t)


def build_palette(t: dict[str, str]) -> QPalette:
    p = QPalette()
    role, group = QPalette.ColorRole, QPalette.ColorGroup
    values = {
        role.Window: t["window"], role.WindowText: t["text"], role.Base: t["raised"],
        role.AlternateBase: t["panel"], role.ToolTipBase: t["raised"], role.ToolTipText: t["text"],
        role.PlaceholderText: t["text3"], role.Text: t["text"], role.Button: t["raised"],
        role.ButtonText: t["text"], role.BrightText: t["danger"], role.Light: t["raised"],
        role.Midlight: t["panel"], role.Mid: t["border"], role.Dark: t["control"], role.Shadow: t["shadow"],
        role.Highlight: t["accent"], role.HighlightedText: t["onaccent"], role.Link: t["accenttext"],
        role.LinkVisited: t["accenttext"],
    }
    for r, value in values.items():
        p.setColor(r, QColor(value))
    for r in (role.WindowText, role.Text, role.ButtonText):
        p.setColor(group.Disabled, r, QColor(t["disabled"]))
    p.setColor(group.Disabled, role.Base, QColor(t["panel"]))
    p.setColor(group.Disabled, role.Button, QColor(t["panel"]))
    p.setColor(group.Inactive, role.Highlight, QColor(t["accent"]))
    p.setColor(group.Inactive, role.HighlightedText, QColor(t["onaccent"]))
    return p


def font_stack_css(language: str | None = None) -> str:
    return ", ".join(f"'{name}'" for name in i18n.ui_font_stack(language))


_CHEVRONS: dict[tuple[str, str], str] = {}


def chevron_file(color: str, name: str = "chevron-down") -> str:
    """A recoloured copy of an icon on disk: QSS ``image:`` only takes file paths."""
    key = (name, color)
    if key not in _CHEVRONS:
        from .icons import svg_text
        folder = Path(tempfile.gettempdir()) / "jianpu-studio-icons"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{name}-{color.lstrip('#')}.svg"
        if not path.is_file():
            path.write_text(svg_text(name).replace("currentColor", color), encoding="utf-8")
        _CHEVRONS[key] = path.as_posix()
    return _CHEVRONS[key]


def build_qss(t: dict[str, str], language: str | None = None) -> str:
    """The whole application style sheet, generated from the tokens (no other colours exist)."""
    fonts = font_stack_css(language)
    numbers = f"'{i18n.latin_sans_family()}'"
    arrow = chevron_file(t["text2"])
    arrow_disabled = chevron_file(t["disabled"])
    c = dict(t, fonts=fonts, numbers=numbers, arrow=arrow, arrow_disabled=arrow_disabled)
    return """
QWidget { font-family: %(fonts)s; font-size: 13px; color: %(text)s; }
QMainWindow, QDialog, QWidget#central { background: %(window)s; }
QToolTip { background: %(raised)s; color: %(text)s; border: 1px solid %(border)s; padding: 6px 8px; border-radius: 6px; }
QLabel { background: transparent; }
QLabel[role="muted"], QLabel#muted { color: %(text2)s; }
QLabel[role="hint"] { color: %(text3)s; font-size: 12px; }
QLabel[role="heading"] { color: %(text2)s; font-size: 12px; font-weight: 600; }
QLabel[role="title"] { font-size: 14px; font-weight: 600; }
QLabel#brand, QLabel#file_name { font-size: 14px; font-weight: 600; }
QLabel#dirty_mark { color: %(text2)s; font-size: 12px; }
QLabel#section { font-size: 15px; font-weight: 600; }
QLabel#notice, QLabel[role="warn"] { background: %(warnsoft)s; color: %(warn)s; padding: 8px 10px; border-radius: 6px; }
QFrame#toolbar, QFrame#scorebar, QFrame#notebar, QFrame#statusframe { background: %(panel)s; border: none; border-bottom: 1px solid %(border)s; }
QFrame#statusframe { border-bottom: none; border-top: 1px solid %(border)s; }
QFrame#sidepanel { background: %(panel)s; border: none; border-left: 1px solid %(border)s; }
QFrame#guidebar { background: %(accentsoft)s; border: none; border-bottom: 1px solid %(border)s; }
QFrame#guidebar QLabel { color: %(text)s; }
QFrame#rule { background: %(border)s; border: none; max-height: 1px; min-height: 1px; }
QFrame#vrule { background: %(border)s; border: none; max-width: 1px; min-width: 1px; }
QFrame#detail { background: %(panel)s; border: none; border-top: 1px solid %(border)s; }
QFrame#previewbox { background: %(paper)s; border: 1px solid %(border)s; border-radius: 6px; }
QFrame#toast { background: %(ink)s; border-radius: 8px; }
QFrame#toast QLabel { color: %(paper)s; }
QFrame#toast QPushButton { background: transparent; color: %(paper)s; border: 1px solid %(text3)s; font-weight: 600; padding: 3px 8px; }
QFrame#transport { background: %(raised)s; border: 1px solid %(border)s; border-radius: 8px; }
QFrame#startpage { background: %(desk)s; }
QFrame[card="true"] { background: %(raised)s; border: 1px solid %(border)s; border-radius: 8px; }
QFrame[card="true"]:hover { border-color: %(accent)s; }
QPushButton, QToolButton { background: %(raised)s; color: %(text)s; border: 1px solid %(border)s; border-radius: 6px; padding: 4px 10px; min-height: 22px; }
QToolButton { padding: 4px 6px; }
QPushButton:hover, QToolButton:hover { background: %(hover)s; }
QPushButton:pressed, QToolButton:pressed { background: %(accentsoft)s; }
QPushButton:checked, QToolButton:checked { background: %(accentsoft)s; border-color: %(accent)s; color: %(accenttext)s; }
QPushButton:disabled, QToolButton:disabled { color: %(disabled)s; background: %(panel)s; border-color: %(border)s; }
QPushButton:focus, QToolButton:focus { border: 2px solid %(accent)s; padding: 3px 9px; }
QToolButton:focus { padding: 3px 5px; }
QPushButton[kind="primary"], QToolButton[kind="primary"] { background: %(accent)s; color: %(onaccent)s; border: 1px solid %(accent)s; font-weight: 600; }
QPushButton[kind="primary"]:hover, QToolButton[kind="primary"]:hover { background: %(accenttext)s; }
QPushButton[kind="primary"]:focus, QToolButton[kind="primary"]:focus { border: 2px solid %(text)s; }
QPushButton[kind="ghost"], QToolButton[kind="ghost"] { background: transparent; border: 1px solid transparent; }
QPushButton[kind="ghost"]:hover, QToolButton[kind="ghost"]:hover { background: %(hover)s; }
QPushButton[kind="ghost"]:checked, QToolButton[kind="ghost"]:checked { background: %(accentsoft)s; color: %(accenttext)s; border-color: %(accent)s; }
QPushButton[kind="ghost"]:focus, QToolButton[kind="ghost"]:focus { border: 2px solid %(accent)s; }
QPushButton[kind="link"] { background: transparent; border: none; color: %(accenttext)s; text-decoration: underline; padding: 2px 2px; font-weight: 600; }
QPushButton[kind="link"]:focus { border: 2px solid %(accent)s; padding: 0px; }
QToolButton[kind="key"] { font-family: %(numbers)s; font-size: 19px; padding: 0px; min-height: 0px; }
QToolButton[kind="key"]:focus { padding: 0px; }
QToolButton[kind="dur"] { font-family: %(numbers)s; font-size: 12px; padding: 0px; min-height: 0px; }
QToolButton[kind="dur"]:focus { padding: 0px; }
QToolButton[flash="true"] { background: %(accentsoft)s; border-color: %(accent)s; }
QToolButton[position="first"], QPushButton[position="first"] { border-top-right-radius: 0px; border-bottom-right-radius: 0px; }
QToolButton[position="middle"], QPushButton[position="middle"] { border-radius: 0px; border-left: none; }
QToolButton[position="last"], QPushButton[position="last"] { border-top-left-radius: 0px; border-bottom-left-radius: 0px; border-left: none; }
QToolButton[chip="true"], QPushButton[chip="true"] { border-radius: 13px; padding: 2px 8px; font-size: 12px; min-height: 20px; }
QToolButton[chip="true"]:checked, QPushButton[chip="true"]:checked { font-weight: 600; }
QToolButton::menu-indicator, QPushButton::menu-indicator { image: url("%(arrow)s"); subcontrol-origin: padding; subcontrol-position: right center; width: 12px; height: 12px; right: 4px; }
QToolButton[popupMode="1"] { padding-right: 18px; }
QToolButton::menu-button { border: none; width: 16px; }
QToolButton::menu-arrow { image: url("%(arrow)s"); width: 12px; height: 12px; }
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit, QTextEdit { background: %(raised)s; color: %(text)s; border: 1px solid %(control)s; border-radius: 6px; padding: 3px 7px; selection-background-color: %(accent)s; selection-color: %(onaccent)s; min-height: 22px; }
QPlainTextEdit, QTextEdit { padding: 4px; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QPlainTextEdit:focus, QTextEdit:focus { border: 2px solid %(accent)s; padding: 2px 6px; }
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled { color: %(disabled)s; background: %(panel)s; border-color: %(border)s; }
QLineEdit#title_field { background: transparent; border: 1px solid transparent; font-size: 15px; font-weight: 600; }
QLineEdit#title_field:hover { border-color: %(control)s; background: %(raised)s; }
QLineEdit#title_field:focus { border: 2px solid %(accent)s; background: %(raised)s; }
QLineEdit#search { border-radius: 8px; min-height: 26px; }
QComboBox { padding-right: 22px; }
QComboBox::drop-down { width: 22px; border: none; subcontrol-origin: padding; subcontrol-position: center right; }
QComboBox::down-arrow { image: url("%(arrow)s"); width: 14px; height: 14px; }
QComboBox::down-arrow:disabled { image: url("%(arrow_disabled)s"); }
QComboBox QAbstractItemView { background: %(raised)s; color: %(text)s; border: 1px solid %(border)s; selection-background-color: %(accentsoft)s; selection-color: %(text)s; outline: 0; padding: 2px; }
QSpinBox::up-button, QSpinBox::down-button { width: 0px; border: none; }
QCheckBox { spacing: 6px; background: transparent; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid %(control)s; border-radius: 4px; background: %(raised)s; }
QCheckBox::indicator:checked { background: %(accent)s; border-color: %(accent)s; image: url("%(check)s"); }
QCheckBox:focus { color: %(accenttext)s; }
QMenuBar { background: %(panel)s; color: %(text)s; border-bottom: 1px solid %(border)s; }
QMenuBar::item { padding: 4px 10px; background: transparent; }
QMenuBar::item:selected { background: %(hover)s; border-radius: 4px; }
QMenu { background: %(raised)s; color: %(text)s; border: 1px solid %(border)s; padding: 4px; }
QMenu::item { padding: 5px 22px 5px 12px; border-radius: 4px; }
QMenu::item:selected { background: %(accentsoft)s; color: %(text)s; }
QMenu::item:disabled { color: %(disabled)s; }
QMenu::separator { height: 1px; background: %(border)s; margin: 4px 6px; }
QMenu::indicator { width: 14px; height: 14px; }
QStatusBar { background: %(panel)s; color: %(text2)s; border-top: 1px solid %(border)s; }
QStatusBar::item { border: none; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 0; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 0; }
QScrollBar::handle:vertical { background: %(border)s; border-radius: 4px; min-height: 28px; margin: 1px 2px; }
QScrollBar::handle:horizontal { background: %(border)s; border-radius: 4px; min-width: 28px; margin: 2px 1px; }
QScrollBar::handle:hover { background: %(control)s; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0px; height: 0px; border: none; background: none; }
QScrollBar::add-page, QScrollBar::sub-page { background: none; }
QSplitter::handle { background: %(border)s; }
QSplitter::handle:horizontal { width: 1px; }
QProgressBar { border: none; background: %(border)s; max-height: 3px; }
QProgressBar::chunk { background: %(accent)s; }
QTabWidget::pane { border: 1px solid %(border)s; background: %(raised)s; border-radius: 6px; top: -1px; }
QTabBar::tab { padding: 6px 12px; background: %(panel)s; color: %(text2)s; border: 1px solid %(border)s; border-bottom: none; border-top-left-radius: 6px; border-top-right-radius: 6px; margin-right: 2px; }
QTabBar::tab:selected { background: %(raised)s; color: %(accenttext)s; font-weight: 600; }
QListWidget, QListView, QTreeView, QTableView { background: %(raised)s; color: %(text)s; border: 1px solid %(border)s; border-radius: 6px; outline: 0; }
QListWidget::item:selected, QTreeView::item:selected, QTableView::item:selected { background: %(accentsoft)s; color: %(text)s; }
QListWidget#categories { background: %(window)s; border: none; border-right: 1px solid %(border)s; border-radius: 0px; padding-top: 4px; }
QListWidget#categories::item { padding: 5px 4px 5px 10px; color: %(text2)s; border-left: 3px solid transparent; }
QListWidget#categories::item:hover { background: %(hover)s; }
QListWidget#categories::item:selected { color: %(accenttext)s; font-weight: 600; background: transparent; border-left: 3px solid %(accent)s; }
QHeaderView::section { background: %(panel)s; color: %(text2)s; border: none; border-bottom: 1px solid %(border)s; padding: 4px 6px; }
QScrollArea { background: transparent; border: none; }
QScrollArea > QWidget > QWidget { background: transparent; }
QAbstractItemView#notes { background: %(desk)s; border: none; }
QPdfView { background: %(desk)s; border: none; }
QLabel[role="warn-text"] { color: %(warn)s; font-weight: 600; }
QLabel[role="ok"] { color: %(accenttext)s; }
QLabel#keycap { color: %(text2)s; border: 1px solid %(border)s; border-radius: 4px; padding: 0px 4px; font-size: 11px; background: %(panel)s; }
QLabel#transport_label { font-size: 13px; }
QFrame#mark_chip { background: %(raised)s; border: 1px solid %(control)s; border-radius: 6px; }
QFrame#mark_chip[partial="true"] { border: 1px dashed %(control)s; }
QFrame#info_popup { background: %(raised)s; border: 1px solid %(border)s; border-radius: 8px; }
QFrame#inspector { background: %(panel)s; border: none; }
QWidget#palette { background: %(panel)s; }
QWidget#status_host { background: transparent; }
QWidget#status_host QPushButton[kind="ghost"] { padding: 1px 6px; min-height: 18px; }
QWidget#status_host QToolButton { min-height: 0px; padding: 0px 2px; }
QWidget#status_host QToolButton:focus { padding: 0px 1px; }
QWidget#status_host QComboBox { min-height: 18px; padding: 1px 18px 1px 6px; max-width: 74px; }
QLabel#result_count { color: %(text2)s; font-size: 12px; }
QTreeWidget { background: %(raised)s; }
""" % dict(c, check=chevron_file(t["onaccent"], "check"))


class ThemeManager(QObject):
    """Owns the current appearance; emits ``changed`` after palette and QSS are re-applied."""

    changed = Signal()

    def __init__(self, settings: QSettings | None = None, parent=None):
        super().__init__(parent)
        self.settings = settings
        saved = settings.value(SETTINGS_KEY, "system") if settings is not None else "system"
        self.appearance = saved if saved in APPEARANCES else "system"
        self.white_paper = str(settings.value(PAPER_KEY, "false")).lower() == "true" if settings is not None else False
        self.scheme = "light"
        self.tokens = tokens("light")
        self.colors = canvas_colors(self.tokens)
        app = QGuiApplication.instance()
        if app is not None:
            app.styleHints().colorSchemeChanged.connect(self._system_changed)

    def system_scheme(self) -> str:
        app = QGuiApplication.instance()
        if app is None:
            return "light"
        scheme = app.styleHints().colorScheme()
        return "dark" if scheme == Qt.ColorScheme.Dark else "light"

    def _system_changed(self, *_):
        if self.appearance == "system":
            self.apply()

    def set_appearance(self, appearance: str) -> None:
        if appearance not in APPEARANCES:
            raise ValueError(appearance)
        self.appearance = appearance
        if self.settings is not None:
            self.settings.setValue(SETTINGS_KEY, appearance)
        self.apply()

    def cycle(self) -> str:
        """☀ → ☾ → 自動 (status-bar button)."""
        order = ("light", "dark", "system")
        self.set_appearance(order[(order.index(self.appearance) + 1) % len(order)])
        return self.appearance

    def set_white_paper(self, enabled: bool) -> None:
        self.white_paper = bool(enabled)
        if self.settings is not None:
            self.settings.setValue(PAPER_KEY, "true" if enabled else "false")
        self.apply()

    def apply(self, language: str | None = None) -> None:
        app = QApplication.instance()
        scheme = self.system_scheme() if self.appearance == "system" else self.appearance
        self.scheme = scheme
        self.tokens = tokens(scheme, self.white_paper)
        self.colors = canvas_colors(self.tokens)
        if app is not None:
            if app.style().name().lower() != "fusion":
                app.setStyle("Fusion")
            hints = app.styleHints()
            if hasattr(hints, "setColorScheme") and sys.platform != "linux":
                # Native title bars and Qt's own dialogs follow a forced choice (Qt 6.8+).
                hints.blockSignals(True)
                try:
                    hints.setColorScheme({"light": Qt.ColorScheme.Light, "dark": Qt.ColorScheme.Dark}.get(
                        self.appearance, Qt.ColorScheme.Unknown))
                finally:
                    hints.blockSignals(False)
            app.setPalette(build_palette(self.tokens))
            app.setStyleSheet(build_qss(self.tokens, language))
        from . import icons
        icons.refresh_all(self.tokens)
        self.changed.emit()
