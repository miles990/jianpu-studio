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
    view = rebar.preview(score, result, meter="3/4")
    assert view.split and view.bars_before == 2 and view.bars_after == 3


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


# ---------------------------------------------------------------- review 3 (#7, #11-#13, #17, #18)
from jianpu_studio.app import MainWindow  # noqa: E402
from jianpu_studio.model import Part, Project  # noqa: E402
from jianpu_studio.notation_rules import bar_items  # noqa: E402


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(MainWindow, "render_score", lambda *a, **k: None)
    monkeypatch.setattr(MainWindow, "error", lambda self, title, error: pytest.fail(f"{title}: {error}"))
    win = MainWindow(recovery=False)
    win.recovery_path = tmp_path / "recovery.jps"
    win.autosave.stop()
    win.asked = []

    def choose(refused, key="rebar"):
        win.asked.append(refused)
        return win.answer
    win.answer = "rebar"
    monkeypatch.setattr(win, "choose_rhythm_option", choose)
    monkeypatch.setattr(win, "confirm_losses", lambda score, lost: pytest.fail(f"second question: {lost}"))
    qtbot.addWidget(win, before_close_func=lambda w: setattr(w, "dirty", False))
    win.resize(1380, 850)
    return win


def _clean(score):
    return score.rhythm_error() is None and score.notation_error() is None


def test_meter_change_without_a_crossing_still_completes_the_last_bar(window):
    # #11: four quarters in 4/4 -> 3/4 fit the new bars, but the last bar would be 2 beats short.
    window.reset(Project(Score(notes=[Note(i) for i in range(1, 5)])))
    before = window.project.to_dict()
    window.meter_field.setCurrentText("3/4")
    window.capture_fields()
    assert window.asked and "補上 2 拍休止符" in str(window.asked[0])
    score = window.project.score
    assert score.meter == "3/4" and _clean(score) and len(window.history) == 1
    window.undo()
    assert window.project.to_dict() == before


def test_a_meter_change_that_only_redraws_bar_lines_asks_nothing(window):
    window.reset(Project(Score(notes=[Note(i) for i in range(1, 7)] * 2)))
    window.meter_field.setCurrentText("3/4")
    window.capture_fields()
    assert not window.asked and window.project.score.meter == "3/4" and _clean(window.project.score)


def test_rebar_moves_a_final_barline_to_the_padded_end(window):
    # #12: what is offered can be applied; the final barline ends the music again.
    window.reset(Project(Score(notes=[Note(1, duration=1, bar_end=("bar.final",))])))
    window.meter_field.setCurrentText("3/4")
    window.capture_fields()
    score = window.project.score
    assert score.meter == "3/4" and _clean(score)
    assert score.notes[-1].degree == 0 and score.notes[-1].bar_end == ("bar.final",)
    assert "「終止線」會移到第 2 小節結尾" in str(window.asked[0])


def test_rebar_never_offers_what_it_cannot_apply():
    # The short last bar after a pickup cannot carry a bar-end sign: rebar uses a whole bar.
    score = Score(notes=[Note(1, duration=1), Note(2, duration=1, bar_end=("nav.fine",))])
    result = rebar.rebar(score, pickup=48)
    view = rebar.preview(score, result, pickup=48)
    assert view.problem is None and _clean(view.after) and view.after.notes[-1].bar_end == ("nav.fine",)


def test_pickup_padding_keeps_segno_on_its_note(window):
    # #13: a new pickup bar of rests in front; Segno stays on the first note of the old bar 1.
    notes = [Note(1, duration=1, bar_start=("nav.segno",)), Note(2, duration=1, bar_end=("nav.ds",))]
    window.reset(Project(Score(notes=notes)))
    window.answer = "pad"
    window.pickup_field.setCurrentIndex(window.pickup_field.findData(48))
    score = window.project.score
    assert score.pickup == 48 and _clean(score)
    assert bar_items(score) == [((), ()), (("nav.segno",), ()), ((), ("nav.ds",))]
    assert score.notes[1] == notes[0]


def test_filling_out_a_pickup_keeps_its_start_sign_at_the_bar_start(window):
    notes = [Note(1, bar_start=("nav.segno",)), Note(2, duration=1), Note(3, duration=2, dotted=True,
                                                                        bar_end=("nav.ds",))]
    window.reset(Project(Score(pickup=48, notes=notes)))
    window.answer = "pad"
    window.pickup_field.setCurrentIndex(window.pickup_field.findData(0))
    score = window.project.score
    assert score.pickup == 0 and _clean(score) and len(score.part_bars(0)) == 3
    assert bar_items(score)[0] == (("nav.segno",), ())
    assert "Segno" in str(window.asked[0]) and "會移到第 1 小節開頭" in str(window.asked[0])


@pytest.mark.parametrize("pickup,notes", [
    (36, [Note(1), Note(2, duration=1)]),                                  # rests would cross the pickup
    (144, [Note(1), Note(2, duration=2, dotted=True)]),                    # short last bar no longer valid
])
def test_pickup_padding_keeps_the_grid_and_the_last_bar_valid(window, pickup, notes):
    # #17
    window.reset(Project(Score(pickup=48, notes=notes)))
    before = window.project.to_dict()
    window.answer = "pad"
    window.pickup_field.setCurrentIndex(window.pickup_field.findData(pickup))
    score = window.project.score
    assert score.pickup == pickup and _clean(score)
    window.undo()
    assert window.project.to_dict() == before


def test_removing_a_bar_sign_after_padding_does_not_raise(window):
    # #17 follow-on: the inspector's chip removal looked up a bar the model did not have.
    from jianpu_studio import notation_edit
    score = Score(pickup=48, notes=[Note(1), Note(2, duration=1, bar_end=("bar.double",))])
    assert notation_edit._bar_has(score, 5, "bar.double") is False
    window.reset(Project(score))
    window.answer = "pad"
    window.pickup_field.setCurrentIndex(window.pickup_field.findData(36))
    window.notes.select_bars([len(window.project.score.part_bars(0)) - 1])
    window.remove_mark("bar.double")
    assert all("bar.double" not in end for _, end in bar_items(window.project.score))


def test_preview_counts_split_notes_in_every_part():
    # #18: one whole note per part, 4/4 -> 3/4: two notes split, not one.
    score = Score(parts=[Part("甲", [Note(1, duration=1)]), Part("乙", [Note(1, duration=1)])])
    result = rebar.rebar(score, meter="3/4")
    view = rebar.preview(score, result, meter="3/4")
    assert len(view.split) == 2 and "有 2 個音" in rebar.describe(score, view, "整首重新分小節")


def test_preview_bar_counts_come_from_the_applied_change():
    # #18: filling out a pickup keeps the bar count (the helper used to claim one more bar);
    # the 3-beat last bar that matched the pickup is completed.
    score = Score(pickup=48, notes=[Note(1), Note(2, duration=1), Note(3, duration=2, dotted=True)])
    view = rebar.preview(score, rebar.pad_to_pickup(score, 0), pickup=0)
    assert (view.bars_before, view.bars_after, view.lead, view.filled) == (3, 3, 144, 48)


def test_trimming_a_first_rest_keeps_its_bar_end_sign(window):
    # #18: the remaining rest keeps the double barline (no loss question).
    window.reset(Project(Score(notes=[Note(0, duration=1, bar_end=("bar.double",)), Note(1, duration=1)])))
    window.answer = "trim"
    window.pickup_field.setCurrentIndex(window.pickup_field.findData(48))
    score = window.project.score
    assert score.pickup == 48 and _clean(score) and bar_items(score)[0] == ((), ("bar.double",))


def test_split_after_grace_stays_on_the_last_piece_and_front_grace_on_the_first():
    # #7 (rebar side): a half note with both graces re-barred to a one-quarter pickup.
    score = Score(notes=[Note(1, duration=2, grace_before="5", grace_after="2"), Note(3), Note(4)])
    after = rebar.preview(score, rebar.rebar(score, pickup=48), pickup=48).after
    pieces = [n for n in after.notes if n.degree == 1]
    assert len(pieces) == 2 and pieces[0].tie and not pieces[1].tie
    assert (pieces[0].grace_before, pieces[0].grace_after) == ("5", "")
    assert (pieces[1].grace_before, pieces[1].grace_after) == ("", "2")
    assert _clean(after)


def test_split_lines_that_arrive_or_leave_at_a_note_stay_legal():
    # A hairpin ending on a note with its dynamic, and a glissando into and out of a split note.
    notes = [Note(1, spans_start=("hairpin.cresc",)), Note(2), Note(3), Note(4),
             Note(5, duration=1, marks=("dyn.f",), spans_end=("hairpin.cresc",), spans_start=("slide.gliss",)),
             Note(6, spans_end=("slide.gliss",)), Note(1), Note(1), Note(1)]
    score = Score(notes=notes)
    assert _clean(score)
    view = rebar.preview(score, rebar.rebar(score, meter="3/4"), meter="3/4")
    assert view.problem is None and _clean(view.after)


def test_bar_line_question_is_rebuilt_in_the_new_language(window):
    from jianpu_studio import i18n
    window.reset(Project(Score(notes=[Note(1, duration=1, bar_end=("bar.final",))])))
    window.answer = None
    window.meter_field.setCurrentText("3/4")
    window.capture_fields()
    refused = window.asked[0]
    assert window.project.score.meter == "4/4" and "終止線" in str(refused)
    i18n.set_language(i18n.SIMPLIFIED)
    again = refused.rebuild()
    assert "终止线" in str(again) and "小节" in str(again) and [c.key for c in again.choices] == ["rebar"]


def test_a_one_bar_rest_score_gets_a_bar_after_its_new_pickup(window):
    # Found by tests/test_rebar_sweep.py: redrawing one bar of rests as a pickup left a score
    # that was only a pickup (its last bar "missing" beats).
    window.reset(Project(Score(notes=[Note(0, duration=1)])))
    window.pickup_field.setCurrentIndex(window.pickup_field.findData(48))
    score = window.project.score
    assert score.pickup == 48 and _clean(score) and len(score.part_bars(0)) == 2
