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


def literal(text: str) -> str:
    """Text fields are literal, never raw LilyPond/Scheme."""
    return text.replace('"', "' ").replace("\\", "/").replace("%", "％").replace("{", "｛").replace("}", "｝")


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
    w: list[str] = []
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


def to_jianpu(score) -> str:
    """Export a validated score (``Score.to_jianpu`` checks it first)."""
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
            lines.append("H: " + literal(part.lyrics))
    return "\n".join(lines) + "\n"


def export_warnings(score) -> list[str]:
    """Adjustments the export makes silently (tech.md P3); for a status message."""
    warnings = []
    items = effective_bar_items(score)
    for bar, (_, ends) in enumerate(items[:-1]):
        if "bar.repeat_start" in items[bar + 1][0] and any(i in ("bar.dashed", "bar.double") for i in ends):
            warnings.append(tr("第 {bar} 小節結尾的小節線被下一小節的反覆起點取代。").format(bar=bar + 1))
    return warnings
