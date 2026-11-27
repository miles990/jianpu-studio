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
    LilyPond starts its file with the grace note and plays everything that much later;
    the play map follows."""
    score = Score(tempo=60, notes=[Note(1, grace_before="2"), Note(3), Note(4), Note(5)])
    close(track(score), [(0, 21, 62, 90), (21, 384, 60, 90), (405, 384, 64, 90), (789, 384, 65, 90), (1173, 384, 67, 90)])
    play = PlayMap(score)
    assert play.lead_in == pytest.approx(64 / 3 / 384)
    assert play.at(0.01) is None and play.at(0.06).row == 0 and play.at(1.06).row == 1
    two = Score(tempo=60, parts=[Part("a", [Note(1, grace_before="23"), Note(2), Note(3, duration=2)]),
                                 Part("b", [Note(5), Note(6, grace_before="7"), Note(3, duration=2)])])
    close(sorted(track(two), key=lambda n: (n[0], n[2])), sorted([(0, 21, 62, 90), (21, 21, 64, 90), (43, 384, 60, 90), (427, 384, 62, 90), (811, 768, 64, 90),
                       (43, 362, 67, 90), (405, 21, 71, 90), (427, 384, 69, 90), (811, 768, 64, 90)], key=lambda n: (n[0], n[2])))


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
