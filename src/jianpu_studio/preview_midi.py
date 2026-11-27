"""Preview MIDI straight from the model, without LilyPond (so playback starts at once).

The playing order is ``playmap.unfold_bars`` (the same rules as LilyPond's
``\\unfoldRepeats``), and every timing and velocity rule below copies what LilyPond 2.26
writes for the same score (measured on tests/fixtures/notation and the examples; see
tests/test_preview_midi.py, which compares both on every fixture):

* pitch: degree 1 of key X is the X at or above middle C (G, A, B: below it); 8va/8vb ±12;
* tempo: the score tempo (quarter notes), 384 ticks per quarter;
* velocity: dynamics ppp…ffff are LilyPond's absolute volumes × 127 (no dynamic: 90);
  hairpins interpolate between the dynamics at their ends; accent and sf-type dynamics +20,
  sfzz and marcato +40, staccato +4, staccatissimo +6; fp/sfp/sfpp change the level, +20 on
  their own note;
* length: staccato = min(half, 0.5 s), staccatissimo = min(quarter, 0.125 s), portato
  (▽) 3/4; tied notes are one note;
* grace notes: 1/18 quarter each, taken from the end of the previous note; after-grace
  notes end at 3/4 of their main note, which ends where they begin;
* sustain pedal: CC64 127/0 at the marked notes; each part is its own track and channel.

Ornaments, tremolo, glissando, slides, fermatas and rit./accel. have no effect (as in the
exported MIDI; wow/tech.md §3.3).
"""
from __future__ import annotations

import re

from . import smf
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
GRACE = DIVISION / 18


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


def _levels(notes, order_rows) -> list[float | None]:
    """Volume (0..1) of each performed note: the last dynamic, hairpins interpolated note by
    note toward the dynamic on (or right after) their last note, else ±0.225 (LilyPond)."""
    count = len(order_rows)
    explicit = [next((VOLUME[m] for m in notes[row].marks if m in VOLUME), None) for row in order_rows]
    ends = {}
    open_at = {}
    for index, row in enumerate(order_rows):
        for sid in notes[row].spans_end:
            if sid.startswith("hairpin.") and sid in open_at:
                ends[open_at.pop(sid)] = (index, "cresc" in sid)
        for sid in notes[row].spans_start:
            if sid.startswith("hairpin."):
                open_at[sid] = index
    out: list[float | None] = [None] * count
    level, index = None, 0
    while index < count:
        if explicit[index] is not None:
            level = explicit[index]
        if index in ends:
            end, louder = ends[index]
            begin = level if level is not None else DEFAULT_VELOCITY / 127
            target = explicit[end] if explicit[end] is not None else (
                explicit[end + 1] if end + 1 < count and explicit[end + 1] is not None else
                min(1.0, max(0.0, begin + (0.225 if louder else -0.225))))
            for k in range(index, end + 1):
                out[k] = begin + (target - begin) * (k - index) / (end - index)
            level, index = target, end + 1
            continue
        out[index] = level
        index += 1
    return out


def part_track(score, part: int, order: list[int], channel: int) -> smf.Track:
    notes = score.parts[part].notes
    ranges = score.part_bars(part)
    lengths = [sum(n.ticks for n in score.parts[0].notes[a:b]) for a, b in score.part_bars(0)]
    shifts = _ottava(notes)
    seconds_per_tick = 60.0 / score.tempo / DIVISION
    performed = []          # (start tick at 384, row)
    tick = 0
    for bar in order:
        if bar < len(ranges):
            position = tick
            for row in range(*ranges[bar]):
                performed.append((position, row))
                position += notes[row].ticks * SCALE
        tick += (lengths[bar] if bar < len(lengths) else 0) * SCALE
    levels = _levels(notes, [row for _, row in performed])
    events: list[list] = []     # [start, end, pitch, velocity]
    controls = []
    tied_from = None
    for index, (start, row) in enumerate(performed):
        note = notes[row]
        for mark in note.marks:
            if mark == "pedal.on":
                controls.append((start, 64, 127))
            elif mark == "pedal.off":
                controls.append((start, 64, 0))
        if not note.degree:
            tied_from = None
            continue
        key = pitch(score, note) + shifts[row]
        length = note.ticks * SCALE
        if tied_from is not None and events[tied_from][2] == key and events[tied_from][1] == start:
            events[tied_from][1] = start + length
            if not note.tie:
                tied_from = None
            continue
        level = levels[index]
        velocity = int(level * 127 + 1e-6) if level is not None else DEFAULT_VELOCITY
        velocity += sum(EXTRA.get(m, 0) for m in note.marks)
        end = start + length
        if "art.staccato" in note.marks:
            end = start + min(length // 2, int(0.5 / seconds_per_tick))
        elif "art.staccatissimo" in note.marks:
            end = start + min(length // 4, int(0.125 / seconds_per_tick))
        elif "art.staccato_hollow" in note.marks:
            end = start + length * 3 // 4
        if note.grace_before:
            graces = _grace_pitches(score, note.grace_before)
            first = start - len(graces) * GRACE
            for k, grace in enumerate(graces):
                g_start = round(first + k * GRACE)
                events.append([g_start, round(first + (k + 1) * GRACE) - 1 if k + 1 < len(graces) else start - 1,
                               grace + shifts[row], velocity])
            # the previous note gives up the time
            for event in events[:-len(graces)][::-1]:
                if event[1] > first:
                    event[1] = round(first)
                break
        main = [start, end, key, velocity]
        events.append(main)
        if note.grace_after:
            graces = _grace_pitches(score, note.grace_after)
            stop = start + length * 3 // 4
            first = stop - len(graces) * GRACE
            main[1] = min(main[1], round(first))
            for k, grace in enumerate(graces):
                events.append([round(first + k * GRACE), round(first + (k + 1) * GRACE) - 1, grace + shifts[row], velocity])
        tied_from = len(events) - 1 if note.tie and not note.grace_after else None
    notes_out = [smf.NoteEvent(s, max(s + 1, e), p, min(127, v)) for s, e, p, v in events]
    return smf.Track(notes_out, channel=channel, controls=controls, name=score.parts[part].name)


def playing_order(score) -> tuple[list[int], bool]:
    """(bar order, structure followed). An unfinished repeat/jump plays the bars as written."""
    from .notation_rules import structure_problems
    if structure_problems(score):
        return list(range(len(score.part_bars(0)))), False
    return unfold_bars(score), True


def preview_midi(score, order: list[int] | None = None) -> bytes:
    """The preview MIDI of ``score`` in ``order`` (default: the unfolded repeats and jumps)."""
    order = unfold_bars(score) if order is None else order
    tracks = [part_track(score, index, order, index) for index in range(len(score.parts))]
    numerator, denominator = (int(v) for v in score.meter.split("/"))
    return smf.write(tracks, division=DIVISION, tempo_us=round(60_000_000 / score.tempo),
                     numerator=numerator, denominator=denominator)
