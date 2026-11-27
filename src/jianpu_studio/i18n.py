"""Interface language: Traditional Chinese is the source text, Simplified is a reviewed catalog.

``tr("繁體原文")`` returns the text for the current language at the moment it is
called, so labels in module-level tables must be looked up when shown, not when
imported.  ``N_("繁體原文")`` only marks such a table entry for extraction.

Switching language never rebuilds widgets.  ``translate_tree`` converts the
visible texts of an existing widget tree through the same catalog, in either
direction, which is why catalog values must be unique (checked by the tests).
"""
from __future__ import annotations

import re
import string
import sys
import weakref
from collections.abc import Callable

from PySide6.QtCore import QCoreApplication, QLibraryInfo, QLocale, QTranslator

TRADITIONAL = "zh_TW"
SIMPLIFIED = "zh_CN"
LANGUAGES = (TRADITIONAL, SIMPLIFIED)
LANGUAGE_NAMES = {TRADITIONAL: "繁體中文", SIMPLIFIED: "简体中文"}
LANGUAGE_SHORT = {TRADITIONAL: "繁", SIMPLIFIED: "简"}   # status-bar switch, never translated
LANGUAGE_MENU_TITLE = "語言 / 语言"
SETTINGS_KEY = "language"

_language = TRADITIONAL
_listeners: list[weakref.WeakMethod] = []
_catalog: dict[str, str] | None = None
_indexes: dict[tuple[str, str], tuple[dict, list]] = {}
_qt_translator: QTranslator | None = None


def catalog() -> dict[str, str]:
    global _catalog
    if _catalog is None:
        from .i18n_zh_cn import CATALOG
        _catalog = CATALOG
    return _catalog


def N_(text: str) -> str:
    """Mark a source string for the catalog without translating it yet."""
    return text


def modifier_name() -> str:
    """The name of the shortcut modifier Qt calls Ctrl: it is the Command key on macOS."""
    return "⌘" if sys.platform == "darwin" else "Ctrl"


def platform_keys(text: str) -> str:
    """Show shortcut hints with this platform's modifier (Ctrl+Z -> ⌘Z on macOS)."""
    if "Ctrl" not in text or modifier_name() == "Ctrl":
        return text
    return text.replace("Ctrl+", modifier_name()).replace("Ctrl", modifier_name())


def _portable_keys(text: str) -> str:
    """Undo ``platform_keys`` so shown text can be matched against the catalog."""
    if "⌘" not in text:
        return text
    # platform_keys turned "Ctrl+X" into "⌘X" and a bare "Ctrl" (e.g. "Ctrl＋點", fullwidth
    # plus) into "⌘": only a ⌘ followed by a key, not by a plus or a space, had a "+".
    return re.sub(r"⌘(?=[^\s＋])", "Ctrl+", text).replace("⌘", "Ctrl")


def tr(text: str) -> str:
    if _language != TRADITIONAL:
        text = catalog().get(text, text)
    return platform_keys(text)


def current_language() -> str:
    return _language


def is_language(value: object) -> bool:
    return value in LANGUAGES


def detect_language(locale: str | QLocale | None = None) -> str:
    """Pick the interface language for a locale: script first, then territory."""
    if locale is None:
        names = QLocale.system().uiLanguages()
        locale = names[0] if names else QLocale.system()
    if isinstance(locale, str):
        locale = QLocale(locale.replace("-", "_"))
    if locale.language() != QLocale.Language.Chinese:
        return TRADITIONAL
    script = locale.script()
    if script == QLocale.Script.SimplifiedChineseScript:
        return SIMPLIFIED
    if script == QLocale.Script.TraditionalChineseScript:
        return TRADITIONAL
    territory = locale.territory()
    if territory in (QLocale.Country.China, QLocale.Country.Singapore):
        return SIMPLIFIED
    return TRADITIONAL


def init_language(settings=None) -> str:
    """Apply the saved language, else follow the system locale (not persisted)."""
    saved = settings.value(SETTINGS_KEY, "") if settings is not None else ""
    language = saved if is_language(saved) else detect_language()
    set_language(language)
    return language


def on_change(callback: Callable[[str, str], None]) -> None:
    """Call ``callback(new, old)`` after the language changes (weakly held)."""
    _listeners.append(weakref.WeakMethod(callback))


def off_change(callback: Callable[[str, str], None]) -> None:
    _listeners[:] = [ref for ref in _listeners if ref() is not None and ref() != callback]


def set_language(language: str) -> bool:
    global _language
    if not is_language(language):
        raise ValueError(f"unsupported language: {language}")
    old, _language = _language, language
    _apply_qt_translator()
    if old == language:
        return False
    for ref in list(_listeners):
        callback = ref()
        if callback is None:
            _listeners.remove(ref)
            continue
        try:
            callback(language, old)
        except RuntimeError:  # the window's Qt object is already gone
            _listeners.remove(ref)
    return True


def _apply_qt_translator() -> None:
    """Qt's own strings (standard buttons, text-edit menus) follow the language."""
    global _qt_translator
    app = QCoreApplication.instance()
    if app is None:
        return
    if _qt_translator is not None:
        try:
            app.removeTranslator(_qt_translator)
            _qt_translator.deleteLater()
        except RuntimeError:
            pass
        _qt_translator = None
    translator = QTranslator(app)
    path = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    if translator.load(f"qtbase_{_language}", path) and app.installTranslator(translator):
        _qt_translator = translator
    else:
        translator.deleteLater()


def ui_font_family(language: str | None = None) -> str:
    """The CJK face that carries the glyph forms of the active language."""
    simplified = (language or _language) == SIMPLIFIED
    if sys.platform == "darwin":
        return "PingFang SC" if simplified else "PingFang TC"
    if sys.platform == "win32":
        return "Microsoft YaHei" if simplified else "Microsoft JhengHei"
    return "Noto Sans CJK SC" if simplified else "Noto Sans CJK TC"


def ui_font_stack(language: str | None = None) -> list[str]:
    """Interface font families in fallback order (wow/design.md §4.3).

    Windows prefers the narrower "UI" faces of JhengHei / YaHei; macOS uses PingFang.
    The first family that carries the language's glyph forms always comes first.
    """
    simplified = (language or _language) == SIMPLIFIED
    if sys.platform == "win32":
        base = "Microsoft YaHei" if simplified else "Microsoft JhengHei"
        return [base + " UI", base, "Segoe UI"]
    if sys.platform == "darwin":
        return [ui_font_family(language), "Helvetica Neue"]
    return [ui_font_family(language), "DejaVu Sans"]


def latin_sans_family() -> str:
    return {"darwin": "Helvetica Neue", "win32": "Segoe UI"}.get(sys.platform, "DejaVu Sans")


def latin_serif_family() -> str:
    return "DejaVu Serif" if sys.platform not in ("darwin", "win32") else "Times New Roman"


# --- converting text that is already on screen -------------------------------

def _template(source: str):
    pieces, names = [], []
    for literal, name, spec, conversion in string.Formatter().parse(source):
        pieces.append(re.escape(literal))
        if name is not None:
            if not name.isidentifier() or spec or conversion:
                raise ValueError(f"unsupported placeholder in {source!r}")
            pieces.append(f"(?P<{name}>.+?)" if name not in names else f"(?P={name})")
            names.append(name)
    return re.compile("".join(pieces), re.DOTALL), sum(len(p) for p in pieces)


def _index(old: str, new: str):
    key = (old, new)
    if key not in _indexes:
        exact, templates = {}, []
        for source, simplified in catalog().items():
            a, b = (source, simplified) if (old, new) == (TRADITIONAL, SIMPLIFIED) else (simplified, source)
            if "{" in a:
                pattern, weight = _template(a)
                templates.append((weight, pattern, b))
            else:
                exact.setdefault(a, b)
        templates.sort(key=lambda item: -item[0])
        _indexes[key] = (exact, templates)
    return _indexes[key]


def convert(text: str, new: str, old: str | None = None) -> str:
    return platform_keys(_convert(_portable_keys(text), new, old))


def _convert(text: str, new: str, old: str | None = None) -> str:
    """Re-render text that was produced by ``tr`` in ``old`` for ``new``.

    Unknown text, including anything the user typed, is returned unchanged.
    Placeholder values are converted recursively, so nested messages follow.
    """
    old = old or (TRADITIONAL if new == SIMPLIFIED else SIMPLIFIED)
    if not text or old == new:
        return text
    exact, templates = _index(old, new)
    if text in exact:
        return exact[text]
    for _, pattern, target in templates:
        match = pattern.fullmatch(text)
        if match:
            return target.format(**{k: _convert(v, new, old) for k, v in match.groupdict().items()})
    if "\n" in text:
        return "\n".join(_convert(line, new, old) for line in text.split("\n"))
    return text


def translate_tree(root, new: str, old: str) -> None:
    """Convert every UI text below ``root``; user content (line edits) is skipped."""
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import (
        QAbstractButton,
        QComboBox,
        QGroupBox,
        QLabel,
        QLineEdit,
        QMenu,
        QPlainTextEdit,
        QTabWidget,
        QTextEdit,
        QWidget,
    )

    def conv(text):
        return convert(text, new, old)

    def apply(widget, getter, setter):
        value = getter()
        result = conv(value)
        if result != value:
            setter(result)

    for widget in [root, *root.findChildren(QWidget)]:
        apply(widget, widget.toolTip, widget.setToolTip)
        apply(widget, widget.accessibleName, widget.setAccessibleName)
        apply(widget, widget.accessibleDescription, widget.setAccessibleDescription)
        if isinstance(widget, (QLabel, QAbstractButton)):
            apply(widget, widget.text, widget.setText)
        elif isinstance(widget, QGroupBox):
            apply(widget, widget.title, widget.setTitle)
        elif isinstance(widget, QComboBox):
            for index in range(widget.count()):
                apply(widget, lambda i=index: widget.itemText(i), lambda v, i=index: widget.setItemText(i, v))
            apply(widget, widget.placeholderText, widget.setPlaceholderText)
        elif isinstance(widget, QTabWidget):
            for index in range(widget.count()):
                apply(widget, lambda i=index: widget.tabText(i), lambda v, i=index: widget.setTabText(i, v))
                apply(widget, lambda i=index: widget.tabToolTip(i), lambda v, i=index: widget.setTabToolTip(i, v))
        elif isinstance(widget, (QLineEdit, QPlainTextEdit, QTextEdit)):
            apply(widget, widget.placeholderText, widget.setPlaceholderText)
    for menu in root.findChildren(QMenu):
        apply(menu, menu.title, menu.setTitle)
    for action in root.findChildren(QAction):
        apply(action, action.text, action.setText)
        apply(action, action.toolTip, action.setToolTip)
        apply(action, action.statusTip, action.setStatusTip)
