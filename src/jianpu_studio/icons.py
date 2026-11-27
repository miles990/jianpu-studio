"""Line icons (assets/icons/*.svg, stroke=currentColor) recoloured per theme and state.

``icon(name)`` returns a cached QIcon with Normal / Disabled / Active / Selected pixmaps
in the current token colours. ``attach(widget, name)`` remembers the name on the widget so
``refresh_all`` (called when the theme changes) can replace the icon in place.
"""
from __future__ import annotations

import weakref
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

ICON_DIR = Path(__file__).parent / "assets" / "icons"
SIZES = (16, 20, 24)
_cache: dict[tuple, QIcon] = {}
_attached: list[tuple[weakref.ref, str, str]] = []
_tokens: dict[str, str] = {}


@lru_cache(maxsize=None)
def svg_text(name: str) -> str:
    return (ICON_DIR / f"{name}.svg").read_text(encoding="utf-8")


def names() -> list[str]:
    return sorted(p.stem for p in ICON_DIR.glob("*.svg"))


def _renderer(name: str, color: str) -> QSvgRenderer:
    return QSvgRenderer(QByteArray(svg_text(name).replace("currentColor", color).encode("utf-8")))


def pixmap(name: str, size: int, color: str | QColor, ratio: float | None = None) -> QPixmap:
    """One recoloured pixmap at the device pixel ratio (sharp on HiDPI)."""
    if ratio is None:
        app = QGuiApplication.instance()
        ratio = app.devicePixelRatio() if app is not None else 1.0
    hex_color = QColor(color).name() if not isinstance(color, str) else color
    image = QPixmap(QSize(round(size * ratio), round(size * ratio)))
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    _renderer(name, hex_color).render(painter, QRectF(0, 0, image.width(), image.height()))
    painter.end()
    image.setDevicePixelRatio(ratio)
    return image


def icon(name: str, role: str = "text") -> QIcon:
    """``role`` picks the normal colour: text, text2, accent or onaccent (on a filled button)."""
    t = _tokens or _default_tokens()
    key = (name, role, tuple(sorted(t.items())))
    if key in _cache:
        return _cache[key]
    result = QIcon()
    normal = t.get(role, t["text"])
    active = t["accenttext"] if role in ("text", "text2") else normal
    for size in SIZES:
        result.addPixmap(pixmap(name, size, normal), QIcon.Mode.Normal)
        result.addPixmap(pixmap(name, size, t["disabled"]), QIcon.Mode.Disabled)
        result.addPixmap(pixmap(name, size, active), QIcon.Mode.Active)
        result.addPixmap(pixmap(name, size, t["accenttext"] if role != "onaccent" else normal), QIcon.Mode.Selected)
    _cache[key] = result
    return result


def _default_tokens() -> dict[str, str]:
    from .theme import LIGHT
    return LIGHT


def attach(widget, name: str, role: str = "text", size: int = 18):
    """Set ``widget``'s icon and keep it in step with theme changes."""
    widget.setIcon(icon(name, role))
    widget.setIconSize(QSize(size, size))
    widget.setProperty("icon_name", name)
    _attached.append((weakref.ref(widget), name, role))
    return widget


def refresh_all(tokens: dict[str, str]) -> None:
    global _tokens
    _tokens = dict(tokens)
    alive = []
    for ref, name, role in _attached:
        widget = ref()
        if widget is None:
            continue
        try:
            widget.setIcon(icon(name, role))
        except RuntimeError:     # the C++ widget is gone
            continue
        alive.append((ref, name, role))
    _attached[:] = alive
    if len(_cache) > 600:
        _cache.clear()
