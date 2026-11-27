"""Score -> jianpu-ly text (wow/tech.md §3.1). Reference: work/probes/notation/proto_export.py.

Bar items live on parts[0]; every part writes the structural ones (R{ } A{ | } Segno ToCoda
DS DC Fine) at the same bars, because upstream builds each part's \\repeat from its own
braces. Print-only marks (\\textMark/\\textEndMark) are Score-level and written once (part 0).
"""
from __future__ import annotations

import re

from . import symbols
from .i18n import tr
from .notation_rules import bar_items

HEADER_LIKE = re.compile(r"[A-Za-z]+=")     # upstream would swallow such a line as a header (P4)
JUMP_WORDS = {"nav.tocoda": "ToCoda", "nav.ds": "DS", "nav.dc": "DC"}


def upstream_normalized(text: str) -> str:
    """What jianpu-ly makes of a line before parsing it (its ``fix_fullwidth``):
    fullwidth ASCII forms U+FF01–FF5E become ASCII, U+201A a comma, U+FF61 a full stop."""
    return text.translate(_FULLWIDTH)


_FULLWIDTH = {code: code - 0xFEE0 for code in range(0xFF01, 0xFF5F)} | {0x201A: ",", 0xFF61: "."}
# Lookalikes outside U+FF01–FF5E (so upstream leaves them alone); all are in Big5 and GB 18030.
_SAFE = {'"': "”", "\\": "﹨", "{": "﹛", "}": "﹜", "$": "﹩", "%": "﹪", "#": "﹟"}
_HEADER_SAFE = {k: v for k, v in _SAFE.items() if k in '"\\{}$'}
# Words upstream finds anywhere in the input: part/score separators, MIDI-per-part and
# Indonesian-notation switches, and the LP:/LPH: code markers ``validate_source`` refuses on
# any line. A zero-width space keeps them from matching (it prints nothing).
_RESERVED = re.compile(r"(?<=Next)(?=Part|Score)|(?<=Part)(?=Midi)|(?<=ang)(?=ka)|(?<=Indo)(?=nesian)"
                       r"|(?<=LP)(?=H?:)")
_OPEN_QUOTES = "‘“《"


def _is_hanzi(c: str) -> bool:
    return 0x3400 <= ord(c) < 0xA700        # upstream's test (H: lines)


def _syllables(text: str) -> list[str]:
    """Split an H: lyric line where upstream will (whitespace; a hanzi or opening quote after a
    hanzi), keeping ``_`` joins (upstream removes them and keeps the hanzi on one note)."""
    out, current, need = [], [], False
    for c in text:
        if c.isspace() or (need and (_is_hanzi(c) or c in _OPEN_QUOTES)):
            if current:
                out.append("".join(current))
                current = []
            if not c.isspace():
                need = False
            else:
                continue
        if _is_hanzi(c):
            need = True
        if c == "_":
            need = False
        current.append(c)
    if current:
        out.append("".join(current))
    return out


def _lyric_word(syllable: str) -> str:
    """A syllable LilyPond reads as text. Digits end a lyric word (``春2`` would become the
    syllable 春 and a duration 2) and ``* . = |`` start syntax, so such syllables are quoted;
    the leading ``_`` (removed by upstream) keeps upstream from spacing inside the quotes."""
    text = syllable.replace("_", "")
    if not any(c.isdigit() for c in text) and not text.startswith(("*", ".", "=", "|")):
        return syllable
    hyphen = "-" if syllable.endswith("-") and syllable.strip("-") else ""
    return '_"' + syllable[:len(syllable) - len(hyphen)] + '"' + hyphen


def literal(text: str, *, lyric: bool = False) -> str:
    """User text (title, composer, part name, lyrics) as literal text, never LilyPond/Scheme.

    The text is first normalized the way upstream will (fullwidth ＂ would become a real
    quote), then characters LilyPond or the converter treats as syntax are replaced by
    lookalikes. Header values sit inside a LilyPond string, where only ``"`` and ``\\``
    matter (plus what ``validate_source`` refuses: braces, ``$``, ``#(``). Lyrics are
    lyric-mode words, where comments and Scheme start anywhere and digits are durations.
    """
    text = upstream_normalized(text)
    if lyric:
        text = "".join(_SAFE.get(c, c) for c in text)
        syllables = _syllables(text)
        words = [_lyric_word(s) for s in syllables]
        if words != syllables:
            text = " ".join(words)
    else:
        text = "".join(_HEADER_SAFE.get(c, c) for c in text)
        text = re.sub(r"#(?=\s*[({])", "﹟", text)
    return _RESERVED.sub("​", text)


# The converter keeps a quoted text in one word only across ASCII spaces; any other
# whitespace (U+3000, NBSP, figure space\u2026) would split it into broken words. Such a space is
# written as ASCII spaces of about its printed width (LilyPond keeps runs of spaces in text;
# one ASCII space is about a quarter em in the print font).
_SPACE_WIDTH = {"\u3000": 4, "\u2001": 4, "\u2003": 4, "\u2000": 2, "\u2002": 2, "\u2007": 2}


def note_text(text: str) -> str:
    """Text above a note inside ``^"..."``: every character jianpu-ly or LilyPond would read as
    syntax (quotes, backslash, braces, $, #, % which starts a comment) becomes a lookalike,
    and every non-ASCII space becomes ASCII spaces (see ``_SPACE_WIDTH``)."""
    text = "".join(" " * _SPACE_WIDTH.get(c, 1) if c.isspace() else _SAFE.get(c, c)
                   for c in upstream_normalized(text))
    return _RESERVED.sub("\u200b", text)


def grace_source(value: str) -> str:
    """Translate the UI's octave suffixes to unambiguous upstream prefixes.

    Upstream's suffix normalization can move the next note's accidental to
    the previous pitch (e.g. 1#2'). Prefixes with OctavesBefore avoid that path.
    """
    return "".join(accidental + octave + degree for accidental, degree, octave
                   in re.findall(r"([#b]?)([1-7])('{1,2}|,{1,2})?", value))


def pickup_token(pickup: int) -> str:
    if not pickup:
        return ""
    for d in (1, 2, 4, 8, 16, 32):
        base = 4 * 48 // d
        if pickup == base:
            return f",{d}"
        if pickup == base * 3 // 2:
            return f",{d}."
    raise ValueError(tr("弱起長度須為單一時值或附點時值，且短於一小節。"))


def _post_first(mark: str) -> tuple:
    return (not mark.startswith(("finger.", "string.")), symbols.ORDER[mark])


def note_words(note, ctx: dict) -> list[str]:
    """Words for one note; ``ctx`` carries the bar context (see ``to_jianpu``)."""
    reg = symbols.SYMBOLS
    first_part = ctx.get("part", 0) == 0
    starts_bar, ends_bar = ctx.get("bar_start", note.bar_start), ctx.get("bar_end", note.bar_end)
    if ctx.get("draft"):
        return _draft_words(note, ctx)
    w: list[str] = []
    if ctx.get("implicit_repeat"):
        w.append("R{")      # a lone :‖ repeats from the beginning (nothing printed at the start)
    # 1. bar-start items
    for item in starts_bar:
        if item == "bar.repeat_start":
            w.append("R{")
            if ctx.get("bar_index") == 0:   # LilyPond omits |: at the very start; jianpu prints it (P6)
                w += ["\\bar", '".|:"']
        elif item == "volta.1":
            w += ["}", "A{"]
        elif item.startswith("volta.") and item[6:].isdigit():
            w.append("|")
        elif item == "volta.final":
            w.append("\\jsVoltaJie")
        elif item == "nav.segno":
            w.append("Segno")
        elif first_part and reg[item].print_only:
            w.append(reg[item].bar_word())
    # 2.-5. tuplet, pre lines, grace, pre marks
    if note.tuplet_start:
        w.append("3[")
    for sid in note.spans_start:
        slot, word = reg[sid].span_words(False)
        if slot == "pre":
            w.append(word)
    if note.grace_before:
        w.append("g[" + grace_source(note.grace_before) + "]")
    marks = sorted(note.marks, key=_post_first)
    words = {m: reg[m].words() for m in marks}
    w += [word for m in marks for slot, word in [words[m]] if slot == "pre"]
    # 6. note head (a long note is a head plus quarter-note dashes; marks attach to the head)
    head, *continuation = note.token().split()
    if any(slot == "tok" for slot, _ in words.values()):
        head += next(word for slot, word in words.values() if slot == "tok")
    w.append(head)
    # 7. post marks, then line ends, then line starts
    w += [word for m in marks for slot, word in [words[m]] if slot == "post"]
    for ids, end in ((note.spans_end, True), (note.spans_start, False)):
        for sid in ids:
            slot, word = reg[sid].span_words(end)
            if slot == "post":
                w.append(word)
    if note.text:
        # Free text above the note (中速、深情地、引子…), as a literal string (never markup).
        # On the very first note it is a tempo word: the worker moves it into the header line.
        w.append('^"' + ("\u2060" if ctx.get("head_text") else "") + note_text(note.text) + '"')
    # 8.-13. dashes, after-grace, tie, after marks, after line ends, tuplet end
    w += continuation
    if note.grace_after:
        w.append("[" + grace_source(note.grace_after) + "]g")
    if note.tie:
        w.append("~")
    w += [word for m in marks for slot, word in [words[m]] if slot == "after"]
    for sid in note.spans_end:
        slot, word = reg[sid].span_words(True)
        if slot == "after":
            w.append(word)
    if note.tuplet_end:
        w.append("]")
    # 14. bar-end items
    next_starts = ctx.get("next_bar_start", ())
    for item in ends_bar:
        if item in symbols.LINE_STYLES:
            if "bar.repeat_start" in next_starts or (item == "bar.final" and ctx.get("last")):
                continue      # upstream R{ regex bug (P3) / automatic final bar line
            w.append(reg[item].bar_word())
    if "bar.repeat_end" in ends_bar and "volta.1" not in next_starts:
        w.append("}")
    if "volta.end" in ends_bar:
        w.append("}")
    for item in ends_bar:
        if item in JUMP_WORDS:
            w.append(JUMP_WORDS[item])
        elif item == "nav.fine":
            if ctx.get("has_jump"):
                w.append("Fine")
            elif first_part:
                w.append("\\jsFineText")     # Fine alone prints nothing upstream (P5)
        elif first_part and reg[item].print_only:
            w.append(reg[item].bar_word())
    return w


_DRAFT_VOLTA = {"volta.1": "\\jsDraftVoltaOne", "volta.2": "\\jsDraftVoltaTwo", "volta.3": "\\jsDraftVoltaThree",
                "volta.4": "\\jsDraftVoltaFour"}
_DRAFT_END = {"nav.tocoda": "nav.tocoda_text", "nav.ds": "nav.ds_text", "nav.dc": "nav.dc_text", "nav.fine": "nav.fine_text"}


def _draft_words(note, ctx) -> list[str]:
    """``note_words`` for a draft print: every repeat, ending and jump is printed as a plain
    sign (bar line glyph, bracket, text) and none of them repeats or jumps."""
    reg = symbols.SYMBOLS
    first_part = ctx.get("part", 0) == 0
    starts, ends = ctx.get("bar_start", ()), ctx.get("bar_end", ())
    pre = []
    for item in starts:
        if item == "bar.repeat_start":
            pre += ["\\bar", '".|:"']
        elif (item in _DRAFT_VOLTA or item == "volta.final" and not any(v in starts for v in _DRAFT_VOLTA)) \
                and first_part:
            # 結 alone (a loaded score may have it without a number) prints its own bracket
            word = "\\jsDraftVoltaJie" if "volta.final" in starts else _DRAFT_VOLTA[item]
            pre.append(word + ("Next" if ctx.get("volta_open") else ""))
        elif item == "nav.segno" and first_part:
            pre.append(reg["nav.segno_sign"].bar_word())
        elif item not in ("volta.final",) and item not in _DRAFT_VOLTA and item != "nav.segno" \
                and first_part and reg[item].print_only:
            pre.append(reg[item].bar_word())
    plain = dict(ctx, draft=False, bar_start=(), bar_end=(), implicit_repeat=False)
    w = pre + note_words(note, plain)
    for item in ends:
        if item in symbols.LINE_STYLES and not (item == "bar.final" and ctx.get("last")):
            if not {"bar.repeat_end", "bar.repeat_both"} & set(ends):
                w.append(reg[item].bar_word())
        elif item == "bar.repeat_end":
            w += ["\\bar", '":|."']
        elif item == "bar.repeat_both":
            w += ["\\bar", '":|.|:"']
        elif item == "volta.end" and first_part:
            w.append("\\jsDraftVoltaEnd")
        elif item in _DRAFT_END and first_part:
            w.append(reg[_DRAFT_END[item]].bar_word())
        elif item not in _DRAFT_END and item != "volta.end" and first_part and reg[item].print_only:
            w.append(reg[item].bar_word())
    if ctx.get("volta_close") and first_part:
        w.append("\\jsDraftVoltaEnd")     # an ending left open closes with the score (else LilyPond drops it)
    return w


def effective_bar_items(score) -> list[tuple[tuple, tuple]]:
    """parts[0] bar items with a both-ways repeat split into an end and the next bar's start."""
    items = bar_items(score)
    out = []
    for bar, (starts, ends) in enumerate(items):
        if bar and "bar.repeat_both" in items[bar - 1][1]:
            starts = symbols.sort_ids([*starts, "bar.repeat_start"])
        ends = tuple("bar.repeat_end" if item == "bar.repeat_both" else item for item in ends)
        out.append((starts, ends))
    return out


def header_words(text: str) -> bool:
    """Text on the first note that prints in the header line (1=D 中速 稍自由 ♩=72): only
    when every word is a tempo/expression word or a tuning; any other label stays on its note."""
    from .text_words import TEXT_WORDS
    known = {t for tc, sc, _ in TEXT_WORDS for w in (tc, sc) for t in w.split()}
    words = text.split()
    return bool(words) and all(w in known for w in words)


def implicit_repeat_start(items) -> bool:
    """True when the first repeat has only its end :‖ -- it repeats from the beginning."""
    for starts, ends in items:
        if "bar.repeat_start" in starts:
            return False
        if "bar.repeat_end" in ends:
            return True
    return False


def to_jianpu(score, draft: bool = False) -> str:
    """Export a validated score (``Score.to_jianpu`` checks it first); ``draft`` prints an
    unfinished repeat/jump structure as plain signs."""
    from .notation_rules import normalized_parts
    parts = normalized_parts(score)
    lines = [f"title={literal(score.title)}", f"composer={literal(score.composer)}", "NoIndent"]
    if any(n.grace_before or n.grace_after for p in parts for n in p.notes):
        # Only grace/chord parsing consults this upstream option; ordinary
        # single notes keep their existing octave suffix syntax unchanged.
        lines.append("OctavesBefore")
    if score.with_staff:
        lines.append("WithStaff")
    items = effective_bar_items(score)
    has_jump = any(item in symbols.JUMPS for _, ends in items for item in ends)
    implicit = implicit_repeat_start(items) and not draft
    open_before, is_open = [], False          # a draft ending bracket still open at each bar's start
    for starts, ends in items:
        open_before.append(is_open)
        if any(v in starts for v in (*_DRAFT_VOLTA, "volta.final")):
            is_open = True
        if "volta.end" in ends:
            is_open = False
    close_at_end = draft and is_open
    for index, part in enumerate(parts):
        if index:
            lines.append("NextPart")
        if len(parts) > 1 and part.name:
            lines.append("instrument=" + literal(part.name))
        lines.append(f"1={score.key} {score.meter}{pickup_token(score.pickup)}" + (f" 4={score.tempo}" if index == 0 else ""))
        ranges = score.part_bars(index)
        for bar, (start, end) in enumerate(ranges):
            starts, ends = items[bar] if bar < len(items) else ((), ())
            next_starts = items[bar + 1][0] if bar + 1 < len(items) else ()
            words = []
            for row in range(start, end):
                ctx = {"part": index, "has_jump": has_jump, "bar_index": bar, "last": bar == len(ranges) - 1,
                       "implicit_repeat": implicit and bar == 0 and row == start,
                       "head_text": index == 0 and row == 0 and header_words(part.notes[row].text),
                       "draft": draft, "volta_open": draft and row == start and open_before[bar],
                       "volta_close": close_at_end and bar == len(ranges) - 1 and row == end - 1,
                       "bar_start": starts if row == start else (), "bar_end": ends if row == end - 1 else (),
                       "next_bar_start": next_starts if row == end - 1 else ()}
                words += note_words(part.notes[row], ctx)
            text = " ".join(words)
            if HEADER_LIKE.match(text):
                lines[-1] += " " + text
            else:
                lines.append(text)
            if score.bars_per_line and index == 0 and (bar + 1 - (1 if score.pickup else 0)) % score.bars_per_line == 0 \
                    and bar + 1 - (1 if score.pickup else 0) > 0:
                lines.append("\\break")
        if part.lyrics.strip():
            lines.append("H: " + literal(part.lyrics, lyric=True))
    return "\n".join(lines) + "\n"


def export_warnings(score) -> list[str]:
    """Adjustments the export makes silently (tech.md P3); for a status message."""
    warnings = []
    items = effective_bar_items(score)
    for bar, (_, ends) in enumerate(items[:-1]):
        if "bar.repeat_start" in items[bar + 1][0] and any(i in ("bar.dashed", "bar.double") for i in ends):
            warnings.append(tr("第 {bar} 小節結尾的小節線被下一小節的反覆起點取代。").format(bar=bar + 1))
    return warnings
