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


SEMITONES = (0, 0, 2, 4, 5, 7, 9, 11)


def written_pitch(note):
    return SEMITONES[note.degree] + 12 * note.octave + {"#": 1, "b": -1}.get(note.accidental, 0)


def bar(degree, **items):
    return Note(degree, duration=1, **items)


RS, RE = ("bar.repeat_start",), ("bar.repeat_end",)
STRUCTURES = {
    # the review's case: D.C. on the closing edge of the last ending (P1.8)
    "dc at last volta": [bar(1, bar_start=RS, bar_end=RE), bar(2, bar_start=("volta.1",)),
                         bar(3, bar_start=("volta.2",), bar_end=("volta.end", "nav.dc"))],
    "dc at a repeat end": [bar(1), bar(2, bar_start=RS), bar(3, bar_end=("bar.repeat_end", "nav.dc"))],
    "dc without fine, trailing bar": [bar(1), bar(2, bar_end=("nav.dc",)), bar(3)],
    "fine at last volta, dc at the end": [bar(1, bar_start=RS, bar_end=RE), bar(2, bar_start=("volta.1",)),
                                          bar(3, bar_start=("volta.2",), bar_end=("volta.end", "nav.fine")),
                                          bar(4), bar(5, bar_end=("nav.dc",))],
    "three endings then ds": [bar(1, bar_start=("nav.segno",)), bar(2, bar_start=RS, bar_end=RE),
                              bar(3, bar_start=("volta.1",)), bar(4, bar_start=("volta.2",)),
                              bar(5, bar_start=("volta.3",), bar_end=("volta.end",)), bar(6, bar_end=("nav.ds",))],
    "double repeat then dc al fine": [bar(1, bar_start=RS, bar_end=("bar.repeat_both", "nav.fine")),
                                      bar(2, bar_end=RE), bar(3, bar_end=("nav.dc",))],
    "ds al coda over a repeat": [bar(1), bar(2, bar_start=("nav.segno",)), bar(3, bar_start=RS, bar_end=RE),
                                 bar(4, bar_end=("nav.tocoda",)), bar(5, bar_end=("nav.ds",)), bar(6), bar(7)],
    "ds without coda, trailing bars": [bar(1), bar(2, bar_start=("nav.segno",)), bar(3, bar_end=("nav.ds",)),
                                       bar(4), bar(5)],
    "to coda at the last ending": [bar(1, bar_start=("nav.segno",)), bar(2, bar_start=RS, bar_end=RE),
                                   bar(3, bar_start=("volta.1",)),
                                   bar(4, bar_start=("volta.2",), bar_end=("volta.end", "nav.tocoda")),
                                   bar(5, bar_end=("nav.ds",)), bar(6)],
    "fine at a repeat end":[bar(1), bar(2, bar_start=RS, bar_end=("bar.repeat_end", "nav.fine")),
                             bar(3, bar_end=("nav.dc",))],
    "repeat at the very start, ds al fine": [bar(1, bar_start=RS, bar_end=RE), bar(2, bar_start=("nav.segno",)),
                                             bar(3, bar_end=("nav.fine",)), bar(4, bar_end=("nav.ds",))],
}


def _real_midi(tmp_path, project):
    tool = find_tool("lilypond")
    if not tool:
        pytest.skip("JIANPU_LILYPOND not configured")
    from test_notation_engine import midi_note_onsets
    result = render(project.notation(), tmp_path / "map", tool.resolve(), create_pdf=False)
    assert "warning" not in result.log.lower(), result.log
    return midi_note_onsets(result.midi)


def assert_map_matches_midi(score, midi, part=0):
    """Same onset times AND the same notes: every MIDI pitch minus the mapped note's written
    pitch is one constant (the key's transposition), so a map that plays the wrong bar fails."""
    notes = score.parts[part].notes
    ours = sounding_onsets(score, part)
    assert [time for time, _ in ours] == [time for time, _ in midi]
    offsets = {pitch - written_pitch(notes[row]) for (_, row), (_, pitch) in zip(ours, midi)}
    assert len(offsets) == 1, (ours, midi)


@pytest.mark.integration
@pytest.mark.parametrize("name", ["f04_volta", "f06_dc_al_fine", "f07_ds_al_coda"])
def test_map_matches_the_real_lilypond_midi(tmp_path, name):
    project = fixture(name)
    assert_map_matches_midi(project.score, _real_midi(tmp_path, project))


@pytest.mark.integration
@pytest.mark.parametrize("name", list(STRUCTURES))
def test_map_matches_real_midi_for_repeat_and_jump_structures(tmp_path, name):
    score = Score(key="D", notes=list(STRUCTURES[name]))
    assert score.rhythm_error() is None and score.notation_error() is None
    assert_map_matches_midi(score, _real_midi(tmp_path, Project(score)))


def test_dc_at_the_last_ending_returns_in_the_map():
    score = Score(notes=list(STRUCTURES["dc at last volta"]))
    assert unfold_bars(score) == [0, 1, 0, 2, 0, 1, 0, 2]
    assert PlayMap(score).total_seconds == pytest.approx(8 * 4 * 60 / score.tempo)


def test_the_oracle_rejects_a_map_with_swapped_bars():
    """Review P2.2: equal onset times alone would accept a map that plays bar 2 first."""
    score = Score(notes=[Note(1)] * 4 + [Note(5)] * 4)
    midi = [(Fraction(i), 60 + (0 if i < 4 else 7)) for i in range(8)]
    assert_map_matches_midi(score, midi)
    swapped = midi[4:] + midi[:4]
    swapped = [(Fraction(i), pitch) for i, (_, pitch) in enumerate(swapped)]
    with pytest.raises(AssertionError):
        assert_map_matches_midi(score, swapped)


@pytest.mark.parametrize("trailing", [1, 2])
def test_fine_with_music_after_the_jump_is_refused(trailing):
    """Review P1.7: after D.C./D.S. al Fine the bars after the jump never play; LilyPond played them."""
    notes = [bar(1, bar_start=RS, bar_end=RE), bar(2, bar_start=("volta.1",)),
             bar(3, bar_start=("volta.2",), bar_end=("volta.end", "nav.fine")), bar(4),
             bar(5, bar_end=("nav.dc",))] + [bar(6)] * trailing
    assert "最後一小節" in Score(notes=notes).notation_error()
    simple = [bar(1), bar(2, bar_end=("nav.fine",)), bar(3, bar_end=("nav.dc",))] + [bar(4)] * trailing
    assert "第 4 小節" in Score(notes=simple).notation_error()
    assert Score(notes=simple[:3]).notation_error() is None


@pytest.mark.parametrize("end", ["bar.repeat_end", "bar.repeat_both"])
def test_to_coda_on_a_plain_repeat_end_is_refused(end):
    """Upstream turns it into an alternative of that repeat (wrong order, stray volta bracket)."""
    notes = [bar(1, bar_start=("nav.segno",)), bar(2, bar_start=RS, bar_end=(end, "nav.tocoda")),
             bar(3, bar_end=RE if end == "bar.repeat_both" else ()), bar(4, bar_end=("nav.ds",)), bar(5)]
    assert "To Coda" in Score(notes=notes).notation_error()


def test_fine_on_a_repeat_end_stops_after_the_repeat_on_the_return():
    score = Score(notes=list(STRUCTURES["fine at a repeat end"]))
    assert unfold_bars(score) == [0, 1, 1, 2, 0, 1, 1]
