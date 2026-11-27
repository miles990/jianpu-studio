"""Moving bar lines of written music (round 6): pickup and meter changes."""
import pytest

from jianpu_studio import rebar
from jianpu_studio.model import Note, Score


def test_rebar_splits_notes_into_tied_pieces_and_keeps_every_item():
    notes = [Note(1, duration=1, marks=("finger.1",), spans_start=("line.slur",), text="中速"),
             Note(2, duration=1, spans_end=("line.slur",), grace_after="3")]
    score = Score(notes=notes)
    result = rebar.rebar(score, meter="3/4")
    after = Score(meter="3/4", notes=result.parts[0])
    assert after.rhythm_error() is None and after.notation_error() is None
    first = [n for n in after.notes if n.degree == 1]
    assert first[0].marks == ("finger.1",) and first[0].text == "中速" and first[0].spans_start == ("line.slur",)
    assert all(n.tie for n in first[:-1]) and not first[-1].tie
    second = [n for n in after.notes if n.degree == 2]
    assert second[-1].spans_end == ("line.slur",) and second[-1].grace_after == "3"
    assert result.split and result.bars_before == 2 and result.bars_after == 3


def test_rebar_refuses_a_cut_triplet_or_a_bar_sign_with_the_reason():
    triplet = [Note(1, duration=2), Note(1, duration=8), Note(2, duration=8, tuplet=3, tuplet_start=True),
               Note(2, duration=8, tuplet=3), Note(2, duration=8, tuplet=3, tuplet_end=True)]
    with pytest.raises(ValueError, match="三連音"):
        rebar.rebar(Score(notes=triplet), meter="3/4")
    signed = [Note(1, duration=1, bar_end=("bar.double",)), Note(2, duration=1)]
    with pytest.raises(ValueError, match="小節記號"):
        rebar.rebar(Score(notes=signed), meter="3/4")


def test_all_rest_scores_keep_their_bar_count_and_bar_lines():
    rests = [Note(0, duration=1)] * 3 + [Note(0, duration=1, bar_end=("bar.final",))]
    score = Score(notes=rests)
    assert rebar.rests_only(score)
    result = rebar.rebar_rests(score, meter="3/4", pickup=48)
    after = Score(meter="3/4", pickup=48, notes=result.parts[0])
    assert len(after.part_bars(0)) == 4 and after.rhythm_error() is None
    assert after.notes[-1].bar_end == ("bar.final",)
