"""CLI worker also available in the frozen executables (Windows/macOS)."""

import os
import re
import sys
from pathlib import Path

from . import symbols
from .engine import validate_source
from .model import DIRECTIVE, atomic_write
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


def read_options(source: str) -> dict:
    """Print options a visual score writes as its first line (``% jianpu-studio: print=sc sloppy-bars``)."""
    first = source.lstrip("\ufeff").split("\n", 1)[0]
    if not first.startswith(DIRECTIVE):
        return {}
    options = {}
    for word in first[len(DIRECTIVE):].split():
        key, _, value = word.partition("=")
        options[key] = value or True
    return {"variant": "sc" if options.get("print") == "sc" else "tc", "sloppy": options.get("sloppy-bars") is True}


def part_count(source: str) -> int:
    """Number of parts, split exactly like upstream (``\\sNextPart\\s``), ignoring comments."""
    text = "\n".join(line.split("%", 1)[0] for line in source.splitlines())
    return len(re.findall(r"(?<=\s)NextPart(?=\s)", " " + text + " ")) + 1


def insert_prelude(ly: str, variant: str = "tc", parts: int = 1) -> str:
    """App-owned macro definitions before the first \\score (tech.md §3.2a/d)."""
    index = ly.index("\\score {")
    return ly[:index] + symbols.prelude(variant, parts) + "\n" + ly[index:]


_FINE = "\\volta 2 \\fine \\volta 1"


def fix_fine(ly: str) -> str:
    """Brace the music after upstream's ``Fine`` (tech.md P2).

    jianpu-ly writes Fine as ``\\volta 2 \\fine \\volta 1`` followed by the rest of the
    block WITHOUT braces, so ``\\volta 1`` covers one note only: unfolded MIDI plays past
    Fine and LilyPond warns "found music after \\fine". Wrap the rest of the enclosing block,
    skipping strings and comments while counting braces.
    """
    position = 0
    while (index := ly.find(_FINE, position)) >= 0:
        start = index + len(_FINE)
        depth, k = 0, start
        while k < len(ly):
            if ly.startswith("%{", k):
                k = ly.index("%}", k) + 2
                continue
            c = ly[k]
            if c == '"':
                k = ly.index('"', k + 1) + 1
                continue
            if c == "%":
                k = ly.index("\n", k)
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                if depth == 0:
                    break
                depth -= 1
            k += 1
        ly = ly[:start] + " {" + ly[start:k] + "} " + ly[k:]
        position = start
    return ly


# 8va/8vb sound ±12 in MIDI. Upstream places the line words wherever the notes put them --
# inside a \times or \afterGrace block when the line starts or ends there -- so the rewrite
# must not open or close braces (that re-nests tuplets and grace blocks). \transposition is a
# context setting: it changes the sounding pitch from that moment on without any nesting.
_OTTAVA_MIDI = {"\\jsOttavaUp": "\\transposition c''", "\\jsOttavaDown": "\\transposition c",
                "\\jsOttavaEnd": "\\transposition c'"}


_SEGNO_BEFORE_KEY = re.compile(r"\\repeat segno (\d+) \{ (\\transpose \S+ \S+ \{)")


def midi_rewrite(ly: str) -> str:
    """Inside MIDI staves only: 8va/8vb sound transposed; sforzandi become velocity-only twins.

    A D.C. return starts at the first note, so upstream opens ``\\repeat segno`` before the
    MIDI staff's key wrapper ``\\transpose c X {``; the jump's closing brace then closes the
    key wrapper instead, and every bar after the D.C. joined the repeated body (played on
    both passes). Opening the key wrapper first restores the nesting the braces were written for.
    """
    twins = "|".join(sorted(symbols.midi_twins(), key=len, reverse=True))
    def fix(match):
        text = _SEGNO_BEFORE_KEY.sub(r"\2 \\repeat segno \1 {", match.group(0))
        for word, replacement in _OTTAVA_MIDI.items():
            text = text.replace(word, replacement)
        return re.sub(r"\\(" + twins + r")(?![A-Za-z])", r"\\\1Midi", text) if twins else text
    return re.sub(r"(?s)% === BEGIN MIDI STAFF ===.*?% === END MIDI STAFF ===", fix, ly)


def finish_output(ly: str, variant: str = "tc", parts: int = 1) -> str:
    """tech.md §3.2 a–d, after ``adapt_output``."""
    return midi_rewrite(fix_fine(insert_prelude(ly, variant, parts)))


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
            options = read_options(data)
            # Upstream reads this variable while converting: a pickup with a full last bar (§3.2e).
            previous = os.environ.pop("j2ly_sloppy_bars", None)
            if options.get("sloppy"):
                os.environ["j2ly_sloppy_bars"] = "1"
            try:
                result = adapt_output(jianpu_ly.process_input(data), minor, paper_size)
            finally:
                os.environ.pop("j2ly_sloppy_bars", None)
                if previous is not None:
                    os.environ["j2ly_sloppy_bars"] = previous
            result = finish_output(result, options.get("variant", "tc"), part_count(data))
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
