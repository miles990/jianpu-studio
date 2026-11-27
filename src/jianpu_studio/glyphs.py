"""One drawing routine per registry mark, shared by the score canvas and the palette cells.

Coordinates are in unscaled design pixels (wow/design.md §3.1) multiplied by ``s``.
``draw_mark`` draws a point/pre mark centred on (x, y); spans and bar items have their
own functions. Every colour is passed in (it comes from the theme tokens), so the same
code paints light, dark and the palette's dimmed previews. Music text uses the fonts
LilyPond itself prints with (Nimbus Sans, C059), loaded from the bundled LilyPond.
"""
from __future__ import annotations

import math
import re
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QFontMetricsF, QPainter, QPainterPath, QPen

from . import i18n, symbols

# ---------------------------------------------------------------- fonts
_families: dict[str, str] = {}
_loaded = False


def _lilypond_font_dir() -> Path | None:
    try:
        from .engine import find_tool
        tool = find_tool("lilypond")
    except Exception:
        return None
    if tool is None:
        return None
    for share in (tool.resolve().parent.parent / "share" / "lilypond").glob("*/fonts/otf"):
        return share
    return None


def load_fonts() -> None:
    """Register Nimbus Sans and C059 from the bundled LilyPond once; fall back silently."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    folder = _lilypond_font_dir()
    if folder is None:
        return
    for key, name in (("sans", "NimbusSans-Regular.otf"), ("sans_bold", "NimbusSans-Bold.otf"),
                      ("serif_bi", "C059-BdIta.otf"), ("serif_i", "C059-Italic.otf"), ("serif", "C059-Roman.otf")):
        path = folder / name
        if path.is_file():
            ident = QFontDatabase.addApplicationFont(str(path))
            found = QFontDatabase.applicationFontFamilies(ident) if ident >= 0 else []
            if found:
                _families[key] = found[0]


def sans_family() -> str:
    load_fonts()
    return _families.get("sans", i18n.latin_sans_family())


def serif_family() -> str:
    load_fonts()
    return _families.get("serif_bi", _families.get("serif", i18n.latin_serif_family()))


@lru_cache(maxsize=256)
def _font(kind: str, pixel: float, ui_family: str) -> QFont:
    if kind in ("number", "sans", "sans_bold"):
        font = QFont(sans_family())
        font.setWeight(QFont.Weight.Bold if kind == "sans_bold" else QFont.Weight.Normal)
    elif kind in ("dynamic", "italic", "serif"):
        font = QFont(serif_family())
        font.setItalic(kind != "serif")
        font.setWeight(QFont.Weight.Bold if kind == "dynamic" else QFont.Weight.Normal)
    else:
        font = QFont(ui_family)
        font.setWeight(QFont.Weight.Medium)
    font.setPixelSize(max(1, round(pixel)))
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    return font


def font(kind: str, pixel: float, variant: str = "tc") -> QFont:
    """number | sans | sans_bold | dynamic | italic | serif | cjk (technique text)."""
    family = i18n.ui_font_family(i18n.SIMPLIFIED if variant == "sc" else i18n.TRADITIONAL)
    return _font(kind, round(pixel * 2) / 2, family)


# ---------------------------------------------------------------- classification
LAYER_FINGER, LAYER_ARTIC, LAYER_BOW, LAYER_TECH, LAYER_TEMPO = range(5)
LANE_HEIGHT = 14.0


def layer(symbol) -> int:
    """Stacking order above a note, from the note outward (design §3.2)."""
    if symbol.slot == "bow":          # bowings share the fingering page but stack outside it
        return LAYER_BOW
    if symbol.slot in ("string_side", "position"):   # 內／外 and 一把… print as text outside the fingering
        return LAYER_TECH
    if symbol.category == "fingering":
        return LAYER_FINGER
    if symbol.category == "tempo":
        return LAYER_TEMPO
    if symbol.category == "technique":
        return LAYER_TECH
    return LAYER_ARTIC


def position(symbol) -> str:
    """above | below | left | right for point and pre marks."""
    if symbol.kind == "pre" or symbol.place == "left":
        return "left"
    if symbol.place == "right":
        return "right"
    if symbol.place == "below":
        return "below"
    return "above"


def is_dynamic(symbol) -> bool:
    return symbol.category == "dynamic" and symbol.kind == "point"


def mark_height(symbol) -> float:
    spec = symbol.canvas
    if spec.startswith("shape:fermata") or spec.startswith("shape:circled") or "stacked" in spec:
        return 15.0
    if "+sharp" in spec or "+flat" in spec or "+natural" in spec:
        return 19.0
    if spec.startswith("shape:leaves") or spec.startswith("shape:stroke_bars") or spec.startswith("shape:duo"):
        return 16.0
    return LANE_HEIGHT


def stack_gaps(above) -> list[float]:
    """Unscaled gap below each mark of a note's stack (from the note outward): marks of a new
    layer (fingering -> bowing -> 內外…) keep a clear gap, so 一 never closes a ⊓ into a box;
    a fermata nearest the note keeps clear of octave dots."""
    gaps, previous = [], None
    for symbol in above:
        if previous is None:
            gaps.append(4.0 if symbol.canvas.startswith("shape:fermata") else 0.0)
        else:
            gaps.append(5.0 if layer(symbol) != layer(previous) else 2.0)
        previous = symbol
    return gaps


def mark_width(symbol, variant: str = "tc") -> float:
    """Approximate width (unscaled) so lanes and cells can reserve room."""
    text = symbol.canvas_text(variant)
    if text and not symbol.canvas.startswith("shape:"):
        if is_dynamic(symbol) or symbol.category == "tempo":
            return 7.5 * len(text) + 4
        return 12.0 * len(text) + 2
    return 16.0


# ---------------------------------------------------------------- helpers
def _pen(p: QPainter, color: QColor, width: float, s: float):
    pen = QPen(color, max(0.6, width * s))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)


def _text(p: QPainter, text: str, x: float, y: float, qfont: QFont, color: QColor, align: str = "center"):
    """Draw text with its visual centre (x-height aware) on (x, y)."""
    p.setFont(qfont)
    p.setPen(color)
    metrics = QFontMetricsF(qfont)
    width = metrics.horizontalAdvance(text)
    left = x - width / 2 if align == "center" else x if align == "left" else x - width
    baseline = y + (metrics.ascent() - metrics.descent()) / 2
    p.drawText(QPointF(left, baseline), text)
    return width


def _wave(path: QPainterPath, x1: float, x2: float, y: float, s: float, amplitude: float = 2.2, period: float = 6.0):
    x = x1
    path.moveTo(x, y)
    step = period * s / 2
    up = True
    while x < x2 - 0.1:
        nx = min(x2, x + step)
        path.quadTo(QPointF((x + nx) / 2, y + (-1 if up else 1) * amplitude * s), QPointF(nx, y))
        x, up = nx, not up


def _accidental(p, kind: str, x: float, y: float, s: float, color: QColor):
    """Small ♯ ♭ ♮ drawn with lines (no font coverage needed)."""
    _pen(p, color, 1.0, s)
    if kind == "sharp":
        for dx in (-1.6, 1.6):
            p.drawLine(QPointF(x + dx * s, y - 4.5 * s), QPointF(x + dx * s, y + 4.5 * s))
        for dy in (-1.5, 1.5):
            p.drawLine(QPointF(x - 3.2 * s, y + (dy + 0.6) * s), QPointF(x + 3.2 * s, y + (dy - 0.6) * s))
    elif kind == "flat":
        path = QPainterPath(QPointF(x - 2 * s, y - 5 * s))
        path.lineTo(x - 2 * s, y + 4 * s)
        path.cubicTo(x + 3.5 * s, y + 1 * s, x + 3 * s, y - 2.5 * s, x - 2 * s, y + 0.5 * s)
        p.drawPath(path)
    else:
        p.drawLine(QPointF(x - 2 * s, y - 5 * s), QPointF(x - 2 * s, y + 2.5 * s))
        p.drawLine(QPointF(x + 2 * s, y - 2.5 * s), QPointF(x + 2 * s, y + 5 * s))
        p.drawLine(QPointF(x - 2 * s, y - 1 * s), QPointF(x + 2 * s, y - 2 * s))
        p.drawLine(QPointF(x - 2 * s, y + 2.5 * s), QPointF(x + 2 * s, y + 1.5 * s))


def draw_accidental(p, accidental: str, x: float, y: float, s: float, color: QColor):
    _accidental(p, {"#": "sharp", "b": "flat", "n": "natural"}[accidental], x, y, s * 1.25, color)


def _arrowhead(p, x, y, direction, s, color, size=2.6):
    """A small open head at (x, y) pointing up (direction -1) or down (+1)."""
    p.drawLine(QPointF(x, y), QPointF(x - size * 0.8 * s, y - direction * size * s))
    p.drawLine(QPointF(x, y), QPointF(x + size * 0.8 * s, y - direction * size * s))


def _arrow(p, x1, y1, x2, y2, s, color, head=3.5):
    _pen(p, color, 1.3, s)
    p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
    angle = math.atan2(y2 - y1, x2 - x1)
    for side in (-1, 1):
        a = angle + math.pi + side * 0.5
        p.drawLine(QPointF(x2, y2), QPointF(x2 + head * s * math.cos(a), y2 + head * s * math.sin(a)))


# ---------------------------------------------------------------- point marks
def draw_mark(p: QPainter, symbol, x: float, y: float, s: float, color: QColor, variant: str = "tc") -> None:
    """Draw a point/pre mark centred on (x, y)."""
    spec = symbol.canvas
    p.save()
    try:
        if spec.startswith("shape:"):
            _shape(p, spec[6:], symbol, x, y, s, color, variant)
        else:
            _text_mark(p, symbol, x, y, s, color, variant)
    finally:
        p.restore()


_SHAPE_TEXT = {"↗": "slide_up", "↘": "slide_down", "～": "tilde", "▼": "triangle_down", "V": "v",
               "V(bold)": "v_bold", "//": "caesura", "⌒": "arc", "+": "plus", "⤴": "curl_up", "⤵": "curl_down",
               "∟∟(stacked)": "double_tuo"}


# Erhu fingers 一二三 (canvas text of finger.1–3) are drawn as strokes in the proportions of a Hei face at 11.5 px (em ≈ 11.5 s):
# (y offset, width) per stroke. Drawn rather than typeset so 一 always reads as a long *thin* line,
# clearly unlike the short thick tenuto bar, whatever CJK font the system has (or lacks).
FINGER_STROKE_WIDTH = 1.15
FINGER_STROKES = {"finger.1": ((0.0, 10.5),),
                  "finger.2": ((-3.2, 7.5), (3.2, 10.5)),
                  "finger.3": ((-4.0, 8.5), (-0.3, 7.0), (4.0, 10.5))}


def _finger_strokes(p, sid, x, y, s, color):
    _pen(p, color, FINGER_STROKE_WIDTH, s)
    for dy, width in FINGER_STROKES[sid]:
        half = (width - FINGER_STROKE_WIDTH) / 2 * s        # round caps add half a stroke each end
        p.drawLine(QPointF(x - half, y + dy * s), QPointF(x + half, y + dy * s))


def _text_mark(p, symbol, x, y, s, color, variant):
    text = symbol.canvas_text(variant)
    if text in _SHAPE_TEXT:
        _shape(p, _SHAPE_TEXT[text], symbol, x, y, s, color, variant)
        return
    if symbol.id in FINGER_STROKES:
        _finger_strokes(p, symbol.id, x, y, s, color)
        return
    if symbol.id == "finger.0":
        # Open string: a small thin ring, like the other fingerings (never a rest 0).
        _pen(p, color, 1.0, s)
        p.drawEllipse(QPointF(x, y), 2.6 * s, 3.6 * s)
        return
    if is_dynamic(symbol):
        _text(p, text, x, y, font("dynamic", 15 * s), color)
    elif symbol.category in ("tempo", "pedal") or text in ("*",):
        _text(p, text, x, y, font("italic", 13 * s), color)
    elif text == ",":
        _text(p, text, x, y - 3 * s, font("serif", 20 * s), color)
    elif re.fullmatch(r"[A-Za-z0-9 .()∞]+", text):
        _text(p, text, x, y, font("sans_bold", 11.5 * s), color)
    else:
        _text(p, text, x, y, font("cjk", 11.5 * s, variant), color)


def _shape(p, name, symbol, x, y, s, color, variant):
    base = name.split("(")[0]
    arg = name[len(base) + 1:-1] if "(" in name else ""
    path = QPainterPath()
    _pen(p, color, 1.4, s)
    if base == "accent":
        path.moveTo(x - 6 * s, y - 3.5 * s)
        path.lineTo(x + 6 * s, y)
        path.lineTo(x - 6 * s, y + 3.5 * s)
    elif base == "marcato":
        path.moveTo(x - 4.5 * s, y + 4 * s)
        path.lineTo(x, y - 5 * s)
        path.lineTo(x + 4.5 * s, y + 4 * s)
    elif base == "fermata":
        # The dot sits inside the arc, well above an octave dot's place.
        path.moveTo(x - 8 * s, y + 4 * s)
        path.cubicTo(x - 8 * s, y - 7 * s, x + 8 * s, y - 7 * s, x + 8 * s, y + 4 * s)
        p.setBrush(color)
        p.drawEllipse(QPointF(x, y + 1.0 * s), 1.6 * s, 1.6 * s)
        p.setBrush(Qt.BrushStyle.NoBrush)
    elif base == "tenuto":
        path.moveTo(x - 6 * s, y)
        path.lineTo(x + 6 * s, y)
    elif base == "tenuto_bar":          # short thick bar: never mistaken for finger 一 (thin, wide)
        pen = QPen(color, max(1.2, 2.6 * s))
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        p.setPen(pen)
        p.drawLine(QPointF(x - 3.5 * s, y), QPointF(x + 3.5 * s, y))
        return
    elif base == "backslash":           # 抹／彈: a drawn stroke, not the 丶 glyph that reads as an octave dot
        _pen(p, color, 1.6, s)
        p.drawLine(QPointF(x - 3 * s, y - 5 * s), QPointF(x + 3 * s, y + 5 * s))
        return
    elif base in ("micro_up", "micro_down"):
        _arrow(p, x, y + (5 if base == "micro_up" else -5) * s, x, y + (-5 if base == "micro_up" else 5) * s, s,
               color, 3)
        return
    elif base in ("triangle_down", "triangle_down_hollow"):
        tri = QPainterPath(QPointF(x - 3.6 * s, y - 3 * s))
        tri.lineTo(x + 3.6 * s, y - 3 * s)
        tri.lineTo(x, y + 3.6 * s)
        tri.closeSubpath()
        _pen(p, color, 1.0, s)
        p.setBrush(color if base == "triangle_down" else Qt.BrushStyle.NoBrush)
        p.drawPath(tri)
        return
    elif base == "wedge":
        tri = QPainterPath(QPointF(x - 2 * s, y - 4.5 * s))
        tri.lineTo(x + 2 * s, y - 4.5 * s)
        tri.lineTo(x, y + 4.5 * s)
        tri.closeSubpath()
        p.setBrush(color)
        p.drawPath(tri)
        return
    elif base == "v":                   # breath V: small, at the upper right like the print
        path.moveTo(x - 3 * s, y - 6.5 * s)
        path.lineTo(x, y + 0.5 * s)
        path.lineTo(x + 3 * s, y - 6.5 * s)
    elif base in ("upbow", "v_bold"):
        if base == "v_bold":
            _pen(p, color, 2.4, s)
        path.moveTo(x - 4.5 * s, y - 5 * s)
        path.lineTo(x, y + 5 * s)
        path.lineTo(x + 4.5 * s, y - 5 * s)
    elif base == "downbow":
        path.moveTo(x - 5 * s, y + 5 * s)
        path.lineTo(x - 5 * s, y - 4.5 * s)
        path.lineTo(x + 5 * s, y - 4.5 * s)
        path.lineTo(x + 5 * s, y + 5 * s)
        p.setBrush(color)
        p.drawRect(QRectF(x - 5 * s, y - 5.5 * s, 10 * s, 2.2 * s))
        p.setBrush(Qt.BrushStyle.NoBrush)
    elif base == "hook_right_down":     # 京胡式拉弓
        path.moveTo(x - 5 * s, y - 4 * s)
        path.lineTo(x + 4 * s, y - 4 * s)
        path.lineTo(x + 4 * s, y + 5 * s)
    elif base == "hook_left_down":      # 京胡式推弓
        path.moveTo(x + 5 * s, y - 4 * s)
        path.lineTo(x - 4 * s, y - 4 * s)
        path.lineTo(x - 4 * s, y + 5 * s)
    elif base == "circle":
        p.drawEllipse(QPointF(x, y), 3.6 * s, 3.6 * s)
        return
    elif base == "diamond":
        path.moveTo(x, y - 5 * s)
        path.lineTo(x + 4 * s, y)
        path.lineTo(x, y + 5 * s)
        path.lineTo(x - 4 * s, y)
        path.closeSubpath()
    elif base in ("prall", "mordent", "prallprall"):
        _ornament_base(p, base, x, y, s, color)
        return
    elif base.startswith(("prall+", "mordent+", "trill+")):
        orn, accidental = base.split("+")
        _ornament_base(p, orn, x, y + 3 * s, s, color)
        _accidental(p, accidental, x, y - 6.5 * s, s * 0.8, color)
        return
    elif base == "trill":
        _ornament_base(p, "trill", x, y, s, color)
        return
    elif base in ("turn", "reverseturn"):
        flip = -1 if base == "reverseturn" else 1
        path.moveTo(x - 8 * s * flip, y + 1 * s)
        path.cubicTo(x - 9 * s * flip, y - 6 * s, x - 2 * s * flip, y - 6 * s, x, y)
        path.cubicTo(x + 2 * s * flip, y + 6 * s, x + 9 * s * flip, y + 6 * s, x + 8 * s * flip, y - 1 * s)
        p.setBrush(color)
        p.drawEllipse(QPointF(x - 8 * s * flip, y + 1 * s), 1.3 * s, 1.3 * s)
        p.drawEllipse(QPointF(x + 8 * s * flip, y - 1 * s), 1.3 * s, 1.3 * s)
        p.setBrush(Qt.BrushStyle.NoBrush)
    elif base == "tremolo":
        count = int(arg or 3)           # same proportions as the printed \jsTrem* strokes
        _pen(p, color, 1.5, s)
        for i in range(count):
            dy = (i - (count - 1) / 2) * 3.4 * s
            p.drawLine(QPointF(x - 4 * s, y + dy + 1.6 * s), QPointF(x + 4 * s, y + dy - 1.6 * s))
        return
    elif base == "arpeggio":
        wave = QPainterPath()
        top, bottom = y - 9 * s, y + 9 * s
        yy, left = top, True
        wave.moveTo(x, yy)
        while yy < bottom - 0.1:
            ny = min(bottom, yy + 3 * s)
            wave.quadTo(QPointF(x + (-2.2 if left else 2.2) * s, (yy + ny) / 2), QPointF(x, ny))
            yy, left = ny, not left
        _pen(p, color, 1.2, s)
        p.drawPath(wave)
        if symbol.id in ("arp.up", "arp.down"):
            tip = top if symbol.id == "arp.up" else bottom
            direction = -1 if symbol.id == "arp.up" else 1
            p.drawLine(QPointF(x, tip), QPointF(x - 2.5 * s, tip - direction * 3 * s))
            p.drawLine(QPointF(x, tip), QPointF(x + 2.5 * s, tip - direction * 3 * s))
        return
    elif base in ("slide_up", "slide_down"):
        # 上滑音: small curved arrow at the upper left, rising into the note; 下滑音: at the upper right,
        # falling out of it (drawn like the printed \jsSlideUp / \jsSlideDown).
        _pen(p, color, 1.3, s)
        points = ((-4, 2), (0, 2), (2.5, 0), (2.5, -6.5)) if base == "slide_up" else \
            ((-4, -1), (0, -1), (2.5, 1), (2.5, 6))
        tail, c1, c2, tip = (QPointF(x + dx * s, y + dy * s) for dx, dy in points)
        path.moveTo(tail)
        path.cubicTo(c1, c2, tip)
        p.drawPath(path)
        _arrowhead(p, tip.x(), tip.y(), -1 if base == "slide_up" else 1, s, color)
        return
    elif base in ("slide_back_up", "slide_back_down"):
        # 上回滑音 ∩ / 下回滑音 ∪ with the head turning back to the note (like \jsSlideBackUp / Down)
        _pen(p, color, 1.3, s)
        sign = -1 if base == "slide_back_up" else 1
        top = y - 3 * s
        path.moveTo(x - 5 * s, top - sign * 2 * s)
        path.cubicTo(QPointF(x - 4.5 * s, top + sign * 5 * s), QPointF(x + 1.5 * s, top + sign * 5 * s),
                     QPointF(x + 2.5 * s, top - sign * 1 * s))
        p.drawPath(path)
        _arrowhead(p, x + 2.5 * s, top - sign * 1.5 * s, -sign, s, color)
        return
    elif base == "curl_up":
        path.moveTo(x - 5 * s, y + 3 * s)
        path.quadTo(QPointF(x + 4 * s, y + 4 * s), QPointF(x + 4 * s, y - 4 * s))
        p.drawPath(path)
        p.drawLine(QPointF(x + 4 * s, y - 4 * s), QPointF(x + 1.5 * s, y - 1.5 * s))
        p.drawLine(QPointF(x + 4 * s, y - 4 * s), QPointF(x + 6 * s, y - 1 * s))
        return
    elif base == "curl_down":
        path.moveTo(x - 5 * s, y - 3 * s)
        path.quadTo(QPointF(x + 4 * s, y - 4 * s), QPointF(x + 4 * s, y + 4 * s))
        p.drawPath(path)
        p.drawLine(QPointF(x + 4 * s, y + 4 * s), QPointF(x + 1.5 * s, y + 1.5 * s))
        p.drawLine(QPointF(x + 4 * s, y + 4 * s), QPointF(x + 6 * s, y + 1 * s))
        return
    elif base in ("tilde", "vibrato"):  # one period, like the printed ～ / \jsErhuVibrato
        _pen(p, color, 1.4, s)
        _wave(path, x - 6.5 * s, x + 6.5 * s, y, s, 2.6, 13)
    elif base == "arc":
        path.moveTo(x - 6 * s, y + 3 * s)
        path.cubicTo(x - 5 * s, y - 4 * s, x + 5 * s, y - 4 * s, x + 6 * s, y + 3 * s)
    elif base == "caesura":
        _pen(p, color, 1.5, s)
        for dx in (-2.2, 2.2):
            p.drawLine(QPointF(x + dx * s - 2 * s, y + 5 * s), QPointF(x + dx * s + 2 * s, y - 5 * s))
        return
    elif base == "plus":
        path.moveTo(x - 4.5 * s, y)
        path.lineTo(x + 4.5 * s, y)
        path.moveTo(x, y - 4.5 * s)
        path.lineTo(x, y + 4.5 * s)
    elif base == "double_tuo":
        qfont = font("cjk", 10 * s, variant)
        _text(p, "∟", x, y - 3.5 * s, qfont, color)
        _text(p, "∟", x, y + 3.5 * s, qfont, color)
        return
    elif base == "circled":
        p.drawEllipse(QPointF(x, y), 6.5 * s, 6.5 * s)
        _text(p, arg, x, y + 0.3 * s, font("cjk", 9 * s, variant), color)
        return
    elif base == "sanban":
        _pen(p, color, 1.3, s)
        p.drawLine(QPointF(x - 6 * s, y - 1 * s), QPointF(x + 6 * s, y - 1 * s))
        p.drawLine(QPointF(x - 2.5 * s, y - 6 * s), QPointF(x - 2.5 * s, y + 5 * s))
        p.drawLine(QPointF(x + 2.5 * s, y - 6 * s), QPointF(x + 2.5 * s, y + 5 * s))
        return
    elif base == "duo":
        _pen(p, color, 1.2, s)
        p.drawLine(QPointF(x - 6 * s, y - 5 * s), QPointF(x + 2 * s, y - 5 * s))
        p.drawLine(QPointF(x - 6 * s, y - 2 * s), QPointF(x + 2 * s, y - 2 * s))
        path.moveTo(x + 2 * s, y - 6 * s)
        path.lineTo(x + 5 * s, y - 2 * s)
        path.lineTo(x + 1 * s, y + 6 * s)
        p.drawPath(path)
        p.drawLine(QPointF(x + 1 * s, y + 6 * s), QPointF(x + 1 * s, y + 3 * s))
        p.drawLine(QPointF(x + 1 * s, y + 6 * s), QPointF(x + 4 * s, y + 5 * s))
        return
    elif base == "stroke_bars":
        count = int(arg or 3)
        _pen(p, color, 1.2, s)
        p.drawLine(QPointF(x - 4 * s, y + 7 * s), QPointF(x + 4 * s, y - 7 * s))
        for i in range(count):
            dy = (i - (count - 1) / 2) * 3.4 * s
            cx, cy = x - dy * 0.55, y + dy
            p.drawLine(QPointF(cx - 4 * s, cy), QPointF(cx + 4 * s, cy))
        return
    elif base == "leaves":
        count = int(arg or 3)
        p.setBrush(color)
        _pen(p, color, 0.8, s)
        for i in range(count):
            angle = -math.pi / 2 + (i - (count - 1) / 2) * 0.75
            cx, cy = x + 7.5 * s * math.cos(angle), y + 4 * s + 7.5 * s * math.sin(angle)
            leaf = QPainterPath(QPointF(x, y + 4 * s))
            leaf.quadTo(QPointF(cx + 3 * s * math.cos(angle + 1.2), cy + 3 * s * math.sin(angle + 1.2)), QPointF(cx, cy))
            leaf.quadTo(QPointF(cx + 3 * s * math.cos(angle - 1.2), cy + 3 * s * math.sin(angle - 1.2)), QPointF(x, y + 4 * s))
            p.drawPath(leaf)
        return
    elif base == "circle_arrow_right":
        p.drawEllipse(QPointF(x - 2 * s, y), 4 * s, 4 * s)
        _arrow(p, x + 2 * s, y, x + 8 * s, y, s, color, 2.5)
        return
    elif base == "vbar_2cross":
        _pen(p, color, 1.3, s)
        p.drawLine(QPointF(x, y - 7 * s), QPointF(x, y + 7 * s))
        for dy in (-2.5, 2.5):
            p.drawLine(QPointF(x - 4 * s, y + dy * s + 1.5 * s), QPointF(x + 4 * s, y + dy * s - 1.5 * s))
        return
    else:   # an unknown shape name still shows something
        _text(p, symbol.display_name[:1], x, y, font("cjk", 11 * s, variant), color)
        return
    p.drawPath(path)


def _ornament_base(p, kind, x, y, s, color):
    _pen(p, color, 1.3, s)
    if kind == "trill":
        _text(p, "tr", x, y, font("italic", 13 * s), color)
        p.setFont(font("italic", 13 * s))
        return
    path = QPainterPath()
    width = 12 if kind != "prallprall" else 18
    x1 = x - width / 2 * s
    path.moveTo(x1, y + 1.5 * s)
    steps = 4 if kind != "prallprall" else 6
    for i in range(1, steps + 1):
        path.lineTo(x1 + i * width / steps * s, y + (-2.5 if i % 2 else 1.5) * s)
    p.drawPath(path)
    if kind == "mordent":
        p.drawLine(QPointF(x, y - 5 * s), QPointF(x, y + 5 * s))


# ---------------------------------------------------------------- bar items
def draw_segno(p, x, y, s, color):
    _pen(p, color, 1.4, s)
    path = QPainterPath(QPointF(x + 3.5 * s, y - 4.5 * s))
    path.cubicTo(QPointF(x + 1 * s, y - 7.5 * s), QPointF(x - 5 * s, y - 5 * s), QPointF(x - 2 * s, y - 1 * s))
    path.lineTo(QPointF(x + 2 * s, y + 1 * s))
    path.cubicTo(QPointF(x + 5 * s, y + 5 * s), QPointF(x - 1 * s, y + 7.5 * s), QPointF(x - 3.5 * s, y + 4.5 * s))
    p.drawPath(path)
    p.drawLine(QPointF(x - 5 * s, y + 6 * s), QPointF(x + 5 * s, y - 6 * s))
    p.setBrush(color)
    p.drawEllipse(QPointF(x - 4.5 * s, y - 2 * s), 1.2 * s, 1.2 * s)
    p.drawEllipse(QPointF(x + 4.5 * s, y + 2 * s), 1.2 * s, 1.2 * s)
    p.setBrush(Qt.BrushStyle.NoBrush)


def draw_coda(p, x, y, s, color):
    _pen(p, color, 1.4, s)
    p.drawEllipse(QPointF(x, y), 4.2 * s, 5.2 * s)
    p.drawLine(QPointF(x - 7 * s, y), QPointF(x + 7 * s, y))
    p.drawLine(QPointF(x, y - 8 * s), QPointF(x, y + 8 * s))


def nav_text(symbol) -> str:
    """Printed text of a navigation item ("" for pure signs)."""
    text = symbol.canvas_text()
    if symbol.id == "nav.tocoda":
        return "To Coda"
    if symbol.id == "nav.dc":
        return "D.C."
    return text.replace(" ⊕", "")


def draw_nav_text(p, text, x, y, s, color, align="left") -> float:
    """Italic navigation words (jump texts, "Coda"), the style the print uses."""
    qfont = font("italic", 13 * s)
    width = QFontMetricsF(qfont).horizontalAdvance(text)
    left = x - width / 2 if align == "center" else x if align == "left" else x - width
    _text(p, text, left, y, qfont, color, "left")
    return width


def draw_nav(p, symbol, x, y, s, color, align="center") -> float:
    """Draw a navigation item at (x, y); returns the width used."""
    sign = symbol.canvas.startswith("shape:")
    if symbol.id in ("nav.segno", "nav.segno_sign"):
        draw_segno(p, x, y, s, color)
        return 14 * s
    if symbol.id == "nav.coda_sign":
        draw_coda(p, x, y, s, color)
        return 16 * s
    text = nav_text(symbol)
    qfont = font("italic", 13 * s)
    width = QFontMetricsF(qfont).horizontalAdvance(text)
    coda = symbol.id in ("nav.tocoda", "nav.tocoda_text")
    total = width + (16 * s if coda else 0)
    left = x - total / 2 if align == "center" else x if align == "left" else x - total
    _text(p, text, left, y, qfont, color, "left")
    if coda:
        draw_coda(p, left + width + 9 * s, y, s * 0.8, color)
    del sign
    return total


def draw_barline(p, style: str, x: float, top: float, bottom: float, s: float, color: QColor,
                 dot_rows=(), edge: str = "") -> None:
    """style: single | dashed | double | final | repeat_start | repeat_end | repeat_both.

    ``dot_rows`` are the vertical centres where repeat dots go (one pair per part).
    For final/repeat lines ``x`` is the outer (thick) line; the thin one is inside.
    """
    thin = max(0.8, 1.1 * s)
    thick = 3.4 * s
    if style == "single":
        _pen(p, color, 1.1, 1)
        p.setPen(QPen(color, thin))
        p.drawLine(QPointF(x, top), QPointF(x, bottom))
        return
    if style == "dashed":
        pen = QPen(color, thin)
        pen.setDashPattern([4, 3])
        p.setPen(pen)
        p.drawLine(QPointF(x, top), QPointF(x, bottom))
        return
    if style == "double":
        p.setPen(QPen(color, thin))
        p.drawLine(QPointF(x - 2 * s, top), QPointF(x - 2 * s, bottom))
        p.drawLine(QPointF(x + 2 * s, top), QPointF(x + 2 * s, bottom))
        return
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    direction = 1 if style == "repeat_start" else -1   # thin line and dots go right of a start
    if style in ("final", "repeat_end"):
        p.drawRect(QRectF(x - thick, top, thick, bottom - top))
        p.setPen(QPen(color, thin))
        p.drawLine(QPointF(x - thick - 3 * s, top), QPointF(x - thick - 3 * s, bottom))
    elif style == "repeat_start":
        p.drawRect(QRectF(x, top, thick, bottom - top))
        p.setPen(QPen(color, thin))
        p.drawLine(QPointF(x + thick + 3 * s, top), QPointF(x + thick + 3 * s, bottom))
    elif style == "repeat_both":
        p.drawRect(QRectF(x - thick / 2, top, thick, bottom - top))
        p.setPen(QPen(color, thin))
        p.drawLine(QPointF(x - thick / 2 - 3 * s, top), QPointF(x - thick / 2 - 3 * s, bottom))
        p.drawLine(QPointF(x + thick / 2 + 3 * s, top), QPointF(x + thick / 2 + 3 * s, bottom))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    if style in ("repeat_start", "repeat_end", "repeat_both"):
        sides = (1, -1) if style == "repeat_both" else (direction,)
        for side in sides:
            dot_x = x + side * (thick + 7.5 * s if style != "repeat_both" else thick / 2 + 7.5 * s)
            for cy in dot_rows:
                for dy in (-5, 5):
                    p.drawEllipse(QPointF(dot_x, cy + dy * s), 1.8 * s, 1.8 * s)
    p.setBrush(Qt.BrushStyle.NoBrush)


# ---------------------------------------------------------------- spans
def hairpin_path(x1, x2, y, s, first_open, last_open) -> QPainterPath:
    path = QPainterPath()
    for side in (-1, 1):
        path.moveTo(x1, y + side * 6 * s * first_open)
        path.lineTo(x2, y + side * 6 * s * last_open)
    return path


def arc_path(x1, x2, y, lift) -> QPainterPath:
    path = QPainterPath(QPointF(x1, y))
    path.cubicTo(x1 + (x2 - x1) * 0.25, y - lift, x1 + (x2 - x1) * 0.75, y - lift, x2, y)
    return path


def wave_path(x1, x2, y, s) -> QPainterPath:
    path = QPainterPath()
    _wave(path, x1, x2, y, s, 2.0, 6)
    return path


def draw_span_label(p, symbol, x, y, s, color, variant="tc") -> float:
    """The text/sign at a line's start (8va, tr, rit., circle); returns its width."""
    if symbol.family == "ottava":
        return _text(p, "8va" if symbol.id == "line.8va" else "8vb", x, y, font("italic", 12 * s), color, "left")
    if symbol.family == "trill" and symbol.id != "line.wavy":
        if symbol.id == "hulusi.xuzhi":
            _pen(p, color, 1.2, s)
            p.drawEllipse(QPointF(x + 4 * s, y), 3.5 * s, 3.5 * s)
            return 10 * s
        return _text(p, "tr", x, y, font("italic", 13 * s), color, "left") + 2 * s
    if symbol.family == "text":
        return _text(p, symbol.canvas_text(variant).split(" ")[0], x, y, font("italic", 13 * s), color, "left") + 3 * s
    if symbol.family == "hairpin" and symbol.canvas.startswith("text:"):
        return _text(p, symbol.canvas_text(variant), x, y, font("italic", 13 * s), color, "left") + 3 * s
    return 0.0


# ---------------------------------------------------------------- palette previews
def symbol_preview(p: QPainter, symbol, rect: QRectF, ink: QColor, faint: QColor, s: float = 1.0,
                   variant: str = "tc", compact: bool = False) -> None:
    """The cell picture: the mark on a faint 5 (or 5 6 / a bar line), drawn like the score."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    cx, cy = rect.center().x(), rect.center().y()
    number = font("number", 17 * s)
    kind = symbol.kind
    if symbol.id in ("line.tie", "tuplet.3"):
        kind = "span"
    if kind == "bar":
        _preview_bar(p, symbol, rect, ink, faint, s, variant)
    elif kind == "span":
        x1, x2 = cx - 9 * s, cx + 9 * s
        ny = cy + 6 * s
        digits = ("5", "5") if symbol.id == "line.tie" else ("5", "3") if symbol.family == "gliss" else ("5", "6")
        if symbol.id != "tuplet.3":
            _text(p, digits[0], x1, ny, number, faint)
            _text(p, digits[1], x2, ny, number, faint)
        _preview_span(p, symbol, x1, x2, ny, s, ink, faint, variant)
    elif kind == "composite" and symbol.canvas.startswith("text:"):     # text.free: words above a 5
        _text(p, "5", cx, cy + 6 * s, number, faint)
        _text(p, symbol.canvas_text(variant), cx, cy - 10 * s, font("cjk", 11.5 * s, variant), ink)
    elif kind == "composite":
        _text(p, "5", cx + 2 * s, cy + 5 * s, number, faint)
        _text(p, "6", cx + 12 * s, cy - 2 * s, font("number", 10 * s), ink)
    else:
        where = position(symbol)
        lifted = where == "below"
        _text(p, "5", cx + (4 * s if where == "left" else -4 * s if where == "right" else 0),
              cy + (-5 * s if lifted else 6 * s), number, faint)
        if where == "left":
            draw_mark(p, symbol, cx - 9 * s, cy + 3 * s, s, ink, variant)
        elif where == "right":
            draw_mark(p, symbol, cx + 8 * s, cy - 2 * s, s, ink, variant)
        elif where == "below":
            draw_mark(p, symbol, cx, cy + 12 * s, s * (0.85 if len(symbol.canvas_text()) > 3 else 1), ink, variant)
        else:
            h = mark_height(symbol)
            draw_mark(p, symbol, cx, cy + 6 * s - (12 + h / 2) * s, s, ink, variant)
    p.restore()


def _preview_span(p, symbol, x1, x2, y, s, ink, faint, variant):
    if symbol.family == "hairpin":
        if symbol.canvas.startswith("text:"):
            draw_span_label(p, symbol, x1 - 6 * s, y + 13 * s, s * 0.85, ink, variant)
            return
        _pen(p, ink, 1.2, s)
        opening = (0, 1) if symbol.id == "hairpin.cresc" else (1, 0)
        p.drawPath(hairpin_path(x1 - 4 * s, x2 + 4 * s, y + 14 * s, s * 0.7, *opening))
    elif symbol.family in ("slur",) or symbol.id == "line.tie":
        _pen(p, ink, 1.3, s)
        p.drawPath(arc_path(x1 - 1 * s, x2 + 1 * s, y - 11 * s, 6 * s))
    elif symbol.id == "tuplet.3":
        _pen(p, ink, 1.1, s)
        top = y - 16 * s
        p.drawLine(QPointF(x1 - 4 * s, top + 4 * s), QPointF(x1 - 4 * s, top))
        p.drawLine(QPointF(x1 - 4 * s, top), QPointF(x2 + 4 * s, top))
        p.drawLine(QPointF(x2 + 4 * s, top), QPointF(x2 + 4 * s, top + 4 * s))
        _text(p, "3", (x1 + x2) / 2, top - 1 * s, font("italic", 11 * s), ink)
        number = font("number", 13 * s)
        for x in (x1 - 2 * s, (x1 + x2) / 2, x2 + 2 * s):
            _text(p, "5", x, y + 1 * s, number, faint)
    elif symbol.family == "gliss":
        _pen(p, ink, 1.3, s)
        p.drawLine(QPointF(x1 + 5 * s, y + 3 * s), QPointF(x2 - 5 * s, y - 5 * s))
    elif symbol.family in ("ottava", "trill", "text"):
        label_y = y - 18 * s if symbol.id != "line.8vb" else y + 16 * s
        width = draw_span_label(p, symbol, x1 - 8 * s, label_y, s * 0.9, ink, variant)
        start = x1 - 8 * s + width + 1 * s
        if symbol.family == "trill":
            _pen(p, ink, 1.2, s)
            p.drawPath(wave_path(start if symbol.id != "line.wavy" else x1 - 8 * s, x2 + 8 * s, label_y, s))
        else:
            pen = QPen(ink, max(0.6, 1.0 * s))
            pen.setDashPattern([3, 3])
            p.setPen(pen)
            p.drawLine(QPointF(start, label_y), QPointF(x2 + 8 * s, label_y))
            if symbol.family == "ottava":
                p.setPen(QPen(ink, max(0.6, 1.0 * s)))
                hook = 4 * s if symbol.id == "line.8va" else -4 * s
                p.drawLine(QPointF(x2 + 8 * s, label_y), QPointF(x2 + 8 * s, label_y + hook))


def _preview_bar(p, symbol, rect, ink, faint, s, variant):
    cx, cy = rect.center().x(), rect.center().y()
    top, bottom = cy - 6 * s, cy + 16 * s
    if symbol.category == "navigation":
        p.setPen(QPen(faint, max(0.8, 1.1 * s)))
        p.drawLine(QPointF(cx + 10 * s, top + 2 * s), QPointF(cx + 10 * s, bottom))
        draw_nav(p, symbol, cx, cy - 6 * s, s * (0.85 if len(nav_text(symbol)) > 6 else 1), ink)
        return
    if symbol.id.startswith("volta."):
        label = {"volta.final": tr_jie(variant)}.get(symbol.id, symbol.id.split(".")[1] + ".")
        _pen(p, ink, 1.1, s)
        y0 = cy - 11 * s
        p.drawLine(QPointF(cx - 12 * s, y0 + 8 * s), QPointF(cx - 12 * s, y0))
        p.drawLine(QPointF(cx - 12 * s, y0), QPointF(cx + 14 * s, y0))
        _text(p, label, cx - 9 * s, y0 + 6 * s, font("sans" if label[0].isdigit() else "cjk", 10 * s, variant), ink, "left")
        p.setPen(QPen(faint, max(0.8, 1.1 * s)))
        p.drawLine(QPointF(cx - 12 * s, top + 4 * s), QPointF(cx - 12 * s, bottom))
        return
    style = {"bar.dashed": "dashed", "bar.double": "double", "bar.final": "final", "bar.repeat_start": "repeat_start",
             "bar.repeat_end": "repeat_end", "bar.repeat_both": "repeat_both"}.get(symbol.id, "single")
    x = cx + (2 * s if style in ("final", "repeat_end") else -2 * s if style == "repeat_start" else 0)
    draw_barline(p, style, x, cy - 11 * s, cy + 11 * s, s, ink, dot_rows=(cy,))


def tr_jie(variant: str) -> str:
    """The 「結」 of a final volta, in the score's print script."""
    return "结" if variant == "sc" else "結"


def anchor_rest_ok(symbol) -> bool:
    return symbol.on_rest or symbol.id in symbols.LEGACY_REST_MARKS
