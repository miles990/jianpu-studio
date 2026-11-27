"""Local rhythm edits: changing, deleting or inserting notes never moves a later bar line.

Every function works on one part and returns a ``RhythmEdit`` (the part's new notes, the
rows to select and a notice) or raises ``RhythmRefused`` with a message that names the place
(「第 3 小節第 2 拍」) and, when there is one, the choices the user can take instead.

* A shorter note leaves its freed time as rests right after it (split at beats).
* A longer note takes the rests that follow it in the same bar; with too little room the
  edit is refused and the choices are 「改為 X（放得下）」 or 「覆蓋後面的音」.
* Deleting notes in the middle leaves rests of the same length; deleting at the very end
  of the part removes them.
* Inserting in the middle takes rests that follow in the same bar (the notes between move
  right inside the bar). Whole bars inserted on a bar line add the same bars of rests to the
  other parts. Otherwise the only ripple is the explicit choice 「整首往後推移」, offered
  only when every later bar stays well formed.

Bar items (bar lines, repeats, endings, jumps) belong to the bar, not to a note: they are
taken off before a bar is edited and put back on its first / last note afterwards. Ties,
glissandi and line ends that lose their partner are removed and counted in the notice.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from . import symbols
from .i18n import tr
from .model import DURATIONS, MAX_NOTES, Note, rest_fill, tuplet_ranges
from .notation_rules import _part_label, bar_label, beat_ticks, note_place


@dataclass(frozen=True)
class Choice:
    key: str          # "fit" | "overwrite" | "ripple"
    label: str


class RhythmRefused(ValueError):
    def __init__(self, message: str, part: int = 0, row: int | None = None, choices: tuple[Choice, ...] = (),
                 fit: Note | None = None):
        super().__init__(message)
        self.part, self.row, self.choices, self.fit = part, row, choices, fit


@dataclass(frozen=True)
class RhythmEdit:
    notes: list[Note]
    selection: tuple[int, ...]
    current: int
    notice: str = ""
    others: dict | None = None        # other parts' new notes (whole-bar inserts only)


# ---------------------------------------------------------------- helpers
def _positions(notes) -> list[int]:
    out, position = [], 0
    for note in notes:
        out.append(position)
        position += note.ticks
    return out


def beat_rests(score, start: int, ticks: int) -> list[Note]:
    """Rests covering ``ticks`` from tick ``start``, one per beat as jianpu writes them (0 0 0),
    with the partial beats at either end decomposed exactly."""
    out, position, end = [], start, start + ticks
    beat = beat_ticks(score.meter)
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


def _take_bar_items(notes, first, end):
    """Remove the bar's items from its first/last note; return them to put back later."""
    if end <= first:
        return (), ()
    starts, ends = notes[first].bar_start, notes[end - 1].bar_end
    if starts:
        notes[first] = replace(notes[first], bar_start=())
    if ends:
        notes[end - 1] = replace(notes[end - 1], bar_end=())
    return starts, ends


def _put_bar_items(notes, first, end, starts, ends):
    if end <= first:
        return
    if starts:
        notes[first] = replace(notes[first], bar_start=symbols.sort_ids([*notes[first].bar_start, *starts]))
    if ends:
        notes[end - 1] = replace(notes[end - 1], bar_end=symbols.sort_ids([*notes[end - 1].bar_end, *ends]))


def _same_pitch(a: Note, b: Note) -> bool:
    return a.degree != 0 and (a.degree, a.octave, a.accidental.replace("n", "")) == (
        b.degree, b.octave, b.accidental.replace("n", ""))


def repair(notes: list[Note], pair_lines: bool = True) -> int:
    """Remove ties, glissandi and line ends that lost their partner; return how many were removed.

    Only applied after a local edit, to the part that was edited. ``pair_lines`` is False when
    the part's lines did not pair before the edit (an existing problem is not 'repaired' away)."""
    removed = 0
    for i, note in enumerate(notes):
        if note.tie and (i + 1 == len(notes) or not _same_pitch(note, notes[i + 1])):
            notes[i] = replace(note, tie=False)
            removed += 1
    if not pair_lines:
        return removed
    open_ = {}
    for i, note in enumerate(notes):
        for sid in note.spans_end:
            family = symbols.SYMBOLS[sid].family
            if family in open_:
                start, start_id = open_.pop(family)
                if family == "gliss" and i != start + 1:
                    notes[start] = replace(notes[start], spans_start=tuple(s for s in notes[start].spans_start
                                                                           if s != start_id))
                    notes[i] = replace(notes[i], spans_end=tuple(s for s in notes[i].spans_end if s != sid))
                    removed += 2
            else:
                notes[i] = replace(notes[i], spans_end=tuple(s for s in notes[i].spans_end if s != sid))
                removed += 1
        for sid in notes[i].spans_start:
            family = symbols.SYMBOLS[sid].family
            if family in open_:
                start, start_id = open_[family]
                notes[start] = replace(notes[start], spans_start=tuple(s for s in notes[start].spans_start
                                                                       if s != start_id))
                removed += 1
            open_[family] = (i, sid)
    for family, (start, start_id) in open_.items():
        notes[start] = replace(notes[start], spans_start=tuple(s for s in notes[start].spans_start if s != start_id))
        removed += 1
    return removed


def _lines_pair(notes) -> bool:
    from .notation_rules import _pair_spans
    try:
        list(_pair_spans(notes))
        return True
    except ValueError:
        return False


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


# ---------------------------------------------------------------- change duration
def change_notes(score, part: int, rows, change, *, choice: str = "") -> RhythmEdit:
    """Apply ``change(note) -> note`` to the selected rows, keeping every bar line in place.

    ``choice`` is "" (refuse when there is no room), "fit" (the longest value that fits) or
    "overwrite" (take the following notes of the same bar too)."""
    notes = list(score.parts[part].notes)
    rows = sorted({r for r in rows if 0 <= r < len(notes)})
    if not rows:
        return RhythmEdit(notes, (), -1)
    pair_lines = _lines_pair(notes)
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
    if all(changed[r].ticks == notes[r].ticks for r in rows):
        for r in rows:
            notes[r] = changed[r]
        removed = repair(notes, pair_lines)
        return RhythmEdit(notes, tuple(rows), rows[0], _notice(removed))
    positions = _positions(notes)
    # Group the rows by bar, then into runs of adjacent rows; edit bars right to left so
    # earlier rows keep their indices.
    bars: dict[int, list[int]] = {}
    spans = {}
    for r in rows:
        try:
            first, end, start_tick, end_tick = _bar_span(score, positions, notes, r)
        except RhythmRefused:
            # The bar is already broken (a note crosses its bar line): there is no bar to keep,
            # so change the values in place; shortening the crossing note is how it gets fixed.
            for x in rows:
                notes[x] = changed[x]
            removed = repair(notes, pair_lines)
            return RhythmEdit(notes, tuple(rows), rows[0], _notice(removed))
        bars.setdefault(first, []).append(r)
        spans[first] = (end, start_tick, end_tick)
    total_end = positions[-1] + notes[-1].ticks if notes else 0
    new_rows: list[int] = []
    for first in sorted(bars, reverse=True):
        end, start_tick, end_tick = spans[first]
        is_last_bar = end == len(notes)
        unwritten = end_tick - total_end if is_last_bar else 0
        bar = notes[first:end]
        starts, ends = _take_bar_items(bar, 0, len(bar))
        local = [r - first for r in bars[first]]
        runs, run = [], []
        for x in local:
            if run and x != run[-1] + 1:
                runs.append(run)
                run = []
            run.append(x)
        runs.append(run)
        selected_local = []
        for run in reversed(runs):
            a, b = run[0], run[-1] + 1
            old = bar[a:b]
            new = [replace(changed[first + x], bar_start=(), bar_end=()) for x in run]
            delta = sum(n.ticks for n in new) - sum(n.ticks for n in old)
            offset_after = sum(n.ticks for n in bar[:a]) + sum(n.ticks for n in new)
            if delta <= 0:
                fill = beat_rests(score, start_tick + offset_after, -delta) if delta else []
                bar[a:b] = new + fill
                selected_local = [a + i for i in range(len(new))] + [x + len(new) + len(fill) - len(old)
                                                                      for x in selected_local]
                continue
            # Longer: take following rests in this bar (or the unwritten end of the last bar).
            k, consumed, blocked = b, 0, None
            while consumed < delta and k < len(bar):
                follower = bar[k]
                if follower.degree != 0 and choice != "overwrite":
                    blocked = k
                    break
                consumed += follower.ticks
                k += 1
            room = consumed + (unwritten if k == len(bar) else 0)
            if room < delta:
                need_room = sum(n.ticks for n in old) + room
                where = note_place(score, part, first + a)
                if blocked is not None:
                    reason = tr("{where}後面到下一個音之前只剩 {beats} 拍休止，放不下改長的音（需要多 {need} 拍）。").format(
                        where=where, beats=_beats_text(room), need=_beats_text(delta))
                else:
                    reason = tr("{where}之後到小節線只剩 {beats} 拍，放不下改長的音（需要多 {need} 拍）。").format(
                        where=where, beats=_beats_text(room), need=_beats_text(delta))
                fit = _fit(old[0], new[0], need_room) if len(run) == 1 else None
                choices = []
                if fit is not None:
                    choices.append(Choice("fit", tr("改為{name}（放得下）").format(name=_duration_name(fit))))
                if blocked is not None and choice != "overwrite":
                    rest_of_bar = sum(n.ticks for n in bar[b:]) + unwritten
                    if rest_of_bar >= delta:
                        choices.append(Choice("overwrite", tr("覆蓋後面的音（只在這一小節）")))
                if choice == "fit" and fit is not None:
                    return change_notes(score, part, rows, lambda n, f=fit: replace(change(n), duration=f.duration,
                                                                                     dotted=f.dotted))
                raise RhythmRefused(label + reason + tr("後面的小節線不會移動。"), part, first + a, tuple(choices), fit)
            leftover = max(0, consumed - delta)
            fill = beat_rests(score, start_tick + offset_after, leftover) if leftover else []
            removed_count = k - b
            bar[a:k] = new + fill
            selected_local = [a + i for i in range(len(new))] + [x + len(new) + len(fill) - (len(old) + removed_count)
                                                                  for x in selected_local]
        _put_bar_items(bar, 0, len(bar), starts, ends)
        delta_rows = len(bar) - (end - first)
        notes[first:end] = bar
        new_rows = [first + x for x in selected_local] + [r + delta_rows for r in new_rows]
    if sum(len(p.notes) for p in score.parts) - len(score.parts[part].notes) + len(notes) > MAX_NOTES:
        raise RhythmRefused(tr("音符太多（上限 10,000）。"), part)
    removed = repair(notes, pair_lines)
    return RhythmEdit(notes, tuple(new_rows), new_rows[0] if new_rows else -1, _notice(removed))


# ---------------------------------------------------------------- delete
def delete_notes(score, part: int, rows) -> RhythmEdit:
    """Delete the selected notes: at the end of the part they go away; elsewhere they become
    rests of the same length (the bar keeps its beats)."""
    notes = list(score.parts[part].notes)
    rows = sorted({r for r in rows if 0 <= r < len(notes)})
    if not rows:
        return RhythmEdit(notes, (), -1)
    pair_lines = _lines_pair(notes)
    trailing = rows == list(range(rows[0], len(notes)))
    if trailing:
        kept = notes[:rows[0]]
        # bar items of a removed last note stay with the bar if the bar survives
        removed = repair(kept, pair_lines)
        row = len(kept) - 1
        return RhythmEdit(kept, (row,) if row >= 0 else (), row, _notice(removed))
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
    selection = []
    for run in reversed(runs):
        a, b = run[0], run[-1] + 1
        first, end, _, _ = _bar_span(score, positions, notes, a)
        bar_first_is_a, bar_last_is_b = a == first, b == end
        starts = notes[a].bar_start if bar_first_is_a else ()
        ends = notes[b - 1].bar_end if bar_last_is_b else ()
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
        _put_bar_items(rests, 0, len(rests), starts if bar_first_is_a else (), ends if bar_last_is_b else ())
        notes[a:b] = rests
        selection = [a + i for i in range(len(rests))] + [x + len(rests) - (b - a) for x in selection]
    removed = repair(notes, pair_lines)
    return RhythmEdit(notes, tuple(selection[:1]), selection[0] if selection else -1, _notice(removed))


# ---------------------------------------------------------------- insert
def _whole_bars(score, notes_in) -> bool:
    ticks = sum(n.ticks for n in notes_in)
    return ticks > 0 and ticks % score.bar_ticks == 0


def insert_notes(score, part: int, index: int, new_notes: list[Note], *, choice: str = "") -> RhythmEdit:
    """Insert notes before row ``index`` without moving later bar lines (see the module doc)."""
    notes = list(score.parts[part].notes)
    new_notes = list(new_notes)
    index = max(0, min(index, len(notes)))
    selection = tuple(range(index, index + len(new_notes)))
    label = _part_label(score, part)
    if sum(len(p.notes) for p in score.parts) + len(new_notes) > MAX_NOTES:
        raise RhythmRefused(tr("每份樂譜最多支援 10,000 個音符。"), part)
    if index == len(notes):
        notes[index:index] = new_notes
        return RhythmEdit(notes, selection, index + len(new_notes) - 1)
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
                return RhythmEdit(trial, selection, index + len(new_notes) - 1, "", others or None)
    # Local: take rests after the insertion point in the same bar.
    first, end, start_tick, end_tick = _bar_span(score, positions, notes, index)
    need = sum(n.ticks for n in new_notes)
    total_end = positions[-1] + notes[-1].ticks
    unwritten = end_tick - total_end if end == len(notes) else 0
    rest_rows = [r for r in range(index, end) if notes[r].degree == 0 and not notes[r].tuplet]
    available = sum(notes[r].ticks for r in rest_rows) + unwritten
    if available >= need:
        pair_lines = _lines_pair(notes)
        bar = notes[first:end]
        starts, ends = _take_bar_items(bar, 0, len(bar))
        local_index = index - first
        remaining = need
        out = bar[:local_index] + new_notes
        dropped_marks = 0
        for note in bar[local_index:]:
            if remaining and note.degree == 0 and not note.tuplet:
                take = min(remaining, note.ticks)
                remaining -= take
                dropped_marks += len(note.marks) if take == note.ticks else 0
                if take < note.ticks:
                    out += rest_fill(note.ticks - take)
                continue
            out.append(note)
        # Anything not taken from rests comes from the unwritten end of the last bar.
        _put_bar_items(out, 0, len(out), starts, ends)
        notes[first:end] = out
        removed = repair(notes, pair_lines) + dropped_marks
        return RhythmEdit(notes, selection, index + len(new_notes) - 1, _notice(removed))
    where = note_place(score, part, index)
    reason = label + tr("{where}所在的小節後面只剩 {beats} 拍休止，放不下要插入的 {need} 拍；後面的小節線不會移動。").format(
        where=where, beats=_beats_text(available), need=_beats_text(need))
    ripple = list(notes)
    ripple[index:index] = new_notes
    ripple_ok = len(score.parts) == 1 and _well_formed(score, part, ripple)
    if choice == "ripple" and ripple_ok:
        return RhythmEdit(ripple, selection, index + len(new_notes) - 1,
                          tr("已插入並把後面的音全部往後推移。"))
    if ripple_ok:
        raise RhythmRefused(reason + tr("可以改用「整首往後推移」（後面每個音都往後移，小節線位置隨之改變），或先「＋新增小節」。"),
                            part, index, (Choice("ripple", tr("整首往後推移")),))
    raise RhythmRefused(reason + tr("整首往後推移會讓後面的音跨過小節線或拆散記號，所以也不能這樣做；請先「＋新增小節」或改用「填寫」。"),
                        part, index)


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
    return trial.notation_error() is None or score.notation_error() is not None
