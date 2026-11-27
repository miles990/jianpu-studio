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
* velocity: dynamics ppp…ffff are LilyPond's absolute volumes × 127 (no dynamic: 90);
  a hairpin moves the volume in time from its start to the note that ends it, toward the
  next absolute dynamic when that one lies in the hairpin's direction (and no other hairpin
  comes first), else by ±0.225; accent and sf-type dynamics +20, sfzz and marcato +40,
  staccato +4, staccatissimo +6; fp/sfp/sfpp change the level, +20 on their own note;
* length: staccato = min(half, 0.5 s), staccatissimo = 0.125 s, portato (▽) 3/4, all of
  the sounding note; breath marks change nothing;
* grace notes: 1/18 quarter each (2/3 of that in a triplet), taken from the end of the
  previous note (not after a rest; notation_rules refuses more than that note's length); after-grace notes end at 3/4 of the last quarter (or of the note when
  shorter), and the main note ends where they begin. A grace note before the very first
  note starts the file: everything else is later by that lead-in (as in LilyPond's file);
* sustain pedal: CC64 127/0 at the marked notes; each part is its own track and channel;
* the file lasts as long as the written music, trailing rests included.

Ornaments, tremolo, glissando, slides, fermatas and rit./accel. have no effect (as in the
exported MIDI; wow/tech.md §3.3).

Every loop below walks a finite list once (bars of the playing order, notes of a bar, a
fixed set of join patterns), so building the preview always ends; a score it cannot play
raises ``PreviewError`` with the place of the problem.
"""
from __future__ import annotations

import re
from bisect import bisect_left
from dataclasses import dataclass, field

from . import smf, symbols
from .i18n import tr
from .model import TICKS_PER_QUARTER, Note
from .playmap import unfold_bars

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
HAIRPIN_STEP = 0.225
GRACE = DIVISION / 18
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
    marks: set = field(default_factory=set)


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
                           marks=set(note.marks) if k == 0 else set())
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
                                marks=set().union(*(p.marks for p in group)))
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
    """LilyPond's midi-length of the articulations (ly/script-init.ly): staccato half the
    note but at most 0.5 s, staccatissimo always 1/8 s, portato 3/4. Breath marks do not
    shorten the note in the exported MIDI (measured), so they do not here either."""
    per_second = _seconds_ticks(score)
    if "art.staccato" in marks:
        length = min(length // 2, int(0.5 * per_second))
    elif "art.staccatissimo" in marks:
        length = int(0.125 * per_second)
    elif "art.staccato_hollow" in marks:
        length = length * 3 // 4
    return length


def _levels(notes, performed) -> list[float | None]:
    """Volume (0..1) at each performed piece: the last dynamic; a hairpin moves in time
    from its first piece to the piece that ends it (see the module notes for the target)."""
    count = len(performed)
    explicit, starts, ends = [], [], []
    for _, piece in performed:
        rows = piece.rows
        explicit.append(next((VOLUME[m] for r in rows for m in notes[r].marks if m in VOLUME), None))
        starts.append(next((s for r in rows for s in notes[r].spans_start if s.startswith("hairpin.")), None))
        ends.append(any(s.startswith("hairpin.") for r in rows for s in notes[r].spans_end))
    out: list[float | None] = [None] * count
    level = None
    index = 0
    while index < count:
        if explicit[index] is not None:
            level = explicit[index]
        sid = starts[index]
        end = next((k for k in range(index + 1, count) if ends[k]), None) if sid else None
        if end is None:
            out[index] = level
            index += 1
            continue
        louder = "cresc" in sid
        begin = level if level is not None else DEFAULT_VELOCITY / 127
        target = None
        for k in range(end, count):
            if explicit[k] is not None:
                target = explicit[k]
                break
            if starts[k] is not None:
                break
        if target is None or (target <= begin if louder else target >= begin):
            target = min(1.0, max(0.0, begin + (HAIRPIN_STEP if louder else -HAIRPIN_STEP)))
        t0, t1 = performed[index][0], performed[end][0]
        for k in range(index, end + 1):
            fraction = (performed[k][0] - t0) / (t1 - t0) if t1 > t0 else 1.0
            out[k] = begin + (target - begin) * fraction
        if explicit[end] is not None:
            out[end] = explicit[end]
        level = out[end]
        index = end + 1
    return out


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


def _steps(score, performed_parts) -> list[float]:
    """Every moment at which LilyPond's performers take a step (a note, rest or grace note
    starts in any part), in performance ticks; a grace note's step comes just before its
    main note's moment."""
    steps = set()
    for performed in performed_parts:
        for start, piece in performed:
            steps.add(float(start))
            head = score.parts[piece.part].notes[piece.row]
            if piece.first and (head.grace_before or (piece.after_grace and head.grace_after)):
                steps.add(start - 1e-6)
            if piece.after_grace:
                steps.add(start + piece.length * 3 / 4 - 1e-6)
    return sorted(steps)


def _events(score, part: int, performed, steps: list[float]):
    """([start, end, pitch, velocity] floats, controls) of one part, before the lead-in."""
    notes = score.parts[part].notes
    shifts = _ottava(notes)
    levels = _levels(notes, performed)
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
                events.append([first + k * grace, first + (k + 1) * grace, value + shifts[piece.row], None])
            if previous is not None and previous[1] > first:
                # the note just before gives up the time (after a rest nothing is cut)
                previous[1] = max(previous[0] + 1, first)
        end = start + _shortened(piece.length, piece.marks, score)
        # LilyPond drops a tie whose first note was shortened (staccato…) when any part
        # takes a step after that note stops sounding and before the tied note starts.
        tied = chain is not None and chain[0][2] == key and chain[1] == start and not any(
            chain[2] <= step < start for step in steps[bisect_left(steps, chain[2]):bisect_left(steps, start)])
        if tied:
            event = chain[0]
            event[1] = end - lead
        else:
            level = levels[index]
            velocity = int(level * 127 + 1e-6) if level is not None else DEFAULT_VELOCITY
            velocity += sum(EXTRA.get(m, 0) for m in piece.marks)
            event = [start, end, key, velocity]
            events.append(event)
        for grace_event in events[len(events) - len(graces) - (0 if tied else 1):]:
            if grace_event[3] is None:
                grace_event[3] = event[3]
        if piece.after_grace:
            after = _grace_pitches(score, head.grace_after)
            stop = start + piece.length * 3 / 4
            first = stop - len(after) * grace
            event[1] = min(event[1], first)
            for k, value in enumerate(after):
                events.append([first + k * grace, first + (k + 1) * grace, value + shifts[piece.row], event[3]])
        chain = (event, start + piece.length, end) if piece.tie and not piece.after_grace else None
        previous = events[-1]
    return events, controls


def lead_in(score, order: list[int] | None = None) -> float:
    """Ticks (384 per quarter) of grace notes before the first beat: LilyPond's file starts
    with them and everything else is that much later."""
    order = playing_order(score)[0] if order is None else order
    if not order or order[0] != 0:
        return 0.0
    earliest = 0.0
    for part in score.parts:
        notes = part.notes
        if notes and notes[0].degree and notes[0].grace_before:
            count = len(_grace_pitches(score, notes[0].grace_before))
            earliest = min(earliest, -count * GRACE * (2 / 3 if notes[0].tuplet else 1))
    return -earliest


def part_track(score, part: int, performed, steps, channel: int, shift: float = 0.0) -> smf.Track:
    events, controls = _events(score, part, performed, steps)
    notes_out = []
    for s, e, p, v in events:
        begin, finish = round(s + shift), round(e + shift)
        if begin < 0:
            raise PreviewError(tr("無法產生試聽：{part}的倚音早於樂曲開頭。").format(
                part=score.parts[part].name or str(part + 1)), part)
        notes_out.append(smf.NoteEvent(begin, max(begin + 1, finish), p, max(1, min(127, v))))
    controls_out = [(round(t + shift), c, v) for t, c, v in controls]
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
    shift = lead_in(score, order)
    performed = [_performed(score, index, order) for index in range(len(score.parts))]
    steps = _steps(score, performed)
    tracks = [part_track(score, index, performed[index], steps, index, shift) for index in range(len(score.parts))]
    numerator, denominator = (int(v) for v in score.meter.split("/"))
    return smf.write(tracks, division=DIVISION, tempo_us=round(60_000_000 / score.tempo),
                     numerator=numerator, denominator=denominator,
                     length=round(total_ticks(score, order) + shift))
