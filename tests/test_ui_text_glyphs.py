"""Every user-visible string uses only characters the UI fonts have on Windows, macOS and Linux.

The UI fonts are CJK system fonts (Microsoft JhengHei/YaHei, PingFang, Noto Sans CJK) plus
Qt's symbol fallback; a character outside this allow-list (for example 𝄋 U+1D10B, which
showed as a blank gap on macOS) is drawn by the app (glyphs.py) or named in words instead.
Checked against explicit code-point ranges, never against an installed font.
"""
import re

import pytest
from test_i18n import translated_literals

from jianpu_studio import symbols, symbols_ui
from jianpu_studio.i18n_zh_cn import CATALOG

ALLOWED_RANGES = (
    (0x0020, 0x007E),     # ASCII
    (0x00A0, 0x00FF),     # Latin-1: · × ° ½ ¼
    (0x0391, 0x03C9),     # Greek letters
    (0x2010, 0x2027),     # dashes, quotes, … (General Punctuation)
    (0x2030, 0x203B),     # ‰ ′ ″ ‹ › ※
    (0x2190, 0x2199),     # basic arrows ← ↑ → ↓ ↗ ↘
    (0x3000, 0x303F),     # CJK symbols and punctuation
    (0x3400, 0x4DBF), (0x4E00, 0x9FFF),   # CJK ideographs
    (0xFF01, 0xFF5E),     # fullwidth forms
)
ALLOWED_SYMBOLS = set("∧∨○●◇▼▽▲△■□♩♭♮♯✓⚠\n")


def allowed(c):
    o = ord(c)
    return c in ALLOWED_SYMBOLS or any(a <= o <= b for a, b in ALLOWED_RANGES)


def visible_texts():
    texts = set(translated_literals()) | set(CATALOG) | set(CATALOG.values())
    texts |= {s.display_name for s in symbols.SYMBOLS.values()} | {s.name for s in symbols.SYMBOLS.values()}
    texts |= set(symbols_ui.USAGE.values())
    return sorted(texts)


@pytest.mark.parametrize("text", visible_texts())
def test_text_has_only_renderable_characters_and_no_gaps(text):
    bad = [f"U+{ord(c):04X}" for c in text if not allowed(c)]
    assert not bad, (text, bad)
    assert not re.search(r"[（(]\s*[）)]", text), text          # no empty brackets left behind
    assert "  " not in text.strip("\n"), text                     # no double space where a glyph was
