"""MIDI time -> note map (wow/design.md §3.5): unfolded like LilyPond's \\unfoldRepeats."""
import json
from fractions import Fraction
from pathlib import Path

import pytest

from jianpu_studio.engine import find_tool, render
from jianpu_studio.model import Note, Project, Score
from jianpu_studio.playmap import PlayMap, unfold_bars

FIXTURES = Path(__file__).parent / "fixtures" / "notation"


def fixture(name):
    return Project.from_dict(json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8")))


def sounding_onsets(score, part=0):
    """(quarter-note time, row) of each struck note: rests and tie continuations do not sound."""
    notes = score.parts[part].notes
    out = []
    for onset in PlayMap(score, part).onsets:
        note = notes[onset.row]
        if note.degree and not (onset.row and notes[onset.row - 1].tie):
            out.append((Fraction(onset.tick, 48), onset.row))
    return out


@pytest.mark.parametrize("name, order", [
    ("f04_volta", [0, 1, 2, 0, 1, 3, 4]),
    ("f05_volta_jie", [0, 1, 2, 1, 3, 1, 4]),
    ("f06_dc_al_fine", [0, 1, 2, 3, 0, 1]),
    ("f07_ds_al_coda", [0, 1, 2, 3, 1, 2, 4, 5]),
])
def test_unfolded_bar_order(name, order):
    assert unfold_bars(fixture(name).score) == order


def test_position_lookup_and_play_from_bar():
    score = fixture("f07_ds_al_coda").score
    play = PlayMap(score)
    seconds_per_quarter = 60 / score.tempo
    assert play.at(0).row == 0
    assert play.at(4 * 4 * seconds_per_quarter).bar == 1          # bar 2 starts after 4 quarters... (4 notes)
    assert play.first_time_of_bar(4) == pytest.approx(6 * 4 * seconds_per_quarter)   # coda after D.S. pass
    assert play.at(play.total_seconds + 1) is None
    assert PlayMap(Score(notes=[Note(1)] * 4)).total_seconds == pytest.approx(4 * 60 / 96)


@pytest.mark.integration
@pytest.mark.parametrize("name", ["f04_volta", "f06_dc_al_fine", "f07_ds_al_coda"])
def test_map_matches_the_real_lilypond_midi(tmp_path, name):
    tool = find_tool("lilypond")
    if not tool:
        pytest.skip("JIANPU_LILYPOND not configured")
    from test_notation_engine import midi_note_onsets
    project = fixture(name)
    result = render(project.notation(), tmp_path / name, tool.resolve(), create_pdf=False)
    midi = [time for time, _ in midi_note_onsets(result.midi)]
    ours = [time for time, _ in sounding_onsets(project.score)]
    assert ours == midi
