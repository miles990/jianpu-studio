"""Moving bar lines: a pickup or a new meter on a score that already has notes.

Every function returns new note lists for all parts (the caller applies them in one undo
step) or raises ValueError with the reason and what to do. Nothing is dropped: a note that a
new bar line would cut is split into tied notes (a rest into rests), with its marks, text and
front grace on the first piece and its tie, after-grace and most line ends on the last one.
A score whose last bar was complete is completed again with rests, and the signs at the end
of the music (a final barline, Fine...) move to the new end.

What a change does is never estimated: ``preview`` applies it to a copy of the score and
compares the two (bars, split notes per part, rests added or removed, signs moved or lost,
and any problem the result would have), so the question shows exactly what applying does.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace

from . import edit_guard, symbols
from .i18n import tr
from .model import Note, Score, rest_fill, tuplet_ranges
from .notation_rules import _part_label, bar_label, structure_problems, tick_place


@dataclass(frozen=True)
class Rebar:
    parts: tuple[list[Note], ...]

    def apply(self, score: Score, *, meter=None, pickup=None) -> None:
        if meter is not None:
            score.meter = meter
        if pickup is not None:
            score.pickup = pickup
        for part, notes in zip(score.parts, self.parts):
            part.notes = list(notes)


def _plain_rest(note: Note) -> bool:
    return note.degree == 0 and not (note.marks or note.spans_start or note.spans_end or note.text
                                     or note.grace_before or note.grace_after or note.tuplet)


def rests_only(score: Score) -> bool:
    """Only rests without marks (a new or template score): its bars can simply be redrawn."""
    return all(_plain_rest(n) for p in score.parts for n in p.notes)


def _target(score: Score, meter, pickup) -> Score:
    return replace(score, meter=meter or score.meter, pickup=score.pickup if pickup is None else pickup,
                   parts=list(score.parts))


def _bar_lists(score: Score, index: int) -> list[list[Note]]:
    notes = score.parts[index].notes
    return [notes[a:b] for a, b in score.part_bars(index)]


def rebar_rests(score: Score, *, meter: str | None = None, pickup: int | None = None) -> Rebar:
    """Redraw an all-rest score with the new pickup/meter, keeping the number of bars and each
    bar's bar lines (repeats, double bar...) on the same bar."""
    target = _target(score, meter, pickup)
    # A pickup alone is not a whole piece: it needs at least one bar after it.
    count = max(2 if target.pickup else 1, len(score.part_bars(0)))
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
    return Rebar(tuple(parts))


def _complete(score: Score) -> bool:
    """Every part ends where a last bar may end (the last bar is not missing beats)."""
    totals = [sum(n.ticks for n in p.notes) for p in score.parts]
    return any(totals) and all(t and score.final_bar_targets(t)[0] == t for t in totals)


def _finish(source: Score, target: Score, parts: list[list[Note]]) -> list[list[Note]]:
    """Complete the last bar again when ``source``'s was complete, moving the bar-end signs of
    the music's end (final barline, Fine, :‖...) to the new end."""
    if not _complete(source):
        return parts
    totals = [sum(n.ticks for n in p) for p in parts]
    goal = max(target.final_bar_targets(t)[0] for t in totals)
    if parts[0] and parts[0][-1].bar_end and not target.is_boundary(goal):
        # A bar-end sign needs a whole last bar (a short one after a pickup cannot carry it).
        offset, length = target.bar_room(goal)
        goal += length - offset
    out = []
    for index, (notes, total) in enumerate(zip(parts, totals)):
        notes = list(notes)
        if goal > total:
            fill = target.rests_between(total, goal)
            if index == 0 and notes and notes[-1].bar_end:
                fill[-1] = replace(fill[-1], bar_end=notes[-1].bar_end)
                notes[-1] = replace(notes[-1], bar_end=())
            notes += fill
        out.append(notes)
    return out


def leading_rest_ticks(score: Score) -> int:
    """Ticks of plain rests every part starts with, inside the first bar (a sign at the start
    of the first bar moves to the new first note)."""
    out = None
    for part in score.parts:
        ticks = 0
        for row, note in enumerate(part.notes):
            if not _plain_rest(note) or (row and note.bar_start):
                break
            ticks += note.ticks
        out = ticks if out is None else min(out, ticks)
    return min(out or 0, score.first_bar_ticks)


def trim_to_pickup(score: Score, pickup: int) -> Rebar:
    """Shorten the first bar to a pickup by removing its leading rests (every part). The signs
    of the first bar's start and end stay on that bar."""
    cut = score.first_bar_ticks - pickup
    if not pickup or cut <= 0 or leading_rest_ticks(score) < cut:
        raise ValueError(tr("第一小節開頭的休止不夠長，不能直接縮成弱起。"))
    target = _target(score, None, pickup)
    parts = []
    for part in score.parts:
        notes, removed, start, end = list(part.notes), 0, (), ()
        while removed < cut:
            first = notes.pop(0)
            start = start or first.bar_start
            end = first.bar_end
            removed += first.ticks
        if removed > cut:
            rests = rest_fill(removed - cut)
            rests[-1] = replace(rests[-1], bar_end=end)
            notes[0:0] = rests
        if start and notes:
            notes[0] = replace(notes[0], bar_start=symbols.sort_ids([*start, *notes[0].bar_start]))
        parts.append(notes)
    return Rebar(tuple(_finish(score, target, parts)))


def pad_ticks(score: Score, pickup: int) -> int:
    """Rests to put in front so every later bar line stays where it is under ``pickup``."""
    new_first = pickup or score.bar_ticks
    return (new_first - score.first_bar_ticks) % score.bar_ticks


def pad_to_pickup(score: Score, pickup: int) -> Rebar:
    """Put rests in front of every part so that, with the new pickup, every bar keeps its
    music. The old first bar's start sign goes to the start of the bar that now holds it: a
    new pickup bar of rests comes before a whole first bar, which keeps the sign on its first
    note; a pickup that grows (or is filled out to a whole bar) gets rests in front of its
    music, and the sign moves to them."""
    ticks = pad_ticks(score, pickup)
    if not ticks:
        raise ValueError(tr("無法移動小節線"))
    target = _target(score, None, pickup)
    start = ticks - target.bar_room(ticks)[0]           # where the bar holding the old first note starts
    parts = []
    for part in score.parts:
        notes = target.rests_between(0, ticks) + list(part.notes)
        first = len(notes) - len(part.notes)
        if part.notes and part.notes[0].bar_start and start < ticks:
            row = _onsets(notes).index(start)
            notes[row] = replace(notes[row], bar_start=part.notes[0].bar_start)
            notes[first] = replace(notes[first], bar_start=())
        parts.append(notes)
    return Rebar(tuple(_finish(score, target, parts)))


def _pieces(note: Note, ticks: int) -> list[Note]:
    """``note``'s pitch written as ``ticks`` (longest first), tied for a note."""
    return [replace(note, duration=r.duration, dotted=r.dotted) for r in rest_fill(ticks)]


# Lines that arrive at the attack of a note (a dynamic there ends a hairpin; a glissando
# reaches the next note) end on the first piece; a glissando leaves from the last piece.
_END_ON_FIRST = ("hairpin", "gliss")
_START_ON_LAST = ("gliss",)


def _family(sid: str) -> str:
    return symbols.SYMBOLS[sid].family


def _split(note: Note, lengths: list[int]) -> list[Note]:
    """``note`` cut into consecutive ``lengths``; items go to the first or last piece."""
    pieces = []
    for length in lengths:
        pieces += _pieces(note, length)
    starting = {_family(s) for s in note.spans_start}
    ends_first = tuple(s for s in note.spans_end if _family(s) in _END_ON_FIRST or _family(s) in starting)
    ends_last = tuple(s for s in note.spans_end if s not in ends_first)
    starts_last = tuple(s for s in note.spans_start if _family(s) in _START_ON_LAST)
    starts_first = tuple(s for s in note.spans_start if s not in starts_last)
    bare = dict(marks=(), spans_start=(), spans_end=(), text="", grace_before="", grace_after="",
                bar_start=(), bar_end=(), tie=False)
    out = []
    for k, piece in enumerate(pieces):
        first, last = k == 0, k == len(pieces) - 1
        values = dict(bare)
        if first:
            values.update(marks=note.marks, spans_start=starts_first, spans_end=ends_first, text=note.text,
                          grace_before=note.grace_before, bar_start=note.bar_start)
        if last:
            values.update(spans_end=symbols.sort_ids([*values["spans_end"], *ends_last]),
                          spans_start=symbols.sort_ids([*values["spans_start"], *starts_last]),
                          grace_after=note.grace_after, bar_end=note.bar_end, tie=note.tie)
        elif note.degree:
            values["tie"] = True
        out.append(replace(piece, **values))
    return out


def rebar(score: Score, *, meter: str | None = None, pickup: int | None = None) -> Rebar:
    """The same music with new bar lines; raises ValueError (reason + what to do) when a sign
    tied to a bar line or a triplet would have to be cut."""
    target = _target(score, meter, pickup)
    first_part = score.parts[0].notes
    total = sum(n.ticks for n in first_part)
    position = 0
    for note in first_part:
        end = position + note.ticks
        if (note.bar_start and position and not target.is_boundary(position)) or (
                note.bar_end and end != total and not target.is_boundary(end)):
            raise ValueError(tr("{where}有反覆、跳躍或小節線記號，新的小節線不在那裡。請先移除這些小節記號，"
                                "改好拍號或弱起後再加回去。").format(where=tick_place(score, position)))
        position = end
    parts = []
    for part in score.parts:
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
            notes += _split(note, lengths) if len(lengths) > 1 else [note]
            position += note.ticks
            row += 1
        parts.append(notes)
    return Rebar(tuple(_finish(score, target, parts)))


# ---------------------------------------------------------------- what applying does
@dataclass(frozen=True)
class Moved:
    ident: str
    edge: str          # "start" | "end"
    bar: int           # the bar it ends up on (in the changed score)


@dataclass
class Preview:
    after: Score
    bars_before: int
    bars_after: int
    split: list[tuple[int, int]] = field(default_factory=list)   # (part, onset tick before) of each split note
    lead: int = 0          # ticks of rests added (+) or removed (-) in front of the music
    filled: int = 0        # ticks of rests added after the music
    moved: list[Moved] = field(default_factory=list)
    lost: list = field(default_factory=list)                      # edit_guard items the change removes
    problem: str | None = None                                    # why it cannot be applied

    unchanged: bool = False                                       # every note is as it was

    @property
    def trivial(self) -> bool:
        """Nothing but the bar lines changes: the notes are those of the score, as they were."""
        return self.unchanged and self.problem is None


def _free_rest(note: Note) -> bool:
    return note.degree == 0 and not (note.marks or note.spans_start or note.spans_end or note.text
                                     or note.grace_before or note.grace_after or note.tuplet)


def _lead(notes: list[Note]) -> int:
    ticks = 0
    for note in notes:
        if not _free_rest(note):
            break
        ticks += note.ticks
    return ticks


def _onsets(notes: list[Note]) -> list[int]:
    out, position = [], 0
    for note in notes:
        out.append(position)
        position += note.ticks
    return out


def _bar_signs(score: Score) -> dict[tuple[str, str], list[int]]:
    """(item, edge) -> the tick each occurrence is anchored to (a bar's start, or its end)."""
    out = defaultdict(list)
    notes = score.parts[0].notes
    for note, onset in zip(notes, _onsets(notes)):
        for item in note.bar_start:
            out[(item, "start")].append(onset)
        for item in note.bar_end:
            out[(item, "end")].append(onset + note.ticks)
    return out


def preview(score: Score, result: Rebar, *, meter=None, pickup=None) -> Preview:
    """Apply ``result`` to a copy of ``score`` and report what changed."""
    after = score.copy()
    result.apply(after, meter=meter, pickup=pickup)
    olds = [p.notes for p in score.parts]
    news = [p.notes for p in after.parts]
    # Every part moves by the same amount; the part with the least leading rest shows it.
    music = min(range(len(olds)), key=lambda i: _lead(olds[i]))
    shift = _lead(news[music]) - _lead(olds[music])
    filled = sum(n.ticks for n in news[music]) - sum(n.ticks for n in olds[music]) - shift
    split = []
    for part, (old, new) in enumerate(zip(olds, news)):
        new_onsets = sorted(_onsets(new))
        for note, onset in zip(old, _onsets(old)):
            if note.degree and any(onset + shift < t < onset + shift + note.ticks for t in new_onsets):
                split.append((part, onset))
    moved = []
    signs_before, signs_after = _bar_signs(score), _bar_signs(after)
    for key, ticks in signs_after.items():
        for old_tick, new_tick in zip(signs_before.get(key, []), ticks):
            if old_tick + shift != new_tick:
                bar = after.bar_index(new_tick if key[1] == "start" else max(0, new_tick - 1))
                moved.append(Moved(key[0], key[1], bar))
    out = Preview(after, len(score.part_bars(0)), len(after.part_bars(0)), split, shift, filled,
                  moved, edit_guard.losses(score, after))
    out.unchanged = olds == news
    out.problem = _problem(score, after)
    return out


def _problem(before: Score, after: Score) -> str | None:
    """A problem the changed score would have that the score did not have."""
    try:
        after.validate()
    except ValueError as exc:
        return str(exc)
    totals = [sum(n.ticks for n in p.notes) for p in after.parts]
    if len(set(totals)) > 1 and len({sum(n.ticks for n in p.notes) for p in before.parts}) == 1:
        return tr("這項修改會讓各聲部的長度不同，所以沒有套用；請改在各聲部的同一位置操作。")
    for check in (Score.rhythm_error, Score.edit_error):
        if (error := check(after)) and not check(before):
            return str(error)
    old = Counter(str(p) for p in structure_problems(before))
    new = structure_problems(after)
    if len(new) > sum(old.values()):
        fresh = [p for p in new if not old[str(p)]]
        return str((fresh or new)[0])
    return None


def options(score: Score, *, meter: str, pickup: int) -> list[tuple[str, str, Rebar | None, Preview | str]]:
    """Every way of moving the bar lines as (key, label, result, preview), or (key, label, None,
    reason) when that way is impossible. An all-rest score is only redrawn ("rests")."""
    if rests_only(score):
        result = rebar_rests(score, meter=meter, pickup=pickup)
        return [("rests", "", result, preview(score, result, meter=meter, pickup=pickup))]
    ways = []
    if meter == score.meter:
        cut = score.first_bar_ticks - (pickup or score.bar_ticks)
        if 0 < cut and leading_rest_ticks(score) >= cut:
            ways.append(("trim", tr("刪去曲首 {beats} 拍休止符（其餘小節不動）").format(beats=_beats(cut)),
                         lambda: trim_to_pickup(score, pickup)))
        if pad := pad_ticks(score, pickup):
            ways.append(("pad", tr("在曲首補上 {beats} 拍休止符（其餘小節不動）").format(beats=_beats(pad)),
                         lambda: pad_to_pickup(score, pickup)))
    ways.append(("rebar", tr("整首重新分小節"), lambda: rebar(score, meter=meter, pickup=pickup)))
    out = []
    for key, label, make in ways:
        try:
            result = make()
        except ValueError as exc:
            out.append((key, label, None, str(exc)))
            continue
        out.append((key, label, result, preview(score, result, meter=meter, pickup=pickup)))
    return out


def describe(score: Score, result: Preview, label: str) -> str:
    """What one way of moving the bar lines does, from its preview."""
    after = result.after
    text = tr("{change}：{before} 小節 → {after} 小節。").format(
        change=label, before=result.bars_before, after=result.bars_after)
    if result.split:
        many = len(score.parts) > 1
        places = [(_part_label(score, part) if many else "") + tick_place(score, onset)
                  for part, onset in result.split]
        shown = "、".join(places[:6]) + ("……" if len(places) > 6 else "")
        text += tr("有 {count} 個音會跨過新的小節線，會拆開並用延音線連起來（{where}）。").format(
            count=len(result.split), where=shown)
    else:
        text += tr("沒有音需要拆開。")
    if result.lead > 0:
        text += tr("曲首補上 {beats} 拍休止符。").format(beats=_beats(result.lead))
    elif result.lead < 0:
        text += tr("刪去曲首 {beats} 拍休止符。").format(beats=_beats(-result.lead))
    if result.filled > 0:
        text += tr("最後一小節補上 {beats} 拍休止符。").format(beats=_beats(result.filled))
    for item in result.moved:
        name = symbols.SYMBOLS[item.ident].display_name
        template = tr("「{name}」會移到{bar}開頭。") if item.edge == "start" else tr("「{name}」會移到{bar}結尾。")
        text += template.format(name=name, bar=bar_label(after, item.bar))
    if result.lost:
        return text + tr("會移除 {count} 項：{items}。可用復原還原。").format(
            count=len(result.lost), items=edit_guard.describe(score, result.lost))
    return text + tr("音符、記號都會保留，可用復原還原。")


def _beats(ticks: int) -> str:
    from fractions import Fraction
    value = Fraction(ticks, 48)
    return str(value) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"
