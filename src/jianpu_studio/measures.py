"""Pure, measure-preserving edits over the flat immutable-note score model.

Measure numbers and note rows are zero based; range ends are exclusive. An
unfinished final measure is a measure, but an empty score has no measures.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from functools import lru_cache
from typing import Iterable

from .model import DURATIONS, Note, Score

MAX_NOTES = 10000
_CONNECTIONS = ("slur_start", "slur_end", "tie", "repeat_start", "repeat_end")
# A source identity is (copy generation, original note row). New rests/notes
# have no source identity, even if their pitch happens to match a removed note.
Origin = tuple[int, int] | None
Entry = tuple[Note, Origin]


@dataclass(frozen=True)
class MeasureEdit:
    notes: list[Note]
    selection: tuple[int, ...] = ()
    current: int = -1
    notice: str = ""


def measure_ranges(score: Score) -> list[tuple[int, int]]:
    """Return note ranges, permitting only an unfinished *last* measure."""
    score.validate()
    bar_ticks = int(score.bar_length * 16)
    ranges, start, position = [], 0, 0
    for row, note in enumerate(score.notes):
        if position % bar_ticks + note.ticks > bar_ticks:
            raise ValueError(f"第 {row + 1} 個音符超出第 {position // bar_ticks + 1} 小節。請先縮短時值或拆分跨小節長音。")
        position += note.ticks
        if position % bar_ticks == 0:
            ranges.append((start, row + 1))
            start = row + 1
    if start < len(score.notes):
        ranges.append((start, len(score.notes)))
    return ranges


def _context(score: Score) -> tuple[list[tuple[int, int]], list[Entry]]:
    ranges = measure_ranges(score)
    if error := score.notation_error():
        raise ValueError("請先修正連線或反覆記號，再編輯小節：" + error)
    return ranges, [(note, (0, row)) for row, note in enumerate(score.notes)]


@lru_cache(maxsize=128)
def _rests(ticks: int) -> tuple[Note, ...]:
    """Shortest exact rest decomposition, including odd dotted-32nd ticks."""
    choices = sorted((Note(0, duration=d, dotted=dot) for d in DURATIONS for dot in (False, True)),
                     key=lambda note: note.ticks, reverse=True)
    fills: list[tuple[Note, ...] | None] = [()] + [None] * ticks
    for length in range(1, ticks + 1):
        for note in choices:
            previous = fills[length-note.ticks] if note.ticks <= length else None
            if previous is not None:
                candidate = (note,) + previous
                if fills[length] is None or len(candidate) < len(fills[length]):
                    fills[length] = candidate
    if fills[ticks] is None:
        raise ValueError("剩餘拍數無法用目前支援的休止符表示；請調整附點三十二分音符的時值。")
    return fills[ticks]


def _bar_selection(indices: Iterable[int], count: int) -> list[int]:
    try:
        raw = list(indices)
    except TypeError as exc:
        raise ValueError("請先選取要編輯的小節。") from exc
    if not raw:
        raise ValueError("請先選取要編輯的小節。")
    if any(type(bar) is not int or not 0 <= bar < count for bar in raw):
        raise ValueError("選取的小節不存在。")
    return sorted(set(raw))


def _spans(notes: list[Note]):
    """Pair validated original marks; a continued slur can end and begin here."""
    slur = repeat = None
    for row, note in enumerate(notes):
        if note.slur_end:
            yield slur, row, "slur_start", "slur_end"
            slur = None
        if note.slur_start:
            slur = row
        if note.tie:
            yield row, row + 1, "tie", None
        if note.repeat_start:
            repeat = row
        if note.repeat_end:
            yield repeat, row, "repeat_start", "repeat_end"
            repeat = None


def _finish(score: Score, entries: list[Entry], selection=(), current=None) -> MeasureEdit:
    if len(entries) > MAX_NOTES:
        raise ValueError("音符太多（上限 10,000）；請減少新增或複製的小節數。")
    positions, groups, run_starts = {}, set(), []
    for row, (_, origin) in enumerate(entries):
        if origin is not None:
            positions[origin] = row
            groups.add(origin[0])
        previous = entries[row-1][1] if row else None
        consecutive = (origin is not None and previous is not None
                       and origin == (previous[0], previous[1]+1))
        run_starts.append(run_starts[-1] if consecutive else row)
    notes = [replace(note, **dict.fromkeys(_CONNECTIONS, False))
             if any(getattr(note, field) for field in _CONNECTIONS) else note for note, _ in entries]
    preserved = 0
    for start, end, start_field, end_field in _spans(score.notes):
        for group in groups:
            first, last = positions.get((group, start)), positions.get((group, end))
            # Endpoint identities plus an unchanged contiguous span prevent a
            # deleted tie from accidentally joining a new, equal-pitch neighbor.
            if first is None or last is None or run_starts[last] > first or last-first != end-start:
                continue
            notes[first] = replace(notes[first], **{start_field: True})
            preserved += 1
            if end_field:
                notes[last] = replace(notes[last], **{end_field: True})
                preserved += 1
    dropped = sum(bool(getattr(note, field)) for note, _ in entries for field in _CONNECTIONS) - preserved
    result_score = replace(score, notes=notes)
    measure_ranges(result_score)
    if error := result_score.notation_error():
        raise ValueError("這項小節修改無法安全保留原有記號：" + error)
    selection = tuple(selection)
    current = selection[0] if current is None and selection else -1 if current is None else current
    notice = f"已移除 {dropped} 個受影響的連線或反覆記號，可用復原還原。" if dropped else ""
    return MeasureEdit(notes, selection, current, notice)


def _complete_tail(score: Score, entries: list[Entry]) -> None:
    missing = (-sum(note.ticks for note in score.notes)) % int(score.bar_length * 16)
    entries.extend((rest, None) for rest in _rests(missing))


def insert_measures(score: Score, at_bar: int, count: int = 1) -> MeasureEdit:
    """Insert complete rest measures before ``at_bar``; len(ranges) appends.

    Appending after an unfinished final measure completes that measure first.
    Inserting before it leaves its existing, unfinished duration unchanged.
    """
    ranges, entries = _context(score)
    if type(at_bar) is not int or not 0 <= at_bar <= len(ranges):
        raise ValueError("新增小節的位置不存在。")
    if type(count) is not int or count < 1:
        raise ValueError("新增小節數須為正整數。")
    rests = _rests(int(score.bar_length * 16))
    if len(entries) + count*len(rests) > MAX_NOTES:
        raise ValueError("音符太多（上限 10,000）；請減少新增的小節數。")
    if at_bar == len(ranges):
        _complete_tail(score, entries)
        position = len(entries)
    else:
        position = ranges[at_bar][0]
    added = [(rest, None) for _ in range(count) for rest in rests]
    entries[position:position] = added
    return _finish(score, entries, range(position, position+len(added)))


def delete_measures(score: Score, bar_indices: Iterable[int]) -> MeasureEdit:
    """Delete selected whole measures, selecting the nearest surviving one."""
    ranges, entries = _context(score)
    selected = _bar_selection(bar_indices, len(ranges))
    removed = set(selected)
    kept = [entry for bar, (start, end) in enumerate(ranges) if bar not in removed for entry in entries[start:end]]
    result = _finish(score, kept)
    remaining = measure_ranges(replace(score, notes=result.notes))
    if remaining:
        start, end = remaining[min(selected[0], len(remaining)-1)]
        return replace(result, selection=tuple(range(start, end)), current=start)
    return result


def clear_measures(score: Score, bar_indices: Iterable[int]) -> MeasureEdit:
    """Keep selected measure positions and replace their contents with rests."""
    ranges, entries = _context(score)
    selected = set(_bar_selection(bar_indices, len(ranges)))
    rests = _rests(int(score.bar_length * 16))
    result, selection = [], []
    for bar, (start, end) in enumerate(ranges):
        if bar in selected:
            selection.extend(range(len(result), len(result)+len(rests)))
            result.extend((rest, None) for rest in rests)
        else:
            result.extend(entries[start:end])
    return _finish(score, result, selection)


def duplicate_measures(score: Score, bar_indices: Iterable[int]) -> MeasureEdit:
    """Duplicate consecutive measures immediately after themselves.

    A selected unfinished tail is first completed in both original and copy.
    Complete internal connections are copied; partial connections are removed.
    """
    ranges, entries = _context(score)
    selected = _bar_selection(bar_indices, len(ranges))
    if selected != list(range(selected[0], selected[-1]+1)):
        raise ValueError("複製小節需選取連續的小節。")
    start, end = ranges[selected[0]][0], ranges[selected[-1]][1]
    if selected[-1] == len(ranges)-1:
        _complete_tail(score, entries)
        end = len(entries)
    copied = [(note, (1, origin[1]) if origin is not None else None) for note, origin in entries[start:end]]
    entries[end:end] = copied
    return _finish(score, entries, range(end, end+len(copied)))


def replace_rest_input(score: Score, row: int, note: Note) -> MeasureEdit:
    """Fill existing consecutive rests without moving any later beat or bar.

    The replacement stays inside one measure, never consumes a pitched note,
    and may consume additional adjacent rests to obtain an exact remainder.
    Unwritten time at the end of an unfinished measure is not an existing rest.
    """
    ranges, entries = _context(score)
    if type(row) is not int or not 0 <= row < len(entries):
        raise ValueError("請先選取要填入音符的休止符。")
    if not isinstance(note, Note):
        raise ValueError("輸入音符格式不正確。")
    note.validate()
    if score.notes[row].degree != 0:
        raise ValueError("填入音符只會覆寫休止符；請選取休止符或切換到修改音符。")
    if any(getattr(note, field) for field in _CONNECTIONS):
        raise ValueError("請先填入音符，再選取範圍加入連線或反覆記號。")
    _, bar_end = next((start, end) for start, end in ranges if start <= row < end)
    consumed, end, remainder = 0, row, None
    while end < bar_end and score.notes[end].degree == 0:
        rest = score.notes[end]
        if end != row and (rest.dynamic or rest.articulation):
            raise ValueError("相鄰休止符含力度或演奏記號；請先調整該記號，避免覆寫時遺失。")
        consumed += rest.ticks
        end += 1
        if consumed >= note.ticks:
            try:
                remainder = _rests(consumed-note.ticks)
                break
            except ValueError:
                # A one-tick remainder may become representable after another
                # rest, e.g. replacing a dotted 32nd plus 32nd with a 32nd.
                continue
    if remainder is None:
        if consumed >= note.ticks:
            raise ValueError("剩餘拍數無法用目前支援的休止符表示；請調整附點或時值。")
        raise ValueError("這個音符超過同小節內可用的連續休止拍數；不會覆蓋已有音符或推移後續小節。")
    previous = score.notes[row]
    note = replace(note, dynamic=note.dynamic or previous.dynamic, articulation=note.articulation or previous.articulation)
    unchanged = end == row+1 and not remainder and note == replace(previous, **dict.fromkeys(_CONNECTIONS, False))
    # Entering the same rest is a no-op, including its existing bar repeat.
    # Connection flags are not input attributes and must not make it an edit.
    if unchanged:
        note = previous
    origin = entries[row][1] if unchanged else None
    replacement = [(note, origin)] + [(rest, None) for rest in remainder]
    entries[row:end] = replacement
    return _finish(score, entries, (row,), row)
