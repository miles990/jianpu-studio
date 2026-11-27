"""Local rhythm edits: changing, deleting or inserting notes never moves a later bar line.

Every function works on one part and returns a ``RhythmEdit`` (the part's new notes, the
rows to select and a notice) or raises ``RhythmRefused`` with a message that names the place
(「第 3 小節第 2 拍」) and, when there is one, the choices the user can take instead.

* A shorter note leaves its freed time as rests right after it (split at beats).
* A longer note takes the rests that follow it in the same bar; with too little room the
  edit is refused and the choices are 「改為 X（放得下）」 or 「覆蓋後面的音」. Every note
  keeps its tick position unless it is consumed, so several selected runs never take the
  same time twice, and a selected note is never overwritten.
* Deleting notes in the middle leaves rests of the same length; deleting at the very end
  of a part removes them only when no bar item goes with them and the parts stay equally
  long (otherwise they become rests too).
* Inserting in the middle takes rests that follow in the same bar (the notes between move
  right inside the bar); a partly used rest keeps its marks and text. Whole bars inserted on
  a bar line add the same bars of rests to the other parts; notes added at the end of one
  part add rests to the end of the others. Otherwise the only ripple is the explicit choice
  「整首往後推移」, offered only when every later bar stays well formed.

Bar items (bar lines, repeats, endings, jumps) belong to the bar, not to a note: they are
taken off before a bar is edited and put back on its first / last note afterwards. Notes are
tracked by identity through an edit: a tie survives only between the same two notes, a line
only between its own two ends. What an edit removes is reported by ``edit_guard`` before it
is applied.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from . import symbols
from .i18n import tr
from .model import DURATIONS, MAX_NOTES, Note, rest_fill, tuplet_ranges
from .notation_rules import _part_label, bar_label, beat_ticks, note_place


@dataclass(frozen=True)
class Choice:
    key: str          # "fit" | "overwrite" | "ripple" | "confirm"
    label: str


class RhythmRefused(ValueError):
    def __init__(self, message: str, part: int = 0, row: int | None = None, choices: tuple[Choice, ...] = (),
                 fit: Note | None = None, *, title: str = ""):
        super().__init__(message)
        self.part, self.row, self.choices, self.fit = part, row, choices, fit
        self.title = title
        self.losses = {}          # choice key -> edit_guard items that choice removes (filled by the editor)


@dataclass(frozen=True)
class RhythmEdit:
    notes: list[Note]
    selection: tuple[int, ...]
    current: int
    notice: str = ""
    others: dict | None = None        # other parts' new notes (whole-bar inserts, tail input)
    explicit: object = None           # edit_guard predicate: what the command removes on purpose


def has_choice(choice: str, key: str) -> bool:
    """Choices accumulate as "fit+confirm" when the user answered more than one question."""
    return key in (choice or "").split("+")


# ---------------------------------------------------------------- helpers
def _positions(notes) -> list[int]:
    out, position = [], 0
    for note in notes:
        out.append(position)
        position += note.ticks
    return out


def beat_rests(score, start: int, ticks: int) -> list[Note]:
    """Rests covering ``ticks`` from tick ``start``, one per beat as jianpu writes them (0 0 0),
    with the partial beats at either end decomposed exactly. Raises ValueError when the
    length cannot be written with rests (e.g. 1/16 beat left by a dotted 32nd)."""
    out, position, end = [], start, start + ticks
    beat = beat_ticks(score.meter)
    try:
        while position < end:
            offset, length = score.bar_room(position)
            bar_start = position - offset
            if score.pickup and position < score.first_bar_ticks:
                grid = score.bar_ticks - score.first_bar_ticks      # pickup beats count from the bar's end
            else:
                grid = 0
            to_beat = (beat - (offset + grid) % beat) % beat or beat
            step = min(end, position + to_beat, bar_start + length) - position
            out += rest_fill(step)
            position += step
        return out
    except ValueError:
        # A partial beat of 1/16 beat cannot be written on its own; written as one group it may be.
        return rest_fill(ticks)


def _bar_span(score, positions, notes, row):
    """(first row, end row, bar start tick, bar end tick) of the bar holding ``row``."""
    position = positions[row] if row < len(positions) else (positions[-1] + notes[-1].ticks if notes else 0)
    offset, length = score.bar_room(position)
    start_tick, end_tick = position - offset, position - offset + length
    first = row
    while first > 0 and positions[first - 1] >= start_tick:
        first -= 1
    end = row
    while end < len(notes) and positions[end] < end_tick:
        end += 1
    if end > first and positions[end - 1] + notes[end - 1].ticks > end_tick:
        raise RhythmRefused(tr("{bar}的拍數本身不正確（有音跨過小節線）；請先修正這一小節。").format(
            bar=bar_label(score, score.bar_index(start_tick))), row=first)
    return first, end, start_tick, end_tick


def _same_pitch(a: Note, b: Note) -> bool:
    return a.degree != 0 and (a.degree, a.octave, a.accidental.replace("n", "")) == (
        b.degree, b.octave, b.accidental.replace("n", ""))


def _plain_rest(note: Note) -> bool:
    return (note.degree == 0 and not note.tuplet and not note.has_marks and not note.text)


def _pairs(notes, tag):
    """The lines of ``notes`` as (family, start origin, end origin, start id, end id), or None
    when they do not pair (an existing problem is left as it is)."""
    from .notation_rules import _pair_spans
    try:
        found = list(_pair_spans(notes))
    except ValueError:
        return None
    out = []
    for family, start, end, start_id in found:
        end_id = next(s for s in notes[end].spans_end if symbols.SYMBOLS[s].family == family)
        out.append((family, (tag, start), (tag, end), start_id, end_id))
    return out


def _succ(origin):
    return None if origin is None else (origin[0], origin[1] + 1)


def settle(entries, pairs) -> tuple[list[Note], int]:
    """The notes of ``entries`` [(note, origin)], keeping a tie only between the two notes it
    joined (still adjacent, same pitch) and a line only between its own two ends (a
    glissando only between neighbours). Returns the notes and how many ends were removed."""
    notes = [note for note, _ in entries]
    at = {origin: i for i, (_, origin) in enumerate(entries) if origin is not None}
    removed = 0
    for i, (note, origin) in enumerate(entries):
        if note.tie and not (i + 1 < len(entries) and origin is not None
                             and entries[i + 1][1] == _succ(origin) and _same_pitch(note, notes[i + 1])):
            notes[i] = replace(notes[i], tie=False)
            removed += 1
    for family, start, end, start_id, end_id in pairs or ():
        a, b = at.get(start), at.get(end)
        if a is not None and b is not None and a < b and (family != "gliss" or b == a + 1):
            continue
        if a is not None and start_id in notes[a].spans_start:
            notes[a] = replace(notes[a], spans_start=tuple(s for s in notes[a].spans_start if s != start_id))
            removed += 1
        if b is not None and end_id in notes[b].spans_end:
            notes[b] = replace(notes[b], spans_end=tuple(s for s in notes[b].spans_end if s != end_id))
            removed += 1
    return notes, removed


def _notice(removed: int) -> str:
    return tr("已移除 {count} 個失去另一端的連線、延音線或大滑音，可用復原還原。").format(count=removed) if removed else ""


def _beats_text(ticks: int) -> str:
    from fractions import Fraction
    value = Fraction(ticks, 48)
    return str(value)


def _duration_name(note: Note) -> str:
    return tr(DURATIONS[note.duration]).split(" · ")[0] + (tr("（附點）") if note.dotted else "")


def _fit(old: Note, new: Note, room: int) -> Note | None:
    """Longest plain or dotted value of ``new``'s kind that fits ``room`` ticks and is longer than ``old``."""
    best = None
    for duration in DURATIONS:
        for dotted in (False, True):
            if new.tuplet and dotted:
                continue
            candidate = replace(new, duration=duration, dotted=dotted)
            try:
                candidate.validate()
            except ValueError:
                continue
            if old.ticks < candidate.ticks <= room and (best is None or candidate.ticks > best.ticks):
                best = candidate
    return best


def _tail_room(score, part: int, total: int) -> int:
    """How far this part may grow past its written end: to the bar line with one part; with
    several parts only up to the longest other part (parts keep the same length)."""
    others = [sum(n.ticks for n in p.notes) for i, p in enumerate(score.parts) if i != part]
    if not others:
        return 1 << 30
    return max(0, max(others) - total)


def _gap_rests(score, notes, entries_after, start, ticks, keep):
    """Rests for [start, start+ticks); when that length cannot be written, absorb the plain
    rests that follow (rows ``entries_after``) until it can. Returns (rests, absorbed rows)
    or raises ValueError."""
    absorbed = []
    while True:
        try:
            return (beat_rests(score, start, ticks) if ticks else []), absorbed
        except ValueError:
            if not entries_after:
                raise
            row = entries_after.pop(0)
            if row in keep or not _plain_rest(notes[row]):
                raise
            absorbed.append(row)
            ticks += notes[row].ticks


def _unwritable(score, part, row, ticks) -> RhythmRefused:
    return RhythmRefused(_part_label(score, part) + tr(
        "{where}：改了以後會剩下 {beats} 拍，無法用休止符表示；請改用其他時值，或先調整後面的休止符。").format(
        where=note_place(score, part, row), beats=_beats_text(ticks)), part, row)


# ---------------------------------------------------------------- change duration
@dataclass
class _Problem:
    row: int
    reason: str
    fit: Note | None = None
    overwrite: bool = False   # overwriting the following notes would make room
    bar: int = -1             # -1: no choice can help (the message says what to do instead)


def change_notes(score, part: int, rows, change, *, choice: str = "") -> RhythmEdit:
    """Apply ``change(note) -> note`` to the selected rows, keeping every bar line in place.

    ``choice`` is "" (refuse when there is no room), "fit" (the longest value that fits) or
    "overwrite" (take the following notes of the same bar too -- never a selected note)."""
    try:
        return _change(score, part, rows, change, choice)
    except RhythmRefused as refused:
        if has_choice(choice, "fit") and refused.fit is not None:
            fit = refused.fit
            return _change(score, part, rows, lambda n: replace(change(n), duration=fit.duration, dotted=fit.dotted),
                           "")
        raise


def _change(score, part, rows, change, choice) -> RhythmEdit:
    notes = list(score.parts[part].notes)
    rows = sorted({r for r in rows if 0 <= r < len(notes)})
    if not rows:
        return RhythmEdit(notes, (), -1)
    label = _part_label(score, part)
    # Tuplet members change only as whole groups (all three, same new value).
    groups = {start: end for start, end in tuplet_ranges(notes, lambda r: note_place(score, part, r))}
    member = {r: (start, end) for start, end in groups.items() for r in range(start, end)}
    changed = {r: change(notes[r]) for r in rows}
    for r in rows:
        new = changed[r]
        if new.ticks == notes[r].ticks:
            continue
        if r in member:
            start, end = member[r]
            if any(x not in changed for x in range(start, end)) or len(
                    {(changed[x].duration, changed[x].dotted) for x in range(start, end)}) != 1:
                raise RhythmRefused(label + tr("{where}是三連音；要改時值請選取整組三個音一起改。").format(
                    where=note_place(score, part, r)), part, r)
    for r, new in changed.items():
        try:
            new.validate()
        except ValueError as exc:
            raise RhythmRefused(label + note_place(score, part, r) + "：" + str(exc), part, r) from exc
    pairs = _pairs(notes, "o")
    entries = [(note, ("o", i)) for i, note in enumerate(notes)]
    if all(changed[r].ticks == notes[r].ticks for r in rows):
        for r in rows:
            entries[r] = (changed[r], ("o", r))
        out, removed = settle(entries, pairs)
        return RhythmEdit(out, tuple(rows), rows[0], _notice(removed))
    positions = _positions(notes)
    bars: dict[int, list[int]] = {}
    spans = {}
    for r in rows:
        try:
            first, end, start_tick, end_tick = _bar_span(score, positions, notes, r)
        except RhythmRefused:
            # The bar is already broken (a note crosses its bar line): there is no bar to keep,
            # so change the values in place; shortening the crossing note is how it gets fixed.
            for x in rows:
                entries[x] = (changed[x], ("o", x))
            out, removed = settle(entries, pairs)
            return RhythmEdit(out, tuple(rows), rows[0], _notice(removed))
        bars.setdefault(first, []).append(r)
        spans[first] = (end, start_tick, end_tick)
    total = positions[-1] + notes[-1].ticks
    tail = _tail_room(score, part, total)
    selected = set(rows)
    tuplets = {r: list(range(a, b)) for a, b in groups.items() for r in range(a, b)}
    problems: list[_Problem] = []
    new_bars = {}
    for first in sorted(bars):
        end, start_tick, end_tick = spans[first]
        limit = end_tick if end < len(notes) else min(end_tick, total + tail)
        new_bars[first] = _change_bar(score, part, notes, positions, first, end, limit, bars[first], changed,
                                      selected, tuplets, has_choice(choice, "overwrite"), problems)
    if problems:
        raise _change_refusal(problems, label, part, choice)
    for first in sorted(new_bars, reverse=True):
        entries[first:spans[first][0]] = new_bars[first]
    if sum(len(p.notes) for p in score.parts) - len(notes) + len(entries) > MAX_NOTES:
        raise RhythmRefused(tr("音符太多（上限 10,000）。"), part)
    out, removed = settle(entries, pairs)
    wanted = {("o", r) for r in rows}
    new_rows = [i for i, (_, origin) in enumerate(entries) if origin in wanted]
    return RhythmEdit(out, tuple(new_rows), new_rows[0] if new_rows else -1, _notice(removed))


def _runs(rows):
    runs, run = [], []
    for r in rows:
        if run and r != run[-1] + 1:
            runs.append(run)
            run = []
        run.append(r)
    return runs + [run]


def _change_bar(score, part, notes, positions, first, end, limit, rows, changed, selected, tuplets, overwrite,
                problems):
    """New entries [(note, origin)] of one bar (rows [first, end)); None after adding to ``problems``.

    Each selected run keeps its start tick; every other note keeps its tick unless it is
    consumed, so runs never compete for the same time."""
    gone, added, failed = set(), [], False
    for run in _runs(rows):
        a, b = run[0], run[-1] + 1
        start = positions[a]
        old = sum(notes[r].ticks for r in run)
        new = sum(changed[r].ticks for r in run)
        gone.update(run)
        position = start
        for r in run:
            added.append((position, replace(changed[r], bar_start=(), bar_end=()), ("o", r)))
            position += changed[r].ticks
        if new <= old:
            gap_start, gap_end, k = start + new, start + old, b
        else:
            need_end = start + new
            covered, k = start + old, b
            taken, needs_overwrite, hard, free_end = [], False, False, None
            while covered < need_end and k < end:
                group = tuplets.get(k, [k])
                if any(x in selected for x in group):
                    hard = True
                    free_end = covered if free_end is None else free_end
                    break
                if len(group) > 1 or any(notes[x].degree for x in group):
                    needs_overwrite = True
                    free_end = covered if free_end is None else free_end
                taken += group
                covered += sum(notes[x].ticks for x in group)
                k = group[-1] + 1
            if covered < need_end and not hard and k == len(notes):
                covered = min(need_end, limit)                 # the unwritten end of the part
            fits = covered >= need_end
            if hard or not fits or (needs_overwrite and not overwrite):
                free = (free_end if free_end is not None else covered) - start - old
                where = note_place(score, part, a)
                if free_end is not None:
                    reason = tr("{where}後面到下一個音之前只剩 {beats} 拍休止，放不下改長的音（需要多 {need} 拍）。")
                else:
                    reason = tr("{where}之後到小節線只剩 {beats} 拍，放不下改長的音（需要多 {need} 拍）。")
                fit = _fit(notes[a], changed[a], old + free) if len(run) == 1 else None
                problems.append(_Problem(a, reason.format(where=where, beats=_beats_text(max(0, free)),
                                                          need=_beats_text(new - old)),
                                         fit, fits and not hard and needs_overwrite, score.bar_index(start)))
                failed = True
                continue
            gone.update(taken)
            gap_start, gap_end = need_end, max(covered, need_end)
        try:
            rests, absorbed = _gap_rests(score, notes, list(range(k, end)), gap_start, gap_end - gap_start, selected)
        except ValueError:
            problems.append(_Problem(a, str(_unwritable(score, part, a, gap_end - gap_start))))
            failed = True
            continue
        gone.update(absorbed)
        position = gap_start
        for rest in rests:
            added.append((position, rest, None))
            position += rest.ticks
    if failed:
        return None
    starts, ends = notes[first].bar_start, notes[end - 1].bar_end
    kept = [(positions[r], replace(notes[r], bar_start=() if r == first else notes[r].bar_start,
                                   bar_end=() if r == end - 1 else notes[r].bar_end), ("o", r))
            for r in range(first, end) if r not in gone]
    bar = sorted(kept + added, key=lambda item: item[0])
    position = positions[first]
    for tick, note, _ in bar:
        if tick != position:
            raise AssertionError("local rhythm edit lost its place")
        position += note.ticks
    out = [(note, origin) for _, note, origin in bar]
    if starts:
        note, origin = out[0]
        out[0] = (replace(note, bar_start=symbols.sort_ids([*note.bar_start, *starts])), origin)
    if ends:
        note, origin = out[-1]
        out[-1] = (replace(note, bar_end=symbols.sort_ids([*note.bar_end, *ends])), origin)
    return out


def _change_refusal(problems, label, part, choice) -> RhythmRefused:
    hopeless = [p for p in problems if p.bar < 0]
    if hopeless:
        return RhythmRefused(hopeless[0].reason, part, hopeless[0].row)
    first = problems[0]
    if len(problems) == 1:
        reason = first.reason
    else:
        reason = tr("有 {count} 個地方放不下改長的音：").format(count=len(problems)) + "".join(p.reason for p in problems)
    choices = []
    fit = first.fit if len(problems) == 1 else None
    if fit is not None:
        choices.append(Choice("fit", tr("改為{name}（放得下）").format(name=_duration_name(fit))))
    if not has_choice(choice, "overwrite") and all(p.overwrite for p in problems):
        count = len({p.bar for p in problems})
        choices.append(Choice("overwrite", tr("覆蓋後面的音（只在這一小節）") if count == 1 else
                              tr("覆蓋後面的音（在這 {count} 個小節）").format(count=count)))
    return RhythmRefused(label + reason + tr("後面的小節線不會移動。"), part, first.row, tuple(choices), fit)


# ---------------------------------------------------------------- delete
def _may_trim(score, part: int, notes, cut: int) -> bool:
    """Notes from ``cut`` to the end may really go away (not become rests)."""
    if any(n.bar_start or n.bar_end for n in notes[cut:]):
        return False                       # bar lines, repeats and jumps stay with their bar
    others = [sum(n.ticks for n in p.notes) for i, p in enumerate(score.parts) if i != part]
    return not others or sum(n.ticks for n in notes[:cut]) >= max(others)


def delete_notes(score, part: int, rows) -> RhythmEdit:
    """Delete the selected notes: at the end of the part they go away (when nothing else must
    stay); elsewhere they become rests of the same length (the bar keeps its beats)."""
    notes = list(score.parts[part].notes)
    rows = sorted({r for r in rows if 0 <= r < len(notes)})
    if not rows:
        return RhythmEdit(notes, (), -1)
    chosen = set(rows)

    def explicit(item):
        return item.part == part and item.row in chosen and item.kind not in ("bar_start", "bar_end")
    pairs = _pairs(notes, "o")
    trailing = rows == list(range(rows[0], len(notes)))
    if trailing and _may_trim(score, part, notes, rows[0]):
        kept, removed = settle([(n, ("o", i)) for i, n in enumerate(notes[:rows[0]])], pairs)
        row = len(kept) - 1
        return RhythmEdit(kept, (row,) if row >= 0 else (), row, _notice(removed), explicit=explicit)
    if all(notes[r].degree == 0 for r in rows):
        raise RhythmRefused(tr("休止符會保留拍數，不能單獨刪除；要移除整個小節，請點小節號後按 Ctrl+Delete。"), part, rows[0])
    positions = _positions(notes)
    runs, run = [], []
    for r in rows:
        same_bar = run and score.bar_index(positions[r]) == score.bar_index(positions[run[-1]])
        if run and (r != run[-1] + 1 or not same_bar):
            runs.append(run)
            run = []
        run.append(r)
    runs.append(run)
    entries = [(note, ("o", i)) for i, note in enumerate(notes)]
    for run in reversed(runs):
        a, b = run[0], run[-1] + 1
        first, end, _, _ = _bar_span(score, positions, notes, a)
        starts = notes[a].bar_start if a == first else ()
        ends = notes[b - 1].bar_end if b == end else ()
        ticks = sum(n.ticks for n in notes[a:b])
        if all(n.tuplet for n in notes[a:b]) and len(notes[a:b]) % 3 == 0:
            rests = [replace(Note(0, duration=n.duration), tuplet=n.tuplet, tuplet_start=n.tuplet_start,
                             tuplet_end=n.tuplet_end) for n in notes[a:b]]
        else:
            try:
                rests = beat_rests(score, positions[a], ticks)
            except ValueError as exc:
                raise RhythmRefused(tr("{where}的時值無法用休止符表示；請改選完整的三連音或整個小節。").format(
                    where=note_place(score, part, a)), part, a) from exc
        if starts:
            rests[0] = replace(rests[0], bar_start=starts)
        if ends:
            rests[-1] = replace(rests[-1], bar_end=symbols.sort_ids([*rests[-1].bar_end, *ends]))
        entries[a:b] = [(rest, None) for rest in rests]
    out, removed = settle(entries, pairs)
    first_new = next(i for i, (_, origin) in enumerate(entries) if origin is None)
    return RhythmEdit(out, (first_new,), first_new, _notice(removed), explicit=explicit)


# ---------------------------------------------------------------- insert
def _whole_bars(score, notes_in) -> bool:
    ticks = sum(n.ticks for n in notes_in)
    return ticks > 0 and ticks % score.bar_ticks == 0


def _new_entries(new_notes):
    return [(note, ("n", k)) for k, note in enumerate(new_notes)]


def insert_notes(score, part: int, index: int, new_notes: list[Note], *, choice: str = "") -> RhythmEdit:
    """Insert notes before row ``index`` without moving later bar lines (see the module doc)."""
    notes = list(score.parts[part].notes)
    new_notes = list(new_notes)
    index = max(0, min(index, len(notes)))
    selection = tuple(range(index, index + len(new_notes)))
    label = _part_label(score, part)
    if sum(len(p.notes) for p in score.parts) + len(new_notes) > MAX_NOTES:
        raise RhythmRefused(tr("每份樂譜最多支援 10,000 個音符。"), part)
    pairs = (_pairs(notes, "o") or []) + (_pairs(new_notes, "n") or [])
    if index == len(notes):
        return _append(score, part, notes, new_notes, selection, pairs)
    positions = _positions(notes)
    at_bar_line = score.is_boundary(positions[index])
    # Whole bars on a bar line: the later bars move by whole bars (every part), bars stay intact.
    if at_bar_line and _whole_bars(score, new_notes) and not (score.pickup and positions[index] == 0):
        trial = list(notes)
        trial[index:index] = new_notes
        if _well_formed(score, part, trial):
            others = {}
            count = sum(n.ticks for n in new_notes) // score.bar_ticks
            for p, other in enumerate(score.parts):
                if p == part:
                    continue
                other_notes = list(other.notes)
                other_positions = _positions(other_notes)
                at = next((r for r, t in enumerate(other_positions) if t >= positions[index]), len(other_notes))
                if at < len(other_notes) and other_positions[at] != positions[index]:
                    break
                other_notes[at:at] = [rest for _ in range(count) for rest in beat_rests(score, positions[index],
                                                                                          score.bar_ticks)]
                others[p] = other_notes
            else:
                entries = [(n, ("o", i)) for i, n in enumerate(notes)]
                entries[index:index] = _new_entries(new_notes)
                out, removed = settle(entries, pairs)
                return RhythmEdit(out, selection, index + len(new_notes) - 1, _notice(removed), others or None)
    # Local: take rests after the insertion point in the same bar.
    first, end, start_tick, end_tick = _bar_span(score, positions, notes, index)
    need = sum(n.ticks for n in new_notes)
    total_end = positions[-1] + notes[-1].ticks
    unwritten = min(end_tick - total_end, _tail_room(score, part, total_end)) if end == len(notes) else 0
    rest_rows = [r for r in range(index, end) if notes[r].degree == 0 and not notes[r].tuplet]
    available = sum(notes[r].ticks for r in rest_rows) + unwritten
    if available >= need:
        return _insert_local(score, part, notes, new_notes, index, first, end, need, selection, pairs)
    where = note_place(score, part, index)
    reason = label + tr("{where}所在的小節後面只剩 {beats} 拍休止，放不下要插入的 {need} 拍；後面的小節線不會移動。").format(
        where=where, beats=_beats_text(available), need=_beats_text(need))
    ripple = list(notes)
    ripple[index:index] = new_notes
    ripple_ok = len(score.parts) == 1 and _well_formed(score, part, ripple)
    if has_choice(choice, "ripple") and ripple_ok:
        entries = [(n, ("o", i)) for i, n in enumerate(notes)]
        entries[index:index] = _new_entries(new_notes)
        out, removed = settle(entries, pairs)
        return RhythmEdit(out, selection, index + len(new_notes) - 1,
                          tr("已插入並把後面的音全部往後推移。"))
    if ripple_ok:
        raise RhythmRefused(reason + tr("可以改用「整首往後推移」（後面每個音都往後移，小節線位置隨之改變），或先「＋新增小節」。"),
                            part, index, (Choice("ripple", tr("整首往後推移")),))
    raise RhythmRefused(reason + tr("整首往後推移會讓後面的音跨過小節線或拆散記號，所以也不能這樣做；請先「＋新增小節」或改用「填寫」。"),
                        part, index)


def _append(score, part, notes, new_notes, selection, pairs) -> RhythmEdit:
    """Notes typed or pasted at the end of a part. With several parts, the other parts get
    rests to the same length (and this part catches up first if it was shorter)."""
    entries = [(n, ("o", i)) for i, n in enumerate(notes)] + _new_entries(new_notes)
    out, removed = settle(entries, pairs)
    others = {}
    total = sum(n.ticks for n in out)
    for p, other in enumerate(score.parts):
        if p == part:
            continue
        length = sum(n.ticks for n in other.notes)
        if length < total:
            try:
                others[p] = list(other.notes) + score.rests_between(length, total)
            except ValueError as exc:
                raise RhythmRefused(_part_label(score, part) + tr(
                    "曲尾加上這些音後，其他聲部無法用休止符補到一樣長；請改用其他時值。"), part, len(notes)) from exc
    return RhythmEdit(out, selection, len(notes) + len(new_notes) - 1, _notice(removed), others or None)


def _insert_local(score, part, notes, new_notes, index, first, end, need, selection, pairs) -> RhythmEdit:
    starts, ends = notes[first].bar_start, notes[end - 1].bar_end
    bar = [(n, ("o", r)) for r, n in zip(range(first, end), notes[first:end])]
    if starts:
        bar[0] = (replace(bar[0][0], bar_start=()), bar[0][1])
    if ends:
        bar[-1] = (replace(bar[-1][0], bar_end=()), bar[-1][1])
    local_index = index - first
    remaining = need
    out = bar[:local_index] + _new_entries(new_notes)
    pending = None              # (marks, text, spans, origin, ticks) of a partly used rest
    for note, origin in bar[local_index:]:
        if pending is not None:
            if _plain_rest(note):
                pending = (*pending[:4], pending[4] + note.ticks)
                continue
            out += _remainder(score, part, notes, pending)
            pending = None
        if remaining and note.degree == 0 and not note.tuplet:
            take = min(remaining, note.ticks)
            remaining -= take
            if take < note.ticks:
                pending = (note, origin, take, note.ticks - take, note.ticks - take)
            continue
        out.append((note, origin))
    if pending is not None:
        out += _remainder(score, part, notes, pending)
    # Anything not taken from rests comes from the unwritten end of the last bar.
    if starts:
        out[0] = (replace(out[0][0], bar_start=symbols.sort_ids([*out[0][0].bar_start, *starts])), out[0][1])
    if ends:
        out[-1] = (replace(out[-1][0], bar_end=symbols.sort_ids([*out[-1][0].bar_end, *ends])), out[-1][1])
    entries = [(n, ("o", i)) for i, n in enumerate(notes)]
    entries[first:end] = out
    result, removed = settle(entries, pairs)
    return RhythmEdit(result, selection, index + len(new_notes) - 1, _notice(removed))


def _remainder(score, part, notes, pending):
    """The rest of a partly used rest (plus plain rests merged after it when needed): it keeps
    the rest's marks, text and line ends."""
    rest, origin, _, _, ticks = pending
    try:
        rests = rest_fill(ticks)
    except ValueError as exc:
        raise RhythmRefused(_part_label(score, part) + tr(
            "插入後這裡會剩下 {beats} 拍，無法用休止符表示；請改用其他時值。").format(
            beats=_beats_text(ticks)), part) from exc
    keep = replace(rests[0], marks=rest.marks, text=rest.text, spans_start=rest.spans_start,
                   spans_end=rest.spans_end)
    return [(keep, origin)] + [(r, None) for r in rests[1:]]


def _well_formed(score, part, notes) -> bool:
    """Every note inside its bar and the score's relational rules still hold."""
    position = 0
    for note in notes:
        offset, length = score.bar_room(position)
        if offset + note.ticks > length:
            return False
        position += note.ticks
    parts = list(score.parts)
    parts[part] = replace(parts[part], notes=notes)
    trial = replace(score, parts=parts)
    try:
        trial.validate()
    except ValueError:
        return False
    return trial.edit_error() is None or score.edit_error() is not None
