"""CLI worker also available in the frozen executables (Windows/macOS)."""

import re
import sys
from pathlib import Path

from .engine import validate_source
from .model import atomic_write
from .musicxml import read_xml
from .vendor import jianpu_ly

# Pango/fontconfig family lists: the bundled Noto font comes first, then the
# usual system CJK fonts on Windows, macOS and Linux. The bundled Traditional
# Chinese Noto font contains the full CJK glyph set, so Simplified text never
# falls back to missing glyphs; system SC fonts are listed only as a last
# resort for developer installs without the bundled font.
SERIF_FONTS = ("Noto Serif CJK TC, Noto Serif TC, PMingLiU, MingLiU, Songti TC, STSong, "
               "Microsoft JhengHei, PingFang TC, Noto Serif CJK SC, SimSun, Songti SC, "
               "Microsoft YaHei, serif")
SANS_FONTS = ("Noto Sans CJK TC, Noto Sans TC, Microsoft JhengHei, PingFang TC, Heiti TC, "
              "Noto Sans CJK SC, Microsoft YaHei, PingFang SC, Heiti SC, sans-serif")

# jianpu-ly adds this block when "/Library/Fonts/Arial Unicode.ttf" exists on
# the converting machine (typical macOS). set-global-fonts was removed in
# LilyPond 2.25, and our own font settings below replace it on every version.
_LEGACY_FONT_BLOCK = re.compile(r"#\(define fonts\s*\(set-global-fonts\b")
_LEGACY_TEMPO = "tempoWholesPerMinute = #(ly:make-moment 84 4)"


def _remove_legacy_fonts(text: str) -> str:
    while match := _LEGACY_FONT_BLOCK.search(text):
        depth, end = 0, None
        for index in range(match.start() + 1, len(text)):
            depth += {"(": 1, ")": -1}.get(text[index], 0)
            if depth == 0:
                end = index + 1
                break
        if end is None:
            raise ValueError("Unbalanced jianpu-ly font block")
        text = text[:match.start()] + text[end:]
    return text


def adapt_output(result: str, minor_version: int, paper_size: str) -> str:
    """Make jianpu-ly's LilyPond text valid for the selected LilyPond version."""
    result = _remove_legacy_fonts(result)
    # jianpu-ly writes erhu fingering (一二三四, 宀 for the open string, ▼) as
    # \finger \markup. Fingering uses the music font (fetaText encoding), and
    # LilyPond 2.25+ no longer falls back to a text font for missing glyphs,
    # so route these markups through the CJK text fonts configured below.
    result = result.replace("\\finger \\markup { ", "\\finger \\markup { \\override #'(font-encoding . latin1) ")
    if minor_version >= 25:
        # 2.25+ expects an exact rational number of whole notes per minute
        # rather than a moment; 84 quarter notes = 21 whole notes. Without
        # this, LilyPond ignores the value (an explicit \tempo still applies).
        result = result.replace(_LEGACY_TEMPO, "tempoWholesPerMinute = #21 ")
    result += '\n\\paper { #(set-paper-size "' + paper_size + '") }\n'
    # property-defaults.fonts exists since 2.24 and replaces set-global-fonts.
    result += ('\\paper {\n  property-defaults.fonts.serif = "' + SERIF_FONTS + '"\n'
               '  property-defaults.fonts.sans = "' + SANS_FONTS + '"\n}\n')
    return result


def run(args: list[str]) -> int:
    try:
        operation, source, target, *extra = args
        if operation == "convert":
            data = Path(source).read_text(encoding="utf-8-sig")
            validate_source(data)
            minor = int(extra[0]) if extra else 26
            jianpu_ly._lilypond_minor_version = minor
            paper_size = extra[1] if len(extra) > 1 else "a4"
            staff_size = int(extra[2]) if len(extra) > 2 else 20
            if paper_size not in ("a4", "letter") or not 16 <= staff_size <= 30:
                raise ValueError("Unsupported page or staff size")
            jianpu_ly.staff_size = jianpu_ly.lyric_size = staff_size
            result = adapt_output(jianpu_ly.process_input(data), minor, paper_size)
        elif operation == "import":
            result = jianpu_ly.xml2jianpu(read_xml(Path(source)))
        else:
            raise ValueError("Unknown worker operation")
        atomic_write(Path(target), result)
        return 0
    except Exception as exc:
        # windowed PyInstaller builds may have no standard output stream.
        message = str(exc)
        if sys.stderr is not None:
            print(message, file=sys.stderr)
        if len(args) >= 3:
            atomic_write(Path(args[2]).with_suffix(".error.txt"), message)
        return 1
