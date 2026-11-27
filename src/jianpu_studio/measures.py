"""Pure, measure-preserving edits over every part of the score at once.

Measure numbers and note rows are zero based; range ends are exclusive. An
unfinished final measure is a measure, but an empty score has no measures.
With a pickup, measure 0 is the pickup. A measure operation changes the same
measures in every part and returns all parts, so the caller applies it as one
undo step. Rows, selection and ``current`` refer to part 0 (the edited part).
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from functools import lru_cache
from typing import Iterable, NamedTuple

from . import symbols
from .i18n import tr
from .model import DURATIONS, Note, Score
from .notation_rules import LocatedError, bar_items, note_place, span_pairs

MAX_NOTES = 10000
# A source identity is (copy generation, original note row). New rests/notes
# have no source identity, even if their pitch happens to match a removed note.
Origin = tuple[int, int] | None
Entry = tuple[Note, Origin]
NAVIGATION = frozenset({"nav.segno", "nav.tocoda", "nav.ds", "nav.dc", "nav.fine"})
REPEAT_ITEMS = frozenset({"bar.repeat_start", "bar.repeat_end", "bar.repeat_both", "volta.1", "volta.2", "volta.3",
                          "volta.4", "volta.final", "volta.end"})
PAIRED_BAR_ITEMS = NAVIGATION | REPEAT_ITEMS


@dataclass(frozen=True)
class MeasureEdit:
    notes: list[Note]                          # part 0 (the edited part)
    selection: tuple[int, ...] = ()
    current: int = -1
    notice: str = ""
    parts: tuple[list[Note], ...] = field(default=())   # every part, parts[0] is ``notes``

    def part_notes(self) -> tuple[list[Note], ...]:
        return self.parts or (self.notes,)

    def apply(self, score: Score) -> None:
        """Put the result into ``score`` (all parts)."""
        for part, notes in zip(score.parts, self.part_notes()):
            part.notes = notes

    def apply_to_project(self, project) -> None:
        self.apply(project.score)


class Connection(NamedTuple):
    """Items that only make sense together: kept only if all their notes survive together."""
    rows: tuple[int, ...]                       # notes that must survive (in order)
    items: tuple[tuple[int, str, object], ...]  # (row, field, id or True) to restore
    strict: bool                                # also require the notes between to be unchanged
    navigation: bool                            # never copied by duplicate (once per piece)


def measure_ranges(score: Score, part: int = 0) -> list[tuple[int, int]]:
    """Return note ranges of one part, permitting only an unfinished *last* measure."""
    score.validate()
    position = 0
    for row, note in enumerate(score.parts[part].notes):
        offset, length = score.bar_room(position)
        if offset + note.ticks > length:
            raise LocatedError(tr("{where}的音超出小節線。請先縮短時值或拆分跨小節長音。").format(
                where=note_place(score, part, row, with_part=True)), row)
        position += note.ticks
    return score.part_bars(part)


def _context(score: Score) -> tuple[list[list[tuple[int, int]]], list[list[Entry]]]:
    ranges = [measure_ranges(score, index) for index in range(len(score.parts))]
    if error := score.notation_error():
        raise ValueError(tr("請先修正連線或反覆記號，再編輯小節：{detail}").format(detail=error))
    if len({len(r) for r in ranges}) > 1:
        raise ValueError(tr("各聲部的小節數不同；請先補齊各聲部，再編輯小節。"))
    return ranges, [[(note, (0, row)) for row, note in enumerate(part.notes)] for part in score.parts]


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
        raise ValueError(tr("剩餘拍數無法用目前支援的休止符表示；請調整附點三十二分音符的時值。"))
    return fills[ticks]


def _bar_selection(indices: Iterable[int], count: int) -> list[int]:
    try:
        raw = list(indices)
    except TypeError as exc:
        raise ValueError(tr("請先選取要編輯的小節。")) from exc
    if not raw:
        raise ValueError(tr("請先選取要編輯的小節。"))
    if any(type(bar) is not int or not 0 <= bar < count for bar in raw):
        raise ValueError(tr("選取的小節不存在。"))
    return sorted(set(raw))


def _refuse_pickup(score: Score, bars) -> None:
    if score.pickup and 0 in bars:
        raise ValueError(tr("弱起小節不能刪除或重複，也不能在它前面新增小節；可以清空它。"))


def connections(score: Score, part: int) -> list[Connection]:
    """Validated note connections of one part (rows are note rows): lines and ties."""
    notes = score.parts[part].notes
    out = []
    for family, start, end, start_id in span_pairs(notes):
        end_id = next(s for s in notes[end].spans_end if symbols.SYMBOLS[s].family == family)
        out.append(Connection((start, end), ((start, "spans_start", start_id), (end, "spans_end", end_id)), True, False))
    for row, note in enumerate(notes):
        if note.tie:
            out.append(Connection((row, row + 1), ((row, "tie", True),), True, False))
    return out


# A both-ways repeat ``:‖:`` closes one repeat and opens the next. Each half is kept with its
# own repeat (as a plain end / start) and joined again when both survive side by side.
_BOTH_CLOSE, _BOTH_OPEN = "bar.repeat_both:close", "bar.repeat_both:open"


def bar_connections(score: Score) -> list[Connection]:
    """Paired bar items of part 0 (rows are *bar* indices): repeats with their endings, and
    navigation. Bar items belong to bar lines, so they follow the bars, not the notes."""
    items = bar_items(score)
    out, group, navigation = [], [], []
    for bar, (starts, ends) in enumerate(items):
        if bar and "bar.repeat_both" in items[bar - 1][1]:
            group.append((bar, "bar_start", _BOTH_OPEN))
        group += [(bar, "bar_start", s) for s in starts if s in REPEAT_ITEMS]
        navigation += [(bar, "bar_start", s) for s in starts if s in NAVIGATION]
        group += [(bar, "bar_end", _BOTH_CLOSE if s == "bar.repeat_both" else s) for s in ends if s in REPEAT_ITEMS]
        navigation += [(bar, "bar_end", s) for s in ends if s in NAVIGATION]
        following = items[bar + 1][0] if bar + 1 < len(items) else ()
        closes = "volta.end" in ends or (("bar.repeat_end" in ends or "bar.repeat_both" in ends)
                                         and "volta.1" not in following)
        if group and closes:
            out.append(Connection(tuple(sorted({b for b, _, _ in group})), tuple(group), True, False))
            group = []
    # Segno/To Coda/D.S. belong together; D.C. and Fine stand alone (Fine becomes print-only).
    linked = [item for item in navigation if item[2] in ("nav.segno", "nav.tocoda", "nav.ds")]
    if linked:
        out.append(Connection(tuple(sorted({b for b, _, _ in linked})), tuple(linked), False, True))
    out += [Connection((b,), ((b, name, item),), False, True) for b, name, item in navigation
            if item not in ("nav.segno", "nav.tocoda", "nav.ds")]
    return out


def _strip(note: Note, *, bars: bool = False) -> tuple[Note, int]:
    """Remove ties and line ends (and with ``bars`` every bar item); return the note and how
    many ties and line ends were removed (bar items are counted by their callers)."""
    removed = int(note.tie) + len(note.spans_start) + len(note.spans_end)
    if not removed and not (bars and (note.bar_start or note.bar_end)):
        return note, 0
    if bars:
        return replace(note, tie=False, spans_start=(), spans_end=(), bar_start=(), bar_end=()), removed
    return replace(note, tie=False, spans_start=(), spans_end=()), removed


def _restore(note: Note, name: str, item) -> Note:
    if name == "tie":
        return replace(note, tie=True)
    return replace(note, **{name: symbols.sort_ids([*getattr(note, name), item])})


BarOrigin = tuple[int, int] | None


def _restore_bars(score: Score, notes: list[Note], bar_origins: list[BarOrigin]) -> int:
    """Put part 0's bar items back on the bars they belong to; return how many paired items of
    the kept bars were lost (half pairs, navigation in copies).

    ``bar_origins[j]`` names the original bar (copy generation, bar) that output bar ``j``
    is, or None for a new bar. A bar keeps its bar lines when its contents are replaced
    (fill, clear); paired items survive only when every bar of the pair survives in order
    (repeats also need the bars between unchanged and contiguous).
    """
    ranges = replace(score, parts=[replace(score.parts[0], notes=notes)]).part_bars(0)
    if len(ranges) != len(bar_origins):
        raise AssertionError("measure edit lost track of its bars")
    original = bar_items(score)
    where = {origin: j for j, origin in enumerate(bar_origins) if origin is not None}

    def put(j, name, item):
        row = ranges[j][0] if name == "bar_start" else ranges[j][1] - 1
        notes[row] = _restore(notes[row], name, item)
        return row

    for j, origin in enumerate(bar_origins):
        if origin is not None:
            starts, ends = original[origin[1]]
            for item in starts:
                if item not in PAIRED_BAR_ITEMS:
                    put(j, "bar_start", item)
            for item in ends:
                if item not in PAIRED_BAR_ITEMS:
                    put(j, "bar_end", item)
    expected = sum(s in PAIRED_BAR_ITEMS for origin in bar_origins if origin is not None
                   for s in original[origin[1]][0] + original[origin[1]][1])
    preserved, halves = 0, {}
    generations = sorted({origin[0] for origin in where})
    for connection in bar_connections(score):
        for generation in generations:
            if generation and connection.navigation:
                continue                        # copies never repeat D.S./D.C./Fine/Segno/To Coda
            bars = [where.get((generation, bar)) for bar in connection.rows]
            if None in bars or bars != sorted(bars):
                continue
            first, span = connection.rows[0], connection.rows[-1] - connection.rows[0]
            if connection.strict and (bars[-1] - bars[0] != span or any(
                    bar_origins[bars[0] + k] != (generation, first + k) for k in range(span + 1))):
                continue
            for bar, name, item in connection.items:
                j = where[(generation, bar)]
                if item == _BOTH_CLOSE:
                    halves.setdefault((generation, bar), {})["close"] = put(j, name, "bar.repeat_end")
                elif item == _BOTH_OPEN:
                    halves.setdefault((generation, bar - 1), {})["open"] = put(j, name, "bar.repeat_start")
                else:
                    put(j, name, item)
                    preserved += 1
    for half in halves.values():
        if "close" in half and "open" in half:
            preserved += 1                      # both repeats survived
            close, opened = half["close"], half["open"]
            if opened == close + 1:             # still side by side: one :‖: again
                notes[close] = replace(notes[close], bar_end=symbols.sort_ids(
                    [s for s in notes[close].bar_end if s != "bar.repeat_end"] + ["bar.repeat_both"]))
                notes[opened] = replace(notes[opened], bar_start=tuple(
                    s for s in notes[opened].bar_start if s != "bar.repeat_start"))
    return expected - preserved


def _finish_part(score: Score, part: int, entries: list[Entry],
                 bar_origins: list[BarOrigin] | None = None) -> tuple[list[Note], int]:
    positions, groups, run_starts = {}, set(), []
    for row, (_, origin) in enumerate(entries):
        if origin is not None:
            positions[origin] = row
            groups.add(origin[0])
        previous = entries[row-1][1] if row else None
        consecutive = (origin is not None and previous is not None
                       and origin == (previous[0], previous[1]+1))
        run_starts.append(run_starts[-1] if consecutive else row)
    bars = part == 0 and bar_origins is not None
    notes, removed = [], 0
    for note, _ in entries:
        note, count = _strip(note, bars=bars)
        notes.append(note)
        removed += count
    preserved = 0
    for connection in connections(score, part):
        for group in groups:
            rows = [positions.get((group, row)) for row in connection.rows]
            if None in rows or rows != sorted(rows):
                continue
            first, last = rows[0], rows[-1]
            # Endpoint identities plus an unchanged contiguous span prevent a
            # deleted tie from accidentally joining a new, equal-pitch neighbor.
            if connection.strict and (run_starts[last] > first or last - first != connection.rows[-1] - connection.rows[0]):
                continue
            for row, name, item in connection.items:
                target = positions[(group, row)]
                notes[target] = _restore(notes[target], name, item)
                preserved += 1
    if bars:
        removed += _restore_bars(score, notes, bar_origins)
    return notes, removed - preserved


def _finish(score: Score, entries: list[list[Entry]], bar_origins: list[BarOrigin],
            selection=(), current=None) -> MeasureEdit:
    if sum(map(len, entries)) > MAX_NOTES:
        raise ValueError(tr("音符太多（上限 10,000）；請減少新增或複製的小節數。"))
    parts, dropped = [], 0
    for index, part_entries in enumerate(entries):
        notes, count = _finish_part(score, index, part_entries, bar_origins)
        parts.append(notes)
        dropped += count
    result_score = replace(score, parts=[replace(p, notes=n) for p, n in zip(score.parts, parts)])
    for index in range(len(parts)):
        measure_ranges(result_score, index)
    if error := result_score.notation_error():
        raise ValueError(tr("這項小節修改無法安全保留原有記號：{detail}").format(detail=error))
    selection = tuple(selection)
    current = selection[0] if current is None and selection else -1 if current is None else current
    notice = tr("已移除 {count} 個受影響的連線、漸強弱或反覆記號，可用復原還原。").format(count=dropped) if dropped else ""
    return MeasureEdit(parts[0], selection, current, notice, tuple(parts))


def _complete_tail(score: Score, part: int, entries: list[Entry]) -> None:
    """Complete an unfinished last bar to the next bar line (so more bars can follow)."""
    offset, length = score.bar_room(sum(note.ticks for note in score.parts[part].notes))
    entries.extend((rest, None) for rest in _rests((length - offset) % length))


def insert_measures(score: Score, at_bar: int, count: int = 1) -> MeasureEdit:
    """Insert complete rest measures before ``at_bar`` in every part; len(ranges) appends.

    Appending after an unfinished final measure completes that measure first.
    Inserting before it leaves its existing, unfinished duration unchanged.
    """
    ranges, entries = _context(score)
    bars = len(ranges[0])
    if type(at_bar) is not int or not 0 <= at_bar <= bars:
        raise ValueError(tr("新增小節的位置不存在。"))
    if type(count) is not int or count < 1:
        raise ValueError(tr("新增小節數須為正整數。"))
    if bars:
        _refuse_pickup(score, [at_bar])
    rests = _rests(score.bar_ticks if bars or not score.pickup else score.pickup)
    first_bar_is_pickup = not bars and score.pickup
    if sum(map(len, entries)) + count * len(rests) * len(entries) > MAX_NOTES:
        raise ValueError(tr("音符太多（上限 10,000）；請減少新增的小節數。"))
    selection = ()
    for index, part_entries in enumerate(entries):
        if at_bar == bars:
            _complete_tail(score, index, part_entries)
            position = len(part_entries)
        else:
            position = ranges[index][at_bar][0]
        added = [(rest, None) for rest in rests]
        if first_bar_is_pickup:
            added += [(rest, None) for _ in range(count - 1) for rest in _rests(score.bar_ticks)]
        else:
            added = [(rest, None) for _ in range(count) for rest in rests]
        part_entries[position:position] = added
        if index == 0:
            selection = range(position, position + len(added))
    kept = [(0, bar) for bar in range(bars)]
    return _finish(score, entries, kept[:at_bar] + [None] * count + kept[at_bar:], selection)


def delete_measures(score: Score, bar_indices: Iterable[int]) -> MeasureEdit:
    """Delete selected whole measures in every part, selecting the nearest surviving one."""
    ranges, entries = _context(score)
    selected = _bar_selection(bar_indices, len(ranges[0]))
    _refuse_pickup(score, selected)
    removed = set(selected)
    kept = [[entry for bar, (start, end) in enumerate(part_ranges) if bar not in removed
             for entry in part_entries[start:end]] for part_ranges, part_entries in zip(ranges, entries)]
    result = _finish(score, kept, [(0, bar) for bar in range(len(ranges[0])) if bar not in removed])
    remaining = replace(score, parts=[replace(p, notes=n) for p, n in zip(score.parts, result.part_notes())]).part_bars(0)
    if remaining:
        start, end = remaining[min(selected[0], len(remaining)-1)]
        return replace(result, selection=tuple(range(start, end)), current=start)
    return result


def clear_measures(score: Score, bar_indices: Iterable[int]) -> MeasureEdit:
    """Keep selected measure positions in every part and replace their contents with rests."""
    ranges, entries = _context(score)
    selected = set(_bar_selection(bar_indices, len(ranges[0])))
    out, selection = [], []
    for index, (part_ranges, part_entries) in enumerate(zip(ranges, entries)):
        result = []
        for bar, (start, end) in enumerate(part_ranges):
            if bar in selected:
                rests = _rests(score.pickup if bar == 0 and score.pickup else score.bar_ticks)
                if index == 0:
                    selection.extend(range(len(result), len(result)+len(rests)))
                result.extend((rest, None) for rest in rests)
            else:
                result.extend(part_entries[start:end])
        out.append(result)
    # Clearing replaces a bar's contents; the bar and its bar lines (repeats, jumps) stay.
    return _finish(score, out, [(0, bar) for bar in range(len(ranges[0]))], selection)


def duplicate_measures(score: Score, bar_indices: Iterable[int]) -> MeasureEdit:
    """Duplicate consecutive measures (every part) immediately after themselves.

    A selected unfinished tail is first completed in both original and copy.
    Complete internal connections are copied; partial connections are removed,
    and navigation (Segno, To Coda, D.S., D.C., Fine) is never copied.
    """
    ranges, entries = _context(score)
    selected = _bar_selection(bar_indices, len(ranges[0]))
    if selected != list(range(selected[0], selected[-1]+1)):
        raise ValueError(tr("複製小節需選取連續的小節。"))
    _refuse_pickup(score, selected)
    selection = ()
    for index, (part_ranges, part_entries) in enumerate(zip(ranges, entries)):
        start, end = part_ranges[selected[0]][0], part_ranges[selected[-1]][1]
        if selected[-1] == len(part_ranges)-1:
            _complete_tail(score, index, part_entries)
            end = len(part_entries)
        copied = [(note, (1, origin[1]) if origin is not None else None) for note, origin in part_entries[start:end]]
        part_entries[end:end] = copied
        if index == 0:
            selection = range(end, end+len(copied))
    kept = [(0, bar) for bar in range(len(ranges[0]))]
    after = selected[-1] + 1
    return _finish(score, entries, kept[:after] + [(1, bar) for bar in selected] + kept[after:], selection)


def _input_connections(note: Note) -> bool:
    return bool(note.tie or note.spans_start or note.spans_end or note.bar_start or note.bar_end)


def replace_rest_input(score: Score, row: int, note: Note, part_index: int = 0) -> MeasureEdit:
    """Fill existing consecutive rests of one part without moving any later beat or bar.

    The replacement stays inside one measure, never consumes a pitched note,
    and may consume additional adjacent rests to obtain an exact remainder.
    Unwritten time at the end of an unfinished measure is not an existing rest.
    Marks on the replaced rest and its bar lines stay; lines through it are cut.
    """
    ranges, entries = _context(score)
    part = entries[part_index]
    notes = score.parts[part_index].notes
    if type(row) is not int or not 0 <= row < len(part):
        raise ValueError(tr("請先選取要填入音符的休止符。"))
    if not isinstance(note, Note):
        raise ValueError(tr("輸入音符格式不正確。"))
    note.validate()
    if notes[row].degree != 0:
        raise ValueError(tr("填入音符只會覆寫休止符；請選取休止符或切換到修改音符。"))
    if _input_connections(note):
        raise ValueError(tr("請先填入音符，再選取範圍加入連線或反覆記號。"))
    previous = notes[row]
    if previous.tuplet:
        if note.duration != previous.duration or note.dotted:
            raise ValueError(tr("填入三連音休止符時須保持原時值；請選取完整三連音群組調整時值。"))
        note = replace(note, tuplet=previous.tuplet, tuplet_start=previous.tuplet_start, tuplet_end=previous.tuplet_end)
    elif note.tuplet:
        raise ValueError(tr("請先填入音符，再選取完整的三個音符設定三連音。"))
    _, bar_end = next((start, end) for start, end in ranges[part_index] if start <= row < end)
    consumed, end, remainder = 0, row, None
    while end < bar_end and notes[end].degree == 0:
        rest = notes[end]
        if end != row and rest.marks:
            raise ValueError(tr("相鄰休止符含力度或演奏記號；請先調整該記號，避免覆寫時遺失。"))
        consumed += rest.ticks
        end += 1
        if consumed >= note.ticks:
            try:
                remainder = _rests(consumed-note.ticks)
                break
            except ValueError:
                # A too-short remainder may become representable after another
                # rest, e.g. replacing a dotted 32nd plus 32nd with a 32nd.
                continue
    if remainder is None:
        if consumed >= note.ticks:
            raise ValueError(tr("剩餘拍數無法用目前支援的休止符表示；請調整附點或時值。"))
        raise ValueError(tr("這個音符超過同小節內可用的連續休止拍數；不會覆蓋已有音符或推移後續小節。"))
    # Keep the rest's marks (the typed note wins a conflict slot) and its bar line items.
    marks = note.marks
    for mark in previous.marks:
        slot = symbols.SYMBOLS[mark].slot
        if mark not in marks and not (slot and any(symbols.SYMBOLS[m].slot == slot for m in marks)):
            marks = symbols.sort_ids([*marks, mark])
    note = replace(note, marks=marks)
    single = end == row+1 and not remainder
    unchanged = single and note == replace(previous, spans_start=(), spans_end=(), bar_start=(), bar_end=())
    # Entering the same rest is a no-op, including its existing bar repeat.
    # Connection flags are not input attributes and must not make it an edit.
    if unchanged:
        note = previous
    origin = part[row][1] if unchanged else None
    part[row:end] = [(note, origin)] + [(rest, None) for rest in remainder]
    # The bars do not move: their bar lines, repeats and jumps stay where they are.
    return _finish(score, entries, [(0, bar) for bar in range(len(ranges[0]))], (row,), row)


def _same_pitch(a: Note, b: Note) -> bool:
    return (a.degree, a.octave, a.accidental.replace("n", "")) == (b.degree, b.octave, b.accidental.replace("n", ""))


def clip_notes(score: Score, part: int, rows: Iterable[int]) -> list[Note]:
    """The selected notes as they go to the clipboard, judged against the score they come from.

    The clipboard holds a flat list, so only what is complete *inside the selection* is kept:
    a line or tie whose notes are all selected and contiguous, and (part 1) the bar items of
    fully selected bars -- a repeat or ending group only when all of its bars are selected.
    Navigation (Segno, To Coda, D.S., D.C., Fine) is never copied: it may appear once.
    """
    notes = score.parts[part].notes
    rows = sorted({r for r in rows if 0 <= r < len(notes)})
    index = {row: i for i, row in enumerate(rows)}
    out = [_strip(notes[r], bars=True)[0] for r in rows]
    for family, start, end, start_id in span_pairs(notes):
        if all(r in index for r in range(start, end + 1)):
            end_id = next(s for s in notes[end].spans_end if symbols.SYMBOLS[s].family == family)
            out[index[start]] = _restore(out[index[start]], "spans_start", start_id)
            out[index[end]] = _restore(out[index[end]], "spans_end", end_id)
    for row in rows:
        if notes[row].tie and row + 1 in index:
            out[index[row]] = replace(out[index[row]], tie=True)
    if part == 0 and not score.notation_error():
        ranges = score.part_bars(0)
        items = bar_items(score)
        whole = {bar for bar, (a, b) in enumerate(ranges) if all(r in index for r in range(a, b))}
        def put(bar, name, item):
            row = ranges[bar][0] if name == "bar_start" else ranges[bar][1] - 1
            out[index[row]] = _restore(out[index[row]], name, item)
        for bar in sorted(whole):
            starts, ends = items[bar]
            for item in starts:
                if item not in PAIRED_BAR_ITEMS:
                    put(bar, "bar_start", item)
            for item in ends:
                if item not in PAIRED_BAR_ITEMS:
                    put(bar, "bar_end", item)
        for connection in bar_connections(score):
            if connection.navigation or not all(bar in whole for bar in range(connection.rows[0], connection.rows[-1] + 1)):
                continue
            for bar, name, item in connection.items:
                if item == _BOTH_CLOSE:
                    put(bar, name, "bar.repeat_end")
                elif item == _BOTH_OPEN:
                    put(bar, name, "bar.repeat_start")
                else:
                    put(bar, name, item)
    return out


def clean_pasted(notes: list[Note], *, keep_bar_items: bool = False) -> tuple[list[Note], int]:
    """Pasted notes keep their marks and complete lines; half lines and bar items are removed.

    ``clip_notes`` already dropped what was incomplete in the copied selection; this re-checks
    clipboard content on its own (e.g. from another window): lines pair by family inside the
    pasted notes, and a tie needs the next pasted note at the same pitch -- it never joins a
    note that happens to follow the paste position. Bar items belong to bar positions, so they
    are kept only with ``keep_bar_items`` (the caller checks that they land on bar lines).
    Returns the cleaned notes and how many items were removed (for a notice).
    """
    cleaned, removed = [], 0
    for row, note in enumerate(notes):
        stripped, count = _strip(note, bars=not keep_bar_items)
        removed += count + (0 if keep_bar_items else len(note.bar_start) + len(note.bar_end))
        tie = note.tie and row + 1 < len(notes) and _same_pitch(note, notes[row + 1])
        cleaned.append(replace(stripped, tie=tie))
        removed -= int(tie)
    open_lines = {}
    for row, note in enumerate(notes):
        for end_id in note.spans_end:
            family = symbols.SYMBOLS[end_id].family
            if family in open_lines:
                start, start_id = open_lines.pop(family)
                cleaned[start] = _restore(cleaned[start], "spans_start", start_id)
                cleaned[row] = _restore(cleaned[row], "spans_end", end_id)
                removed -= 2
        for start_id in note.spans_start:
            open_lines[symbols.SYMBOLS[start_id].family] = (row, start_id)
    return cleaned, removed
