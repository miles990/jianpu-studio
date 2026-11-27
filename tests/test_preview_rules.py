"""Preview MIDI rules found in the third review (playback freeze, ties, after-graces,
hairpins, trailing rests, the check reader's tempo).

Expected values were measured from the exported MIDI (jianpu-ly + LilyPond 2.26) of the
same scores; tests/test_preview_equivalence.py re-measures randomized scores with the real
engine on every run.
"""
import json
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from jianpu_studio import smf
from jianpu_studio.model import Note, Part, Project, Score, load_project, rest_fill
from jianpu_studio.playmap import PlayMap
from jianpu_studio.preview_midi import playing_order, preview_midi

ROOT = Path(__file__).resolve().parents[1]


def notes_of(score, order=None):
    return smf.read(preview_midi(score, order))[0]


def track(score, order=None):
    """(start, length, pitch, velocity) in ticks at 384 per quarter."""
    q = 60 / score.tempo
    return [(round(n.start / q * 384), round((n.end - n.start) / q * 384), n.pitch, n.velocity)
            for n in notes_of(score, order)]


def close(got, lily, tolerance=2):
    assert [(p, v) for *_, p, v in got] == [(p, v) for *_, p, v in lily], got
    assert all(abs(a[0] - b[0]) <= tolerance and abs(a[1] - b[1]) <= tolerance for a, b in zip(got, lily)), got


# ---------------------------------------------------------------- P0: never hang
def test_vlq_refuses_a_negative_or_huge_delta_instead_of_looping():
    from jianpu_studio.smf import _vlq
    assert _vlq(0) == b"\x00" and _vlq(0x0FFFFFFF) == b"\xff\xff\xff\x7f"
    for bad in (-1, -43, 0x10000000):
        with pytest.raises(ValueError):
            _vlq(bad)


def test_a_grace_note_before_the_first_note_starts_the_file():
    """Review 3 P0: a negative tick froze Play (endless VLQ loop on the GUI thread).
    LilyPond starts its file with the grace note and plays everything that much later (when
    a later part's grace notes come first, those play at jianpu-ly's default 84 per minute,
    before the tempo of the first part starts); the play map follows."""
    lead = 21 / 384                    # measured: 21 ticks at the score tempo
    score = Score(tempo=60, notes=[Note(1, grace_before="2"), Note(3), Note(4), Note(5)])
    got = [(round(n.start, 3), round(n.end, 3), n.pitch) for n in notes_of(score)]
    assert got == [(0.0, round(lead, 3), 62)] + [(round(lead + k, 3), round(lead + k + 1, 3), p)
                                                 for k, p in enumerate((60, 64, 65, 67))]
    play = PlayMap(score)
    assert play.lead_in == pytest.approx(lead)
    assert play.at(0.01) is None and play.at(lead + 0.01).row == 0 and play.at(lead + 1.01).row == 1
    assert play.total_seconds == pytest.approx(lead + 4)
    two = Score(tempo=60, parts=[Part("a", [Note(1, grace_before="23"), Note(2), Note(3, duration=2)]),
                                 Part("b", [Note(5), Note(6, grace_before="7"), Note(3, duration=2)])])
    lead = 43 / 384
    starts = sorted((round(n.start, 3), n.pitch) for n in notes_of(two))
    assert starts == sorted([(0.0, 62), (round(21 / 384, 3), 64), (round(lead, 3), 60), (round(lead, 3), 67),
                             (round(lead + 1 - 22 / 384, 3), 71), (round(lead + 1, 3), 62), (round(lead + 1, 3), 69),
                             (round(lead + 2, 3), 64), (round(lead + 2, 3), 64)])
    # Only a later part has grace notes first: they play before the tempo starts (84 per minute).
    later = Score(tempo=60, parts=[Part("a", [Note(1), Note(2), Note(3, duration=2)]),
                                   Part("b", [Note(5, grace_before="7"), Note(6), Note(3, duration=2)])])
    early = 21 * (60 / 84) / 384
    assert [round(n.start, 4) for n in notes_of(later)][:3] == [0.0, round(early, 4), round(early, 4)]
    assert PlayMap(later).lead_in == pytest.approx(early)


def review_scores():
    for path in sorted((ROOT / "tests/fixtures/notation").glob("*.json")):
        yield path.stem, Project.from_dict(json.loads(path.read_text(encoding="utf-8"))).score
    for path in sorted((ROOT / "examples").glob("*.jps")):
        yield path.stem, load_project(path).score
    for path in sorted((ROOT / "tests/fixtures/review3").glob("*.jps")):
        yield path.stem, load_project(path).score
    yield "grace-first", Score(tempo=60, notes=[Note(1, grace_before="2"), Note(3), Note(4), Note(5)])
    yield "grace-short-previous", Score(tempo=60, notes=[Note(1, duration=32), Note(3, duration=32, grace_before="23456"),
                                                         Note(0, duration=16), Note(4, duration=2), Note(5, duration=4, dotted=True)])
    yield "grace-every-part", Score(tempo=300, parts=[Part(str(k), [Note(k + 1, duration=32, grace_before="12345671")] + rest_fill(186))
                                                      for k in range(4)])


@pytest.mark.parametrize("name, score", list(review_scores()), ids=[name for name, _ in review_scores()])
def test_preview_generation_always_ends_quickly(name, score):
    """Watchdog: building the preview of every fixture, example and review score ends within
    a small budget (or raises a located error) -- it runs on the GUI thread when Play is pressed."""
    outcome = []

    def build():
        try:
            outcome.append(preview_midi(score, playing_order(score)[0]))
        except ValueError as exc:       # PreviewError and model errors carry a message
            outcome.append(exc)
    worker = threading.Thread(target=build, daemon=True)
    worker.start()
    worker.join(2.0)
    assert not worker.is_alive(), f"{name}: preview generation did not finish within 2 s"
    assert outcome and (isinstance(outcome[0], bytes) or str(outcome[0])), name


# ---------------------------------------------------------------- grace notes
def test_a_grace_group_longer_than_the_previous_note_is_a_located_problem():
    """LilyPond pushes the rest of the music later (or drops the note: "Going back in MIDI
    time") when grace notes need more time than the note before them."""
    notes = [Note(1), Note(1, duration=32), Note(5, duration=32, grace_before="234"), Note(2, duration=16, dotted=True)]
    score = Score(tempo=60, notes=notes + rest_fill(192 - sum(n.ticks for n in notes)))
    problem = score.edit_error()
    assert problem is not None and "倚音" in problem and problem.row == 2
    fits = Score(tempo=60, notes=[replace(n, grace_before="23") if n.grace_before else n for n in score.notes])
    assert fits.edit_error() is None
    after_rest = Score(tempo=60, notes=[Note(0, duration=32) if i == 1 else n for i, n in enumerate(score.notes)])
    assert after_rest.edit_error() is None       # after a rest the grace notes have room
    # ... and the note before the rest keeps its full length (measured)
    close(track(after_rest)[:2], [(0, 384, 60, 90), (367, 21, 62, 90)])


@pytest.mark.parametrize("pickup", [0, 48])
def test_an_after_grace_on_a_long_note_comes_near_the_end_of_its_last_beat(pickup):
    """Review 3 #8 (and the re-barred case of #7): jianpu-ly attaches the after-grace to the
    last quarter of a long note; a tied piece with an after-grace lost its grace."""
    from jianpu_studio import rebar
    score = Score(tempo=60, notes=[Note(1, duration=2, grace_after="2"), Note(3), Note(4)])
    if pickup:
        rebar.rebar(score, pickup=pickup).apply(score, pickup=pickup)
        assert [n.tie for n in score.notes][:2] == [True, False] and score.notes[1].grace_after == "2"
    close(track(score), [(0, 650, 60, 90), (650, 21, 62, 90), (768, 384, 64, 90), (1152, 384, 65, 90)])


def test_grace_notes_in_a_triplet_are_two_thirds_as_long():
    score = Score(tempo=60, notes=[Note(1, duration=8, tuplet=3, tuplet_start=True), Note(2, duration=8, tuplet=3, grace_after="5"),
                                   Note(3, duration=8, tuplet=3, tuplet_end=True, grace_before="4"), Note(1, duration=4, dotted=True),
                                   Note(2, duration=8), Note(1)])
    close(track(score)[:5], [(0, 128, 60, 90), (128, 81, 62, 90), (209, 14, 67, 90), (241, 14, 65, 90), (256, 128, 64, 90)])


# ---------------------------------------------------------------- ties
def test_a_staccato_first_note_of_a_tie_still_sounds_as_one_note():
    """Review 3 #7: LilyPond plays the tied notes as one (the first note's staccato only adds
    velocity); the preview shortened it and played the second piece again."""
    score = Score(tempo=60, notes=[Note(1, tie=True, marks=("art.staccato",)), Note(1), Note(4), Note(2)])
    assert track(score) == [(0, 768, 60, 94), (768, 384, 65, 90), (1152, 384, 62, 90)]


def test_tied_crotchets_join_and_carry_the_marks_of_the_last_one():
    """jianpu-ly writes ``c'4 ~ c'4 \\staccato`` as ``c'2 \\staccato``: the whole note is shortened."""
    score = Score(tempo=60, notes=[Note(1, tie=True), Note(1, marks=("art.staccato",)), Note(4), Note(2)])
    assert track(score) == [(0, 192, 60, 94), (768, 384, 65, 90), (1152, 384, 62, 90)]
    eighths = Score(tempo=60, notes=[Note(1, duration=8, tie=True), Note(1, duration=8, marks=("art.accent",)),
                                     Note(2, duration=8, tie=True, marks=("art.accent",)), Note(2, duration=8, marks=("art.staccato",)),
                                     Note(3, duration=2)])
    assert track(eighths) == [(0, 384, 60, 90), (384, 288, 62, 110), (768, 768, 64, 90)]
    pedal = Score(tempo=60, notes=[Note(1, tie=True), Note(1, marks=("pedal.on",)), Note(2), Note(2, marks=("pedal.off",))])
    _, controls, _ = smf.read(preview_midi(pedal))
    assert [(round(t * 384), c, v) for t, _, c, v in controls] == [(0, 64, 127), (1152, 64, 0)]


def test_a_tie_is_dropped_when_another_part_moves_after_a_shortened_note():
    """LilyPond lets go of a pending tie when any part takes a step after the shortened first
    note stops and before the tied note starts (measured)."""
    dropped = Score(tempo=60, parts=[
        Part("a", [Note(1, tie=True, marks=("art.staccato",)), Note(1), Note(4), Note(2)]),
        Part("b", [Note(5, duration=8), Note(5, duration=8), Note(6, duration=4, dotted=True), Note(7, duration=8), Note(1)])])
    assert [n for n in track(dropped) if n[2] == 60][:2] == [(0, 192, 60, 94), (384, 384, 60, 90)]
    kept = Score(tempo=60, parts=[
        Part("a", [Note(1, tie=True, marks=("art.staccato",)), Note(1), Note(4), Note(2)]),
        Part("b", [Note(5), Note(6, duration=8), Note(7, duration=8), Note(1, duration=2)])])
    assert [n for n in track(kept) if n[2] == 60][0] == (0, 768, 60, 94)


# ---------------------------------------------------------------- dynamics and lengths
@pytest.mark.parametrize("notes, lily", [
    # Review 3 #9: a hairpin moves in time, not note by note
    ([Note(1, duration=2, marks=("dyn.p",), spans_start=("hairpin.cresc",)), Note(2), Note(3, spans_end=("hairpin.cresc",), marks=("dyn.f",))],
     [69, 86, 95]),
    # no end dynamic: toward the next dynamic in the hairpin's direction ...
    ([Note(1, duration=2, marks=("dyn.p",), spans_start=("hairpin.cresc",)), Note(2, duration=8), Note(3, duration=8),
      Note(4, spans_end=("hairpin.cresc",)), Note(5, duration=2), Note(5, duration=2, marks=("dyn.f",))],
     [69, 86, 91, 95, 95, 95]),
    # ... else by LilyPond's step, and a following hairpin starts from there
    ([Note(1, marks=("dyn.p",), spans_start=("hairpin.cresc",)), Note(2), Note(3, spans_end=("hairpin.cresc",)),
      Note(4, spans_start=("hairpin.dim",)), Note(5), Note(6, spans_end=("hairpin.dim",)), Note(7), Note(1, marks=("dyn.mf",))],
     [69, 84, 98, 98, 92, 86, 86, 86]),
    ([Note(1, marks=("dyn.p",), spans_start=("hairpin.cresc",)), Note(2), Note(3), Note(4, spans_end=("hairpin.cresc",)),
      Note(5), Note(6, marks=("dyn.pp",)), Note(7), Note(1)],
     [69, 79, 88, 98, 98, 62, 62, 62]),
])
def test_hairpins_follow_lilypond_in_time(notes, lily):
    score = Score(tempo=60, notes=notes)
    score.fill_last_bar()
    assert [n.velocity for n in notes_of(score)] == lily


def test_staccatissimo_lasts_an_eighth_of_a_second():
    """ly/script-init.ly: staccatissimo's midi-length is 1/8 s whatever the note."""
    score = Score(tempo=240, notes=[Note(1, marks=("art.staccatissimo",)), Note(2, duration=8, marks=("art.staccatissimo",)),
                                    Note(3, duration=8), Note(4, duration=2)])
    assert [n[1] for n in track(score)][:2] == [192, 192]


def _end_ticks(data: bytes) -> list[int]:
    """Absolute tick of each track's End-of-Track (an independent walk of the chunks)."""
    out, offset = [], 14
    while offset < len(data):
        size = int.from_bytes(data[offset + 4:offset + 8], "big")
        body, offset = data[offset + 8:offset + 8 + size], offset + 8 + size
        position, tick, running = 0, 0, 0
        while position < len(body):
            value = 0
            for _ in range(4):
                byte = body[position]
                position += 1
                value = (value << 7) | (byte & 0x7F)
                if byte < 0x80:
                    break
            tick += value
            if body[position] & 0x80:
                running = body[position]
                position += 1
            if running == 0xFF:
                kind, length = body[position], body[position + 1]
                position += 2 + length
                if kind == 0x2F:
                    out.append(tick)
            elif running >> 4 in (0xC, 0xD):
                position += 1
            else:
                position += 2
    return out


def test_the_file_lasts_as_long_as_the_music_with_trailing_rests():
    """Review 3 #10: End-of-Track came at the last note, so seeking into a resting bar failed."""
    score = Score(tempo=60, notes=[Note(1, duration=1), Note(0, duration=1), Note(0, duration=1)])
    assert _end_ticks(preview_midi(score)) == [4608, 4608]      # LilyPond: [4608, 4608]
    assert PlayMap(score).total_seconds == pytest.approx(12.0)


def test_the_check_reader_uses_the_written_tempo_above_120_bpm():
    """Review 3 #20: smf.read sorted its 500,000 µs default after the file's own tempo at
    tick 0, so a fast piece was read at 120 bpm (preview and export agreed at the wrong speed)."""
    score = Score(tempo=240, notes=[Note(1), Note(2), Note(3), Note(4)])
    assert [n.start for n in notes_of(score)] == [0.0, 0.25, 0.5, 0.75]


# ---------------------------------------------------------------- the join rule × the catalog
def _converted_midi_staff(score, tmp_path) -> str:
    """The MIDI staff jianpu-ly writes for ``score`` (the real converter, in this process)."""
    import re

    from jianpu_studio import worker
    source, target = tmp_path / "s.jly", tmp_path / "s.ly"
    source.write_text(Project(score).notation(), encoding="utf-8")
    assert worker.run(["convert", str(source), str(target), "26"]) == 0
    return re.findall(r"(?s)% === BEGIN MIDI STAFF ===(.*?)% === END MIDI STAFF ===", target.read_text(encoding="utf-8"))[0]


def _tie_cases():
    from jianpu_studio import symbols
    for sid, symbol in symbols.SYMBOLS.items():
        if symbol.storage == "marks" and symbol.kind in ("point", "pre"):
            for where in (0, 1):
                notes = [Note(1, tie=True), Note(1), Note(3), Note(4)]
                notes[where] = replace(notes[where], marks=(sid,))
                yield f"{sid}-{where}", notes
        elif symbol.storage == "spans" and symbol.family not in ("gliss",):
            notes = [Note(1, tie=True, spans_start=(sid,)), Note(1), Note(3, spans_end=(sid,)), Note(4)]
            yield f"{sid}-start", notes
            notes = [Note(2, spans_start=(sid,)), Note(1, tie=True, spans_end=(sid,)), Note(1), Note(4)]
            yield f"{sid}-end", notes
    yield "text-0", [Note(1, tie=True, text="甲"), Note(1), Note(3), Note(4)]
    yield "text-1", [Note(1, tie=True), Note(1, text="甲"), Note(3), Note(4)]
    yield "grace-1", [Note(2), Note(1, tie=True), Note(1, grace_before="5"), Note(4)]


@pytest.mark.parametrize("name, notes", list(_tie_cases()), ids=[name for name, _ in _tie_cases()])
def test_the_preview_joins_tied_crotchets_exactly_when_jianpu_ly_does(tmp_path, name, notes):
    """Every catalog mark and line, on either note of a tied pair of crotchets: the preview's
    join decision (which decides length, velocity and shortening) equals the converter's."""
    from jianpu_studio.preview_midi import _pieces
    score = Score(tempo=60, notes=notes)
    if score.edit_error():
        pytest.skip(f"not a valid combination: {score.edit_error()}")
    pieces = [p for p in _pieces(score, 0) if not p.rest]
    tied_row = next(i for i, n in enumerate(notes) if n.tie)
    joined = any(tied_row in p.rows and tied_row + 1 in p.rows for p in pieces)
    staff = _converted_midi_staff(score, tmp_path)
    assert joined == ("c'2" in staff), staff


@pytest.mark.parametrize("change", [dict(spans_start=("line.8vb",)), dict(marks=("slide.up",))])
def test_an_octave_line_or_slide_right_after_an_after_grace_is_a_located_problem(change):
    """jianpu-ly writes the next note's leading command inside the after-grace block; across
    an ending boundary the exported MIDI then played the repeat an octave low (found by the
    equivalence sweep, seed 115607). Refused with its place instead."""
    notes = [Note(1, duration=2, grace_after="5"), Note(2, **change), Note(3, spans_end=change.get("spans_start", ())), Note(4)]
    problem = Score(notes=notes).edit_error()
    assert problem is not None and "後倚音" in problem and problem.row == 1
    later = [notes[0], Note(2), Note(2, **change), Note(3, spans_end=change.get("spans_start", ()))]
    assert Score(notes=later).edit_error() is None


def _hairpins(start, chain, final, lengths):
    notes = []
    for k, (direction, length) in enumerate(zip(chain, lengths)):
        sid = "hairpin.cresc" if direction == "<" else "hairpin.dim"
        notes += [Note(1 + k % 7, marks=(start,) if k == 0 and start else (), spans_start=(sid,))]
        notes += [Note(2)] * (length - 2) + [Note(3, spans_end=(sid,))]
    score = Score(tempo=60, notes=notes + [Note(5, marks=(final,) if final else ())] + [Note(6)] * 3)
    score.fill_last_bar()
    return score


@pytest.mark.parametrize("start, chain, final, lengths, lily", [
    # measured from the exported MIDI; LilyPond 2.26's Dynamic_performer
    ("dyn.p", "<>", "dyn.pp", (3, 3), [69, 80, 90, 90, 76, 62, 62, 62, 62, 62]),
    ("dyn.p", "<<", "dyn.mf", (4, 2), [69, 73, 78, 82, 82, 86, 86, 86, 86, 86]),
    ("dyn.f", "><", "dyn.ff", (4, 2), [95, 87, 80, 73, 73, 101, 101, 101, 101, 101]),
    (None, "<><", None, (3, 3, 3), [90, 104, 118, 118, 104, 90, 90, 104, 118, 118, 118, 118, 118]),
])
def test_successive_hairpins_share_the_change_like_lilypond(start, chain, final, lengths, lily):
    """Review 3 #9 class: consecutive hairpins divide the change by duration, and one that
    turns back departs by LilyPond's padding (25 % / 7 % of the 0.1–1 range)."""
    assert [n.velocity for n in notes_of(_hairpins(start, chain, final, lengths))] == lily
