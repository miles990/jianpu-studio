"""Engraving decisions shared by the canvas and the print path, so screen = print.

* ``shown_accidentals`` / ``shown_grace_accidentals``: which accidental each note and grace
  note shows. LilyPond (the print) shows an accidental when the note's alteration differs
  from the one in force for the same degree and octave earlier in the bar (grace notes
  included); every bar starts natural; ties follow LilyPond's tie rules (``_accidental_walk``).
  A stored ♮ ("n") prints only when needed (upstream has no cautionary natural).
* ``beam_runs``: underline (減時線) groups — notes shorter than a quarter joined within one
  counted beat, level by level, like jianpu-ly.
* ``rest_beats``: a rest longer than one beat is written as one 0 per beat (jianpu never
  extends a rest with dashes); the export writes the same zeros.
"""
from __future__ import annotations

import re

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


_GRACE = re.compile(r"([#b]?)([1-7])((?:'{1,2}|,{1,2})?)")
_ANY = object()       # "an alteration different from every real one": the next note always shows its sign


def _real_tie(previous, note) -> bool:
    """A tie LilyPond sees as a tie: same pitch, and the tied-from note is a single head.
    jianpu-ly draws the tie from a head with dashes (a half or longer) as a slur-like
    curve, so LilyPond treats that continuation as a fresh note (it reprints the sign)."""
    return (previous is not None and previous.tie and previous.degree == note.degree != 0
            and previous.octave == note.octave and _alteration(previous) == _alteration(note)
            and nominal_beats(previous) == 1)


def _accidental_walk(score, part: int) -> list[tuple[tuple[str, ...], str, tuple[str, ...]]]:
    """Per note: (signs of its before-graces, sign of the note, signs of its after-graces);
    a sign is "#", "b", "n" (a natural is printed) or "" (nothing printed).

    The rules LilyPond 2.26 applies to the jianpu staff (its default accidental style, checked
    against real renders in tests/test_print_fidelity.py):

    * state per degree and octave, starting natural in every bar; a note prints its sign when
      its alteration differs from the state, then sets the state;
    * grace notes take part in time order — before-graces ahead of their note, after-graces
      after it, all in their note's bar;
    * a really tied continuation (see ``_real_tie``) never prints a sign. When it would have
      needed one (a ♯ tied over the bar line), the rest of that bar prints the sign of every
      later note of that degree and octave, even ♮ or the same ♯ again;
    * rests change nothing.
    """
    notes = score.parts[part].notes
    out = []
    state: dict[tuple[int, int], object] = {}
    position, bar, previous = 0, None, None

    def sign(degree, octave, alteration, tied=False):
        key = (degree, octave)
        if alteration == state.get(key, ""):
            return ""
        state[key] = _ANY if tied else alteration
        return "" if tied else alteration or "n"

    for note in notes:
        index = score.bar_index(position)
        if index != bar:
            bar, state = index, {}
        position += note.ticks
        if note.degree == 0:
            out.append(((), "", ()))
        else:
            before = tuple(sign(int(d), len(o) * (-1 if "," in o else 1), a)
                           for a, d, o in _GRACE.findall(note.grace_before))
            main = sign(note.degree, note.octave, _alteration(note), _real_tie(previous, note))
            after = tuple(sign(int(d), len(o) * (-1 if "," in o else 1), a)
                          for a, d, o in _GRACE.findall(note.grace_after))
            out.append((before, main, after))
        previous = note
    return out


def shown_accidentals(score, part: int) -> list[str]:
    """Per note: "#", "b", "n" (a natural sign is printed) or "" (nothing printed)."""
    return [main for _, main, _ in _accidental_walk(score, part)]


def shown_grace_accidentals(score, part: int) -> list[tuple[tuple[str, ...], tuple[str, ...]]]:
    """Per note: the signs its (before, after) grace notes print, one per grace note, like
    ``shown_accidentals`` (a grace note's written ♯ may print nothing, a plain one a ♮)."""
    return [(before, after) for before, _, after in _accidental_walk(score, part)]


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


# A print-only sign that repeats what a working sign already prints at the same bar edge.
_SAME_PRINT = {"nav.segno_sign": "nav.segno", "nav.fine_text": "nav.fine", "nav.tocoda_text": "nav.tocoda",
               "nav.ds_text": "nav.ds", "nav.dscoda_text": "nav.ds", "nav.dc_text": "nav.dc",
               "nav.dcfine_text": "nav.dc"}
_VOLTA_STARTS = ("volta.1", "volta.2", "volta.3", "volta.4")


def redundant_signs(items) -> dict[tuple[int, str], str]:
    """(bar, print-only sign) -> what already prints the same thing there.

    The coda's first bar is labelled "⊕ Coda" automatically, so a Coda sign (僅符號) on that bar
    would be a second ⊕ on top of it; a text-only D.S./D.C./Fine/To Coda or a sign-only Segno
    beside the working sign of the same bar prints the same words twice. Neither the canvas
    nor the print draws these (the stored sign stays in the file and can be removed)."""
    out = {}
    coda = coda_start(items)
    for bar, (starts, ends) in enumerate(items):
        if bar == coda and "nav.coda_sign" in starts:
            out[bar, "nav.coda_sign"] = "coda"
        for edge in (starts, ends):
            for item in edge:
                if _SAME_PRINT.get(item) in edge:
                    out[bar, item] = _SAME_PRINT[item]
    return out


def without_redundant(items) -> list[tuple[tuple, tuple]]:
    """``items`` without the signs ``redundant_signs`` names: what is actually drawn."""
    hidden = redundant_signs(items)
    if not hidden:
        return list(items)
    return [(tuple(s for s in starts if (bar, s) not in hidden), tuple(e for e in ends if (bar, e) not in hidden))
            for bar, (starts, ends) in enumerate(items)]


def lone_first_endings(items) -> dict[int, int]:
    """start bar -> end bar of every first ending that is the only ending of its repeat (a
    「1.」 with its 「房子結束」 and no 「2.」 after it). LilyPond prints such an ending with
    the :‖ at its end, so the canvas draws it there too."""
    out = {}
    for bar, (starts, _) in enumerate(items):
        if "volta.1" not in starts or not bar or not {"bar.repeat_end", "bar.repeat_both"} & set(items[bar - 1][1]):
            continue
        for end in range(bar, len(items)):
            if end > bar and any(v in items[end][0] for v in _VOLTA_STARTS):
                break
            if "volta.end" in items[end][1]:
                out[bar] = end
                break
    return out


def repeat_end_bars(items) -> set[int]:
    """Bars whose end bar line is drawn as :‖ -- on the canvas, in the print and in a draft.

    A repeat end stored before a first ending marks where the repeated body ends; its :‖ is
    drawn at the end of every ending but the last (and at the end of a first ending that has
    no second one). Any other stored repeat end is drawn where it is."""
    out = set()
    for bar, (_, ends) in enumerate(items):
        following = items[bar + 1][0] if bar + 1 < len(items) else ()
        if {"bar.repeat_end", "bar.repeat_both"} & set(ends) and "volta.1" not in following:
            out.add(bar)
        if any(v in following for v in _VOLTA_STARTS[1:]):
            out.add(bar)
    out.update(lone_first_endings(items).values())
    return out


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
