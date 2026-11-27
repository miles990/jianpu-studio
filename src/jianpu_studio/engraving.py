"""Engraving decisions shared by the canvas and the print path, so screen = print.

* ``shown_accidentals``: which accidental each note shows. LilyPond (the print) shows an
  accidental when the note's alteration differs from the one in force for the same degree
  and octave earlier in the bar; a tied-over continuation shows none; every bar starts
  natural. A stored ♮ ("n") prints only when needed (upstream has no cautionary natural).
* ``beam_runs``: underline (減時線) groups — notes shorter than a quarter joined within one
  counted beat, level by level, like jianpu-ly.
* ``rest_beats``: a rest longer than one beat is written as one 0 per beat (jianpu never
  extends a rest with dashes); the export writes the same zeros.
"""
from __future__ import annotations

from .notation_rules import beat_ticks

BEAMS = {8: 1, 16: 2, 32: 3}


def positions_of(notes) -> list[int]:
    out, position = [], 0
    for note in notes:
        out.append(position)
        position += note.ticks
    return out


def _alteration(note) -> str:
    return "" if note.accidental in ("", "n") else note.accidental


def shown_accidentals(score, part: int) -> list[str]:
    """Per note: "#", "b", "n" (a natural sign is printed) or "" (nothing printed)."""
    notes = score.parts[part].notes
    out = [""] * len(notes)
    state: dict[tuple[int, int], str] = {}
    position, bar = 0, None
    previous = None
    for row, note in enumerate(notes):
        index = score.bar_index(position)
        if index != bar:
            bar, state = index, {}
        position += note.ticks
        if note.degree == 0:
            previous = note
            continue
        key = (note.degree, note.octave)
        alteration = _alteration(note)
        tied_over = (previous is not None and previous.tie and previous.degree == note.degree
                     and previous.octave == note.octave and _alteration(previous) == alteration)
        if not tied_over and alteration != state.get(key, ""):
            out[row] = alteration or "n"
        state[key] = alteration
        previous = note
    return out


def beam_runs(score, part: int, start: int, end: int) -> list[tuple[int, list[int]]]:
    """(level, rows) underline runs of rows [start, end) of one bar; level 0 is the first line."""
    notes = score.parts[part].notes
    if start >= end:
        return []
    positions = positions_of(notes[:end])
    beat = beat_ticks(score.meter)
    first = positions[start]
    offset0, _ = score.bar_room(first)
    if score.pickup and first < score.first_bar_ticks:
        offset0 += score.bar_ticks - score.first_bar_ticks
    bar_start = first - offset0

    def beat_of(row):
        return (positions[row] - bar_start) // beat

    runs = []
    for level in range(3):
        run = []
        for row in range(start, end + 1):
            ok = row < end and BEAMS.get(notes[row].duration, 0) > level
            if ok and run and beat_of(run[-1]) != beat_of(row):
                runs.append((level, run))
                run = []
            if ok:
                run.append(row)
            else:
                if run:
                    runs.append((level, run))
                run = []
    return runs


def nominal_beats(note) -> int:
    """Quarter beats a note of a half or longer covers (its dashes, or a rest's zeros)."""
    if note.duration > 2:
        return 1
    return (4 // note.duration) * (3 if note.dotted else 2) // 2


def rest_beats(note) -> int:
    """How many 0 a rest is written with (1 unless it is a half or longer)."""
    return nominal_beats(note) if note.degree == 0 else 1


def jump_text(items) -> str:
    """The printed text of the D.S./D.C. jump, from the bar items: the print's formatter
    (worker.ENGRAVING js-jump-text) applies the same rule — al Coda with a To Coda, al Fine
    with a Fine, otherwise the bare jump."""
    flat = {item for starts, ends in items for item in (*starts, *ends)}
    head = "D.S." if "nav.ds" in flat else "D.C."
    if "nav.tocoda" in flat:
        return head + " al Coda"
    if "nav.fine" in flat:
        return head + " al Fine"
    return head


def coda_start(items) -> int | None:
    """The bar where the coda starts (after the jump bar), when the piece has a To Coda."""
    flat = {item for starts, ends in items for item in (*starts, *ends)}
    if "nav.tocoda" not in flat:
        return None
    for bar, (_, ends) in enumerate(items):
        if "nav.ds" in ends or "nav.dc" in ends:
            return bar + 1 if bar + 1 < len(items) else None
    return None


def end_barline(items, bar: int, last: bool) -> str:
    """Bar line style implied by navigation, as the print draws it: Fine ends with a final
    bar line, and the jump bar before a coda with a double bar line ("" = no change)."""
    ends = items[bar][1]
    if "nav.fine" in ends and not last:
        return "final"
    if ("nav.ds" in ends or "nav.dc" in ends) and coda_start(items) == bar + 1:
        return "double"
    return ""


def lyric_map(notes, lyrics: str) -> dict[int, str]:
    """row -> syllable, as LilyPond's \\lyricsto places them for jianpu-ly (checked with a real
    render): one syllable per sung note; rests, tied continuations and the notes after a slur's
    first note (a melisma) take none."""
    if not lyrics.strip():
        return {}
    from .export import _syllables
    from .notation_rules import span_pairs
    syllables = [s.replace("_", "").rstrip("-") or s for s in _syllables(lyrics)]
    melisma = set()
    for family, start, end, _ in span_pairs(notes):
        if family == "slur":
            melisma.update(range(start + 1, end + 1))
    out, index = {}, 0
    for row, note in enumerate(notes):
        if index >= len(syllables):
            break
        if note.degree == 0 or row in melisma or (row and notes[row - 1].tie):
            continue
        out[row] = syllables[index]
        index += 1
    return out
