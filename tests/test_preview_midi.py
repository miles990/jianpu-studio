"""The preview MIDI written from the model equals LilyPond's MIDI (round 6).

Unit tests hold values measured from LilyPond 2.26 (work/round6/probe_midi.py,
probe_hairpin.py); the integration test renders every fixture and example with the real
engine and compares note by note: start, end, pitch, velocity and track.
"""
import json
import threading
from pathlib import Path

import pytest

from jianpu_studio import smf
from jianpu_studio.engine import find_tool, render
from jianpu_studio.model import Note, Project, Score, load_project
from jianpu_studio.preview_midi import playing_order, preview_midi

ROOT = Path(__file__).resolve().parents[1]


def notes_of(score, order=None):
    return smf.read(preview_midi(score, order))[0]


def ticks(score, notes):
    q = 60 / score.tempo
    return [(round(n.start / q * 384), round((n.end - n.start) / q * 384), n.pitch, n.velocity) for n in notes]


def test_grace_notes_take_their_time_from_the_previous_note():
    score = Score(tempo=60, key="C", notes=[Note(1), Note(2, grace_before="3"), Note(3, grace_before="45"),
                                             Note(4, grace_before="567"), Note(5, grace_after="6"),
                                             Note(6, grace_after="71"), Note(1), Note(2)])
    got = ticks(score, notes_of(score))
    lily = [(0, 362, 60, 90), (362, 21, 64, 90), (384, 340, 62, 90), (724, 21, 65, 90), (746, 21, 67, 90),
            (768, 319, 64, 90), (1087, 21, 67, 90), (1108, 21, 69, 90), (1130, 21, 71, 90), (1152, 384, 65, 90),
            (1536, 266, 67, 90), (1802, 21, 69, 90), (1920, 244, 69, 90), (2164, 21, 71, 90), (2186, 21, 60, 90),
            (2304, 384, 60, 90), (2688, 384, 62, 90)]
    # Same notes and velocities; grace times within 2 ticks (LilyPond rounds its grace
    # moments, about 1/18 quarter each, slightly differently: under 6 ms at this tempo).
    assert [(p, v) for _, _, p, v in got] == [(p, v) for _, _, p, v in lily]
    assert all(abs(a[0] - b[0]) <= 2 and abs(a[1] - b[1]) <= 2 for a, b in zip(got, lily)), got


@pytest.mark.parametrize("tempo, lily", [
    (60, [(0, 192, 60, 94), (768, 48, 62, 96), (1536, 384, 64, 127), (1920, 384, 65, 90), (2304, 48, 67, 96), (2688, 192, 69, 94)]),
    (120, [(0, 384, 60, 94), (768, 96, 62, 96), (1536, 384, 64, 127), (1920, 384, 65, 90), (2304, 96, 67, 96), (2688, 192, 69, 94)]),
])
def test_articulations_shorten_and_accent_like_lilypond(tempo, lily):
    score = Score(tempo=tempo, key="C", notes=[
        Note(1, duration=2, marks=("art.staccato",)), Note(2, duration=2, marks=("art.staccatissimo",)),
        Note(3, marks=("art.marcato",)), Note(4, marks=("art.tenuto",)), Note(5, marks=("art.staccatissimo",)),
        Note(6, marks=("art.staccato",)), Note(0, duration=2)])
    assert ticks(score, notes_of(score)) == lily


@pytest.mark.parametrize("dyn, hairpin, lily", [
    ("dyn.p", "hairpin.dim", [69, 60, 50, 41, 41, 41, 41, 41]),
    ("dyn.p", "hairpin.cresc", [69, 79, 88, 98, 98, 98, 98, 98]),
    ("dyn.ff", "hairpin.cresc", [101, 110, 118, 127, 127, 127, 127, 127]),
    (None, "hairpin.dim", [90, 80, 70, 61, 61, 61, 61, 61]),
])
def test_hairpins_without_an_end_dynamic_move_by_lilypond_steps(dyn, hairpin, lily):
    notes = [Note(1, marks=(dyn,) if dyn else (), spans_start=(hairpin,)), Note(2), Note(3),
             Note(4, spans_end=(hairpin,)), Note(5), Note(6), Note(7), Note(1)]
    assert [n.velocity for n in notes_of(Score(tempo=120, key="C", notes=notes))] == lily


def test_keys_g_a_b_sound_below_middle_c_like_upstream():
    assert notes_of(Score(key="G", notes=[Note(1, duration=1)]))[0].pitch == 55
    assert notes_of(Score(key="F", notes=[Note(1, duration=1)]))[0].pitch == 65


def test_an_unfinished_structure_plays_as_written():
    notes = [Note(1, duration=1, bar_start=("nav.segno",)), Note(2, duration=1), Note(3, duration=1)]
    score = Score(notes=notes)
    order, structured = playing_order(score)
    assert order == [0, 1, 2] and not structured
    assert [n.pitch for n in notes_of(score, order)] == [60, 62, 64]


def cases():
    for path in sorted((ROOT / "tests/fixtures/notation").glob("*.json")):
        yield path.stem, Project.from_dict(json.loads(path.read_text(encoding="utf-8")))
    for path in sorted((ROOT / "examples").glob("*.jps")):
        yield path.stem, load_project(path)


@pytest.mark.integration
@pytest.mark.parametrize("name, project", list(cases()), ids=[name for name, _ in cases()])
def test_preview_equals_lilypond_midi(tmp_path, name, project):
    lilypond = find_tool("lilypond")
    if not lilypond:
        pytest.skip("JIANPU_LILYPOND not configured")
    result = render(project.notation(), tmp_path.resolve(), lilypond.resolve(), threading.Event(), create_pdf=False)
    key = lambda n: (round(n.start, 3), n.track, n.pitch)   # noqa: E731
    lily = sorted(smf.read(result.midi.read_bytes())[0], key=key)
    ours = sorted(notes_of(project.score), key=key)
    assert len(ours) == len(lily)
    for a, b in zip(lily, ours):
        assert (a.track, a.pitch, a.velocity) == (b.track, b.pitch, b.velocity), (name, a, b)
        assert abs(a.start - b.start) < 0.01 and abs(a.end - b.end) < 0.02, (name, a, b)
