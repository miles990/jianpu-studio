"""Preview MIDI straight from the model, without LilyPond (so playback starts at once).

The playing order is ``playing_order`` (``playmap.unfold_bars``: the same rules as
LilyPond's ``\\unfoldRepeats``), and every timing and velocity rule below copies what the
export (jianpu-ly's MIDI staff, then LilyPond 2.26) writes for the same score. Measured on
tests/fixtures/notation, the examples and randomized scores; tests/test_preview_midi.py and
tests/test_preview_equivalence.py compare both note by note.

* pitch: degree 1 of key X is the X at or above middle C (G, A, B: below it); 8va/8vb ±12;
* tempo: the score tempo (quarter notes), 384 ticks per quarter;
* which notes sound as one: jianpu-ly writes a long note as tied quarters and then joins
  tied quarters (or dotted quarters) of one bar back into one longer note when nothing but
  the tie stands between them (``_join``); the joined note carries the marks of every piece.
  Other tied notes stay separate pieces that LilyPond plays as one note: velocity of the
  first piece, end at the end of the last piece (after that piece's own shortening);
* velocity: LilyPond's Dynamic_performer (lily/dynamic-performer.cc, ported in
  ``_dynamics``): dynamics ppp…ffff are absolute volumes (no dynamic: 90/127); a hairpin
  changes the volume linearly in time up to the step that ends it; consecutive hairpins
  share the change toward the next dynamic by their durations, and a hairpin without a
  dynamic in its direction departs by 25 % of the 0.1–1 range (7 % beyond the nearer end
  when it turns back); velocity = volume × 127 plus accent and sf-type +20, sfzz and
  marcato +40, staccato +4, staccatissimo +6 (fp/sfp/sfpp also set a level); grace notes
  take the volume at their own moment;
* length: staccato = min(half, 0.5 s), staccatissimo = 0.125 s, portato (▽) 3/4, all of
  the sounding note (two on a joined note apply one after the other); breath marks change
  nothing; ticks are rounded down like LilyPond's;
* grace notes: a sixteenth lasts 9/40 of its value (21.6 ticks; 2/3 of that in a
  triplet), taken from the end of the previous note (not after a rest; notation_rules
  refuses more than that note's length); after-grace notes end at 3/4 of the last quarter
  (or of the note when shorter), and the main note ends where they begin. A grace note
  before the very first note starts the file: everything else is later by that lead-in (as
  in LilyPond's file; the score's tempo starts with the first part's grace notes, anything
  before them plays at jianpu-ly's default 84 quarters per minute);
* LilyPond lets go of a tie whose first note was shortened when any part takes a step
  (a note, rest, grace group or measure start, counting measures as LilyPond does across
  repeats) before the tied note: then the tied note sounds again;
* a note still sounding when its pitch starts again ends there;
* sustain pedal: CC64 127/0 at the marked notes; each part is its own track and channel;
* the file lasts as long as the written music, trailing rests included.

Ornaments, tremolo, glissando, slides, fermatas and rit./accel. have no effect (as in the
exported MIDI; wow/tech.md §3.3).

Every loop below walks a finite list once (bars of the playing order, notes of a bar, a
fixed set of join patterns), so building the preview always ends; a score it cannot play
raises ``PreviewError`` with the place of the problem.
"""
from __future__ import annotations

import math
import re
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field

from . import smf, symbols
from .i18n import tr
from .model import TICKS_PER_QUARTER, Note
from .notation_rules import bar_items
from .playmap import unfold, unfold_bars

DIVISION = 384
SCALE = DIVISION // TICKS_PER_QUARTER
KEY_SEMITONES = {"C": 0, "G": 7, "D": 2, "A": 9, "E": 4, "B": 11, "F#": 6, "F": 5, "Bb": 10, "Eb": 3,
                 "Ab": 8, "Db": 1, "Gb": 6}
SCALE_STEPS = (0, 2, 4, 5, 7, 9, 11)
ACCIDENTAL = {"#": 1, "b": -1, "n": 0, "": 0}
# LilyPond's default absolute-volume-alist (plus the app's fp/sfp/sfpp levels)
VOLUME = {"dyn.pppp": 0.34, "dyn.ppp": 0.42, "dyn.pp": 0.49, "dyn.p": 0.55, "dyn.mp": 0.61, "dyn.mf": 0.68,
          "dyn.f": 0.75, "dyn.ff": 0.80, "dyn.fff": 0.85, "dyn.ffff": 0.92,
          "dyn.fp": 0.55, "dyn.sfp": 0.55, "dyn.sfpp": 0.49}
EXTRA = {"art.accent": 20, "art.marcato": 40, "art.staccato": 4, "art.staccatissimo": 6,
         "dyn.sf": 20, "dyn.fz": 20, "dyn.sfz": 20, "dyn.sfzz": 40, "dyn.rf": 20, "dyn.rfz": 20,
         "dyn.fp": 20, "dyn.sfp": 20, "dyn.sfpp": 20}
DEFAULT_VELOCITY = 90
# A grace note (a sixteenth) lasts 9/40 of its written length in LilyPond's MIDI time
# (moment_to_real): 21.6 ticks.
GRACE = 4 * DIVISION / 16 * 9 / 40
# Grace notes before the first beat come before the score's \tempo: LilyPond plays them at
# jianpu-ly's default tempoWholesPerMinute (21 whole notes = 84 quarters per minute).
LEAD_TEMPO_US = 60_000_000 // 84
# Marks whose jianpu-ly word does not stand between a note and its tie (so the tied notes
# can still be joined): breath marks come after the tie, slides before the note.
TRANSPARENT_SLOTS = ("breath", "slide")
# (pieces, dotted) in the order jianpu-ly tries them (its tied-crotchet collapse).
JOINS = ((4, True), (4, False), (3, False), (2, True), (2, False))


class PreviewError(ValueError):
    """The preview cannot play this score; ``part``/``row`` locate the note."""

    def __init__(self, message, part=0, row=None):
        super().__init__(message)
        self.part, self.row = part, row


def pitch(score, note) -> int:
    # upstream: \transpose c X, with G/A/B (and flats of them) one octave down
    tonic = KEY_SEMITONES[score.key] - (12 if score.key[0] in "GAB" else 0)
    return (60 + tonic + SCALE_STEPS[note.degree - 1] + ACCIDENTAL.get(note.accidental, 0)
            + 12 * note.octave)


def _grace_pitches(score, text: str) -> list[int]:
    out = []
    for accidental, degree, octave in re.findall(r"([#b]?)([1-7])('{1,2}|,{1,2})?", text):
        shift = len(octave) if octave.startswith("'") else -len(octave)
        out.append(pitch(score, Note(int(degree), octave=shift, accidental=accidental)))
    return out


def _ottava(notes) -> list[int]:
    shift, out = 0, []
    for note in notes:
        for sid in note.spans_start:
            if sid == "line.8va":
                shift = 12
            elif sid == "line.8vb":
                shift = -12
        out.append(shift)
        if any(s in ("line.8va", "line.8vb") for s in note.spans_end):
            shift = 0
    return out


def _post_words(note) -> list[str]:
    """Words jianpu-ly writes right after the note head (marks, lines, text above the note)."""
    words = [symbols.SYMBOLS[m].words()[1] for m in note.marks if symbols.SYMBOLS[m].slot not in TRANSPARENT_SLOTS]
    words += [symbols.SYMBOLS[s].span_words(end)[1] for ids, end in ((note.spans_end, True), (note.spans_start, False))
              for s in ids if symbols.SYMBOLS[s].span_words(end)[0] == "post"]
    return words + (['^"' + note.text + '"'] if note.text else [])


def _movable(word: str) -> bool:
    """A word jianpu-ly's join carries along (a backslash command or a text script)."""
    return (word.startswith("\\") and " " not in word) or word.startswith('^"')


def _between(previous, note) -> tuple[bool, bool]:
    """(any words, only movable words) between the tie after ``previous`` and ``note``:
    the words after a tie (breath marks, an 8va end) and before a note (slides, an 8va start;
    a grace group, triplet bracket or glissando start cannot be carried along)."""
    if previous.tuplet_end or note.tuplet_start or note.grace_before:
        return True, False
    words = [symbols.SYMBOLS[m].words() for m in previous.marks if symbols.SYMBOLS[m].words()[0] == "after"]
    words += [symbols.SYMBOLS[s].span_words(True) for s in previous.spans_end if symbols.SYMBOLS[s].span_words(True)[0] == "after"]
    words += [symbols.SYMBOLS[m].words() for m in note.marks if symbols.SYMBOLS[m].words()[0] == "pre"]
    words += [symbols.SYMBOLS[s].span_words(False) for s in note.spans_start if symbols.SYMBOLS[s].span_words(False)[0] == "pre"]
    if any(symbols.SYMBOLS[s].family == "gliss" for s in note.spans_start):
        return True, False
    return bool(words), True


@dataclass
class _Piece:
    """One note as LilyPond plays it (after jianpu-ly's joins), or a rest."""
    onset: int                  # written tick (384 per quarter) in the part
    length: int
    rows: list[int]             # model rows whose marks and lines it carries
    part: int = 0
    row: int = 0                # model row it was written for (its first piece's)
    first: bool = True          # it starts its model note (grace notes before it)
    rest: bool = False
    key: int = 0                # written pitch (before 8va/8vb)
    token: tuple | None = None  # (quarter, dotted) when jianpu-ly writes it as a crotchet
    tie: bool = False           # tied to the next piece
    after_grace: bool = False   # the note jianpu-ly wraps in \afterGrace
    solid: bool = False         # something stands between it and its tie (no join)
    clean_after: bool = True    # nothing at all between its tie and the next piece
    open_after: bool = True     # only words that may move (marks, text) before the next piece
    marks: list = field(default_factory=list)     # in the order the words are written


def _pieces(score, part: int) -> list[_Piece]:
    """The notes of one written part as jianpu-ly's MIDI staff writes them."""
    notes = score.parts[part].notes
    ranges = score.part_bars(part)
    bar_start = {start for start, _ in ranges}
    out: list[_Piece] = []
    tick = 0
    for row, note in enumerate(notes):
        length = note.ticks * SCALE
        if out:
            previous = out[-1]
            if row in bar_start:
                previous.open_after = previous.clean_after = False
            else:
                words, movable = _between(notes[row - 1], note)
                previous.clean_after = not words
                previous.open_after = movable
        rest = not note.degree
        key = 0 if rest else pitch(score, note)
        crotchets = length // DIVISION if note.duration <= 2 else 0
        sizes = [DIVISION] * crotchets if crotchets else [length]
        token = (True, False) if crotchets else (note.duration == 4, note.dotted)
        position = tick
        post = _post_words(note)
        for k, size in enumerate(sizes):
            last = k == len(sizes) - 1
            # A long note is a crotchet and tied crotchets ("dashes"); its marks belong to
            # the first one. A long rest is crotchet rests (jianpu's "0 0").
            piece = _Piece(position, size, [row] if k == 0 else [], row=row, first=k == 0, rest=rest, key=key,
                           token=token if token[0] else None, tie=False if rest else (note.tie if last else True),
                           marks=list(note.marks) if k == 0 else [])
            if last and note.grace_after:
                piece.after_grace, piece.token = True, None
            if not last:
                # jianpu-ly puts a dash's tie right after the previous crotchet, so the
                # note's marks stand after that tie (where a join may carry them along).
                piece.clean_after = k > 0 or not post
                piece.open_after = k > 0 or all(_movable(w) for w in post)
            else:
                piece.solid = not crotchets and bool(post)
            out.append(piece)
            position += size
        tick += length
    return _join(out)


def _joins(group: list[_Piece], dotted: bool) -> bool:
    """jianpu-ly's patterns: ``X4 ~ <words> X4 ~ X4`` (notes) and ``r4 <words> r4 r4`` (rests),
    where only the first gap may hold words, and only words a join can carry along."""
    head = group[0]
    if not all(p.token == (True, dotted) and p.rest == head.rest and p.key == head.key for p in group):
        return False
    if not head.open_after or not all(p.clean_after for p in group[1:-1]):
        return False
    return head.rest or all(p.tie and not p.solid for p in group[:-1])


def _join(pieces: list[_Piece]) -> list[_Piece]:
    """jianpu-ly's collapse of tied crotchets: ``X4 ~ X4`` (2, 3 or 4 of them, or 2 or 4
    dotted ones) becomes one note when only marks or text stand after the first tie (and
    crotchet rests become one rest the same way)."""
    for count, dotted in JOINS:
        out: list[_Piece] = []
        index = 0
        while index < len(pieces):
            group = pieces[index:index + count]
            if len(group) == count and _joins(group, dotted):
                last = group[-1]
                joined = _Piece(group[0].onset, sum(p.length for p in group), [r for p in group for r in p.rows],
                                row=group[0].row, first=group[0].first, rest=group[0].rest, key=group[0].key, token=None, tie=last.tie, solid=last.solid,
                                clean_after=last.clean_after, open_after=last.open_after,
                                marks=[m for p in group for m in p.marks])
                out.append(joined)
                index += count
            else:
                out.append(pieces[index])
                index += 1
        pieces = out
    return pieces


def _seconds_ticks(score) -> float:
    return DIVISION * score.tempo / 60.0


def _shortened(length: int, marks, score) -> int:
    """LilyPond's midi-length of the articulations (ly/script-init.ly), applied one after the
    other in the written order (a joined note can carry two): staccato half the note but at
    most 0.5 s, staccatissimo 1/8 s (even past the note's end), portato 3/4. Breath marks do not shorten the note
    in the exported MIDI (measured), so they do not here either."""
    per_second = _seconds_ticks(score)
    for mark in marks:
        if mark == "art.staccato":
            length = min(length // 2, int(0.5 * per_second))
        elif mark == "art.staccatissimo":
            length = int(0.125 * per_second)        # even when longer than the note
        elif mark == "art.staccato_hollow":
            length = length * 3 // 4
    return length


class _Span:
    """LilyPond's Audio_span_dynamic: a volume that changes linearly over its duration."""
    __slots__ = ("start", "volume", "gain", "duration", "grow")

    def __init__(self, start: float, volume: float, grow: int):
        self.start, self.volume, self.gain, self.duration, self.grow = start, volume, 0.0, 0.0, grow

    def at(self, time: float) -> float:
        when = time - self.start
        if when <= 0:
            return self.volume
        if when >= self.duration:
            return self.volume + self.gain
        return self.volume + self.gain * when / self.duration


class _Queue:
    def __init__(self):
        self.spans: list[_Span] = []
        self.change = 0.0

    def push(self, span: _Span) -> None:
        if span.grow:
            self.change += span.duration
        self.spans.append(span)

    def set_volume(self, start: float, target: float) -> None:
        """Every (de)crescendo gets its share of the change by its share of the time."""
        gain, done, volume = target - start, 0.0, start
        for span in self.spans:
            previous = volume
            if span.grow:
                done += span.duration
                volume = start + gain * (done / self.change)
            span.volume, span.gain = previous, volume - previous
        self.spans, self.change = [], 0.0


MIN_TARGET, MAX_TARGET = 0.1, 1.0       # Dynamic_performer's target range (piano: no equalizer)


def _departure(direction: int, start: float, end: float) -> float:
    """``Dynamic_performer::calc_departure_volume``: 25 % of the range beyond the farther
    end, 7 % beyond the nearer one, whichever goes further, within the range."""
    if not direction:
        return start
    low, high = min(MIN_TARGET, start, end), max(MAX_TARGET, start, end)
    span = high - low
    if direction > 0:
        value = max(max(start, end) + 0.07 * span, min(start, end) + 0.25 * span)
    else:
        value = min(min(start, end) - 0.07 * span, max(start, end) - 0.25 * span)
    return max(min(value, high), low)


def _dynamics(notes, steps, end: float):
    """LilyPond 2.26's Dynamic_performer, step by step (lily/dynamic-performer.cc).

    ``steps``: (time, rows) in LilyPond's order, rows = model rows whose dynamics and
    hairpin starts/ends act at that time (none for a grace-note step); ``end``: the end of
    the music. Returns the span in effect at each step; its volume at a time is final once
    every step has been taken."""
    queues = {"depart": _Queue(), "return": _Queue()}
    state, depart_dir, open_span = "initial", 0, None
    linked: list[_Span] = []

    def finish(next_volume: float) -> float:
        depart, back = queues["depart"], queues["return"]
        if not depart.spans:
            return next_volume
        start = depart.spans[0].volume
        if not back.spans:
            target = next_volume
            if next_volume < 0 or depart_dir != (1 if next_volume > start else -1 if next_volume < start else 0):
                target = _departure(depart_dir, start, start)
            depart.set_volume(start, target)
            return next_volume if next_volume >= 0 else target
        returning = next_volume if next_volume >= 0 else start
        target = _departure(depart_dir, start, returning)
        depart.set_volume(start, target)
        back.set_volume(target, returning)
        return returning

    def close(time: float) -> None:
        nonlocal open_span
        open_span.duration = max(0.0, time - open_span.start)
        queues["return" if state == "return" else "depart"].push(open_span)
        open_span = None

    for time, rows in steps:
        script = next((VOLUME[m] for r in rows for m in notes[r].marks if m in VOLUME), None)
        grow = next((1 if "cresc" in sid else -1 for r in rows for sid in notes[r].spans_start
                     if sid.startswith("hairpin.")), 0)
        stop = any(sid.startswith("hairpin.") for r in rows for sid in notes[r].spans_end)
        volume = script if script is not None else (-1.0 if open_span else DEFAULT_VELOCITY / 127)
        if open_span and (grow or stop or script is not None):
            close(time)
            if script is not None:
                state = "initial"
                volume = finish(volume)
        if open_span is None:
            restart = False
            if state == "initial" and grow:
                state, depart_dir = "depart", grow
            elif state == "depart" and grow == -depart_dir:
                state = "return"
            elif state == "return" and grow == depart_dir:
                state, restart = "depart", True
            if restart:
                volume = finish(volume)
            if volume < 0:
                volume = DEFAULT_VELOCITY / 127
            open_span = _Span(time, volume, grow)
        linked.append(open_span)
    if open_span is not None:
        close(end)
    finish(-1.0)
    return linked


def _performed(score, part: int, order: list[int]) -> list[tuple[int, _Piece]]:
    """(performance tick, piece) of one part in playing order."""
    ranges = score.part_bars(part)
    lengths = [sum(n.ticks for n in score.parts[0].notes[a:b]) * SCALE for a, b in score.part_bars(0)]
    pieces = _pieces(score, part)
    for piece in pieces:
        piece.part = part
    by_bar: dict[int, list[_Piece]] = {}
    row_bar = {row: bar for bar, (a, b) in enumerate(ranges) for row in range(a, b)}
    bar_ticks, tick = [], 0
    for a, b in ranges:
        bar_ticks.append(tick)
        tick += sum(n.ticks for n in score.parts[part].notes[a:b]) * SCALE
    for piece in pieces:
        by_bar.setdefault(row_bar[piece.row], []).append(piece)
    out = []
    tick = 0
    for bar in order:
        for piece in by_bar.get(bar, ()):
            out.append((tick + piece.onset - bar_ticks[bar], piece))
        tick += lengths[bar] if bar < len(lengths) else 0
    return out


def _steps(score, performed_parts, total: int, back: int | None = None) -> tuple[list[float], list[float]]:
    """Every moment at which LilyPond's performers take a step (a note, rest or grace note
    starts in any part, or a measure starts), in performance ticks, and the moments of grace
    groups before a note (LilyPond takes those steps just before the moment itself; an
    after-grace counts at 3/4 of its note). LilyPond counts measures on from the pickup and never
    re-aligns them, so after a repeat that replays the pickup its measures start mid-bar;
    ``back``: tick where a D.C. return starts."""
    bar = score.bar_ticks * SCALE
    first = score.pickup * SCALE % bar
    steps = set()
    if score.pickup and back is not None and "nav.dc" in {i for _, edge in bar_items(score) for i in edge}:
        # A D.C. return replays the start, \partial included, so measures count from the
        # return again. When the piece opens with a repeat, the measures of the old count go
        # on as well (measured: LilyPond keeps both); otherwise the new count replaces them.
        # Known gap: with a pickup, a repeat from the start, Fine and D.C., a few staccato
        # ties on the return still differ (2 of 7,140 randomized scores; equivalence test).
        from .export import effective_bar_items, implicit_repeat_start
        items = effective_bar_items(score)
        keep = bool(items) and ("bar.repeat_start" in items[0][0] or implicit_repeat_start(items))
        steps.update(float(t) for t in range(first, (total if keep else back - 1) + 1, bar))
        steps.update(float(t) for t in range(back + score.pickup * SCALE, total + 1, bar))
    else:
        steps.update(float(t) for t in range(first, total + 1, bar))
    graces = set()
    for performed in performed_parts:
        for start, piece in performed:
            steps.add(float(start))
            head = score.parts[piece.part].notes[piece.row]
            if piece.first and head.grace_before and not piece.rest:
                graces.add(float(start))
            if piece.after_grace:
                steps.add(start + piece.length * 3 / 4)
    return sorted(steps), sorted(graces)


def _events(score, part: int, performed, steps: tuple[list[float], list[float]], total: int):
    """([start, end, pitch, velocity] floats, controls) of one part, before the lead-in."""
    notes = score.parts[part].notes
    shifts = _ottava(notes)
    # LilyPond's time steps of this part: (main moment, grace order, time, rows, what)
    timeline = []
    for index, (start, piece) in enumerate(performed):
        head = notes[piece.row]
        grace = GRACE * (2 / 3 if head.tuplet else 1)
        if piece.first and head.grace_before and not piece.rest:
            count = len(_grace_pitches(score, head.grace_before))
            for k in range(count):
                timeline.append((start, k - count, start - (count - k) * grace, [], ("before", index, k)))
        timeline.append((start, 0, float(start), piece.rows, ("main", index, 0)))
        if piece.after_grace:
            count = len(_grace_pitches(score, head.grace_after))
            stop = start + piece.length * 3 / 4
            for k in range(count):
                timeline.append((stop, k - count, stop - (count - k) * grace, [], ("after", index, k)))
    timeline.sort(key=lambda step: (step[0], step[1]))
    spans = _dynamics(notes, [(time, rows) for _, _, time, rows, _ in timeline], total)
    volume = {what: span.at(time) for (_, _, time, _, what), span in zip(timeline, spans)}

    events: list[list] = []
    controls = []
    chain = None        # (event a tie continues, end of its last written piece, end as played)
    previous = None     # the last event when the previous piece was a note
    for index, (start, piece) in enumerate(performed):
        for row in piece.rows:
            for mark in notes[row].marks:
                if mark == "pedal.on":
                    controls.append((start, 64, 127))
                elif mark == "pedal.off":
                    controls.append((start, 64, 0))
        if piece.rest:
            chain = previous = None
            continue
        head = notes[piece.row]
        key = piece.key + shifts[piece.row]
        grace = GRACE * (2 / 3 if head.tuplet else 1)
        graces = _grace_pitches(score, head.grace_before) if head.grace_before and piece.first else []
        lead = len(graces) * grace
        if graces:
            first = start - lead
            for k, value in enumerate(graces):
                events.append([first + k * grace, first + (k + 1) * grace, value + shifts[piece.row],
                               int(volume["before", index, k] * 127)])
            if previous is not None and previous[1] > first:
                # the note just before gives up the time (after a rest nothing is cut)
                previous[1] = max(previous[0] + 1, first)
        end = start + _shortened(piece.length, piece.marks, score)
        # LilyPond drops a tie whose first note was shortened (staccato…) when any part
        # takes a step after that note stops sounding and before the tied note starts (a
        # grace group counts just before its moment, so also one at the tied note's moment).
        tied = chain is not None and chain[0][2] == key and chain[1] == start and not any(
            chain[2] <= step < start for step in steps[0][bisect_left(steps[0], chain[2]):bisect_left(steps[0], start)]) \
            and not any(chain[2] < step <= start for step in steps[1][bisect_left(steps[1], chain[2]):bisect_right(steps[1], start)])
        if tied:
            event = chain[0]
            event[1] = end - lead
        else:
            velocity = int(volume["main", index, 0] * 127 + sum(EXTRA.get(m, 0) for m in piece.marks))
            event = [start, end, key, velocity]
            events.append(event)
        if piece.after_grace:
            after = _grace_pitches(score, head.grace_after)
            stop = start + piece.length * 3 / 4
            first = stop - len(after) * grace
            event[1] = min(event[1], first)
            for k, value in enumerate(after):
                events.append([first + k * grace, first + (k + 1) * grace, value + shifts[piece.row],
                               int(volume["after", index, k] * 127)])
        chain = (event, start + piece.length, end) if piece.tie and not piece.after_grace else None
        # after its after-grace notes nothing is cut (measured)
        previous = None if piece.after_grace else event
    return events, controls


def _leads(score, order) -> list[float]:
    """Ticks of grace notes before the first beat in each part (0 without)."""
    out = []
    for part in score.parts:
        notes = part.notes
        first = notes[0] if notes and order and order[0] == 0 else None
        if first is not None and first.degree and first.grace_before:
            out.append(len(_grace_pitches(score, first.grace_before)) * GRACE * (2 / 3 if first.tuplet else 1))
        else:
            out.append(0.0)
    return out


def lead_in(score, order: list[int] | None = None) -> tuple[float, float]:
    """(ticks, seconds) of grace notes before the first beat: LilyPond's file starts with
    them and everything else is that much later. The score's tempo starts with the first
    part's own grace notes; anything earlier plays at jianpu-ly's default 84 per minute."""
    order = playing_order(score)[0] if order is None else order
    leads = _leads(score, order) or [0.0]
    shift = max(leads)
    early = math.floor(shift - leads[0] + 1e-9)
    seconds = early * LEAD_TEMPO_US / DIVISION / 1e6 + (math.floor(shift + 1e-9) - early) * 60 / score.tempo / DIVISION
    return shift, seconds


def part_track(score, part: int, performed, steps, channel: int, shift: float = 0.0, total: int = 0) -> smf.Track:
    events, controls = _events(score, part, performed, steps, total)
    # A note still sounding when the same pitch starts again ends there (as in LilyPond's file).
    following: dict[int, float] = {}
    for event in sorted(events, key=lambda ev: ev[0], reverse=True):
        nxt = following.get(event[2])
        if nxt is not None and event[1] > nxt > event[0]:
            event[1] = nxt
        following[event[2]] = event[0]
    notes_out = []
    for s, e, p, v in events:
        # LilyPond's ticks: the start and the length, each rounded down (moment_to_ticks)
        begin = math.floor(s + shift + 1e-9)
        finish = begin + math.floor(e - s + 1e-9)
        if begin < 0:
            raise PreviewError(tr("無法產生試聽：{part}的倚音早於樂曲開頭。").format(
                part=score.parts[part].name or str(part + 1)), part)
        notes_out.append(smf.NoteEvent(begin, max(begin + 1, finish), p, max(1, min(127, v))))
    controls_out = [(math.floor(t + shift + 1e-9), c, v) for t, c, v in controls]
    return smf.Track(notes_out, channel=channel, controls=controls_out, name=score.parts[part].name)


def playing_order(score) -> tuple[list[int], bool]:
    """(bar order, structure followed). An unfinished repeat/jump plays the bars as written."""
    from .notation_rules import structure_problems
    if structure_problems(score):
        return list(range(len(score.part_bars(0)))), False
    return unfold_bars(score), True


def total_ticks(score, order: list[int]) -> int:
    """Length of the music in ``order`` (384 per quarter), trailing rests included."""
    lengths = [sum(n.ticks for n in score.parts[0].notes[a:b]) * SCALE for a, b in score.part_bars(0)]
    return sum(lengths[bar] for bar in order if bar < len(lengths))


def preview_midi(score, order: list[int] | None = None) -> bytes:
    """The preview MIDI of ``score`` in ``order`` (default: ``playing_order``)."""
    order = playing_order(score)[0] if order is None else order
    shift, _ = lead_in(score, order)
    early = math.floor(shift - _leads(score, order)[0] + 1e-9)
    performed = [_performed(score, index, order) for index in range(len(score.parts))]
    unfolded, back = unfold(score)
    back = total_ticks(score, order[:back]) if back is not None and order == unfolded else None
    steps = _steps(score, performed, total_ticks(score, order), back)
    total = total_ticks(score, order)
    tracks = [part_track(score, index, performed[index], steps, index, shift, total) for index in range(len(score.parts))]
    numerator, denominator = (int(v) for v in score.meter.split("/"))
    return smf.write(tracks, division=DIVISION, tempo_us=round(60_000_000 / score.tempo),
                     numerator=numerator, denominator=denominator,
                     length=math.floor(total_ticks(score, order) + shift + 1e-9), lead=(early, LEAD_TEMPO_US))
