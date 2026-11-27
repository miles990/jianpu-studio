"""Moving bar lines: a pickup or a new meter on a score that already has notes.

Every function returns new note lists for all parts (the caller applies them in one undo
step) or raises ValueError with the reason and what to do. Nothing is dropped: a note that a
new bar line would cut is split into tied notes (a rest into rests), with its marks on the
first piece and its line ends, tie and after-grace on the last one.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from .i18n import tr
from .model import Note, Score, rest_fill, tuplet_ranges
from .notation_rules import tick_place


@dataclass(frozen=True)
class Rebar:
    parts: tuple[list[Note], ...]
    split: tuple[str, ...] = ()      # 「第 2 小節第 3 拍」 of every note that was split
    filled: int = 0                  # ticks of rests added at the end
    bars_before: int = 0
    bars_after: int = 0

    def apply(self, score: Score, *, meter=None, pickup=None) -> None:
        if meter is not None:
            score.meter = meter
        if pickup is not None:
            score.pickup = pickup
        for part, notes in zip(score.parts, self.parts):
            part.notes = notes


def _plain_rest(note: Note) -> bool:
    return note.degree == 0 and not (note.marks or note.spans_start or note.spans_end or note.text
                                     or note.grace_before or note.grace_after or note.tuplet)


def rests_only(score: Score) -> bool:
    """Only rests without marks (a new or template score): its bars can simply be redrawn."""
    return all(_plain_rest(n) for p in score.parts for n in p.notes)


def _bar_lists(score: Score, index: int) -> list[list[Note]]:
    notes = score.parts[index].notes
    return [notes[a:b] for a, b in score.part_bars(index)]


def rebar_rests(score: Score, *, meter: str | None = None, pickup: int | None = None) -> Rebar:
    """Redraw an all-rest score with the new pickup/meter, keeping the number of bars and each
    bar's bar lines (repeats, double bar...) on the same bar."""
    target = replace(score, meter=meter or score.meter, pickup=score.pickup if pickup is None else pickup,
                     parts=list(score.parts))
    count = max(1, len(score.part_bars(0)))
    old = [_bar_lists(score, i) for i in range(len(score.parts))]
    parts = []
    for index in range(len(score.parts)):
        notes = []
        for bar in range(count):
            length = target.first_bar_ticks if bar == 0 else target.bar_ticks
            rests = rest_fill(length)
            source = old[index][bar] if bar < len(old[index]) else []
            if source:
                rests[0] = replace(rests[0], bar_start=source[0].bar_start)
                rests[-1] = replace(rests[-1], bar_end=source[-1].bar_end)
            notes += rests
        parts.append(notes)
    return Rebar(tuple(parts), (), 0, count, count)


def leading_rest_ticks(score: Score) -> int:
    """Ticks of plain rests every part starts with, inside the first bar."""
    out = None
    for part in score.parts:
        ticks = 0
        for note in part.notes:
            if not _plain_rest(note) or note.bar_start:
                break
            ticks += note.ticks
        out = ticks if out is None else min(out, ticks)
    return min(out or 0, score.first_bar_ticks)


def trim_to_pickup(score: Score, pickup: int) -> Rebar:
    """Shorten the first bar to a pickup by removing its leading rests (every part)."""
    cut = score.first_bar_ticks - pickup
    if cut <= 0 or leading_rest_ticks(score) < cut:
        raise ValueError(tr("第一小節開頭的休止不夠長，不能直接縮成弱起。"))
    parts = []
    for part in score.parts:
        notes, removed = list(part.notes), 0
        while removed < cut:
            first = notes.pop(0)
            removed += first.ticks
        if removed > cut:
            notes[0:0] = rest_fill(removed - cut)
        parts.append(notes)
    bars = len(score.part_bars(0))
    return Rebar(tuple(parts), (), 0, bars, bars)


def pad_first_bar(score: Score, ticks: int) -> Rebar:
    """Put ``ticks`` of rests in front of every part (a new pickup bar, or a pickup filled out
    to a whole bar). Bar lines keep their bars because the music moves by whole bars' worth."""
    parts = []
    for part in score.parts:
        rests = rest_fill(ticks)
        notes = list(part.notes)
        if notes and notes[0].bar_start:
            # The sign at the start of the music stays at the start of the music.
            rests[0] = replace(rests[0], bar_start=notes[0].bar_start)
            notes[0] = replace(notes[0], bar_start=())
        parts.append(rests + notes)
    bars = len(score.part_bars(0))
    return Rebar(tuple(parts), (), 0, bars, bars + 1)


def _pieces(note: Note, ticks: int) -> list[Note]:
    """``note``'s pitch written as ``ticks`` (longest first), tied for a note."""
    return [replace(note, duration=r.duration, dotted=r.dotted) for r in rest_fill(ticks)]


def _split(note: Note, lengths: list[int]) -> list[Note]:
    """``note`` cut into consecutive ``lengths``; items go to the first or last piece."""
    pieces = []
    for length in lengths:
        pieces += _pieces(note, length)
    bare = dict(marks=(), spans_start=(), spans_end=(), text="", grace_before="", grace_after="",
                bar_start=(), bar_end=(), tie=False)
    out = []
    for k, piece in enumerate(pieces):
        first, last = k == 0, k == len(pieces) - 1
        values = dict(bare)
        if first:
            values.update(marks=note.marks, spans_start=note.spans_start, text=note.text,
                          grace_before=note.grace_before, bar_start=note.bar_start)
        if last:
            values.update(spans_end=note.spans_end, grace_after=note.grace_after, bar_end=note.bar_end,
                          tie=note.tie)
        elif note.degree:
            values["tie"] = True
        out.append(replace(piece, **values))
    return out


def rebar(score: Score, *, meter: str | None = None, pickup: int | None = None) -> Rebar:
    """The same music with new bar lines; raises ValueError (reason + what to do) when a sign
    tied to a bar line or a triplet would have to be cut."""
    target = replace(score, meter=meter or score.meter, pickup=score.pickup if pickup is None else pickup,
                     parts=list(score.parts))
    first_part = score.parts[0].notes
    total = sum(n.ticks for n in first_part)
    position = 0
    for row, note in enumerate(first_part):
        end = position + note.ticks
        if (note.bar_start and position and not target.is_boundary(position)) or (
                note.bar_end and end != total and not target.is_boundary(end)):
            raise ValueError(tr("{where}有反覆、跳躍或小節線記號，新的小節線不在那裡。請先移除這些小節記號，"
                                "改好拍號或弱起後再加回去。").format(where=tick_place(score, position)))
        position = end
    split, parts = [], []
    for index, part in enumerate(score.parts):
        groups = dict(tuplet_ranges(part.notes))
        notes, position, row = [], 0, 0
        while row < len(part.notes):
            if row in groups:
                end_row = groups[row]
                length = sum(n.ticks for n in part.notes[row:end_row])
                offset, room = target.bar_room(position)
                if offset + length > room:
                    raise ValueError(tr("{where}的三連音會被新的小節線切開。請先把它改成一般音符，再改拍號或弱起。").format(
                        where=tick_place(score, position)))
                notes += part.notes[row:end_row]
                position += length
                row = end_row
                continue
            note = part.notes[row]
            lengths, left, here = [], note.ticks, position
            while left:
                offset, room = target.bar_room(here)
                step = min(left, room - offset)
                lengths.append(step)
                left -= step
                here += step
            if len(lengths) > 1:
                split.append(tick_place(score, position))
                notes += _split(note, lengths)
            else:
                notes.append(note)
            position += note.ticks
            row += 1
        parts.append(notes)
    trial = replace(target, parts=[replace(p, notes=n) for p, n in zip(score.parts, parts)])
    before = len(trial.part_bars(0))
    totals = [sum(n.ticks for n in p) for p in parts]
    goal = max(trial.final_bar_targets(t)[0] for t in totals) if any(totals) else 0
    filled = goal - totals[0] if parts and totals[0] else 0
    for notes, t in zip(parts, totals):
        if t and goal > t:
            notes += trial.rests_between(t, goal)
    after = replace(target, parts=[replace(p, notes=n) for p, n in zip(score.parts, parts)])
    return Rebar(tuple(parts), tuple(dict.fromkeys(split)), filled, len(score.part_bars(0)),
                 len(after.part_bars(0)) or before)


def describe(score: Score, result: Rebar, label: str) -> str:
    """What a re-barring changes, for the question before it is applied."""
    text = tr("{change}：{before} 小節 → {after} 小節。").format(
        change=label, before=result.bars_before, after=result.bars_after)
    if result.split:
        shown = "、".join(result.split[:6]) + ("……" if len(result.split) > 6 else "")
        text += tr("有 {count} 個音會跨過新的小節線，會拆開並用延音線連起來（{where}）。").format(
            count=len(result.split), where=shown)
    else:
        text += tr("沒有音需要拆開。")
    if result.filled:
        text += tr("最後一小節補上 {beats} 拍休止符。").format(beats=_beats(result.filled))
    return text + tr("音符、記號都會保留，可用復原還原。")


def _beats(ticks: int) -> str:
    from fractions import Fraction
    value = Fraction(ticks, 48)
    return str(value) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"


