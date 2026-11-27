"""Group A (round 4): rhythm edits in an existing score are local and safe.

Changing a duration, deleting, inserting or pasting never moves a later bar line; when an
edit cannot fit, the refusal names the place 「第 X 小節第 Y 拍」 and offers the ways out.
"""
from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtCore import Qt

from jianpu_studio import rhythm_edit
from jianpu_studio.app import MainWindow
from jianpu_studio.model import Note, Part, Project, Score, demo_project, load_project
from jianpu_studio.notation_rules import note_place
from jianpu_studio.rhythm_edit import RhythmRefused, change_notes, delete_notes, insert_notes

DUET = Path(__file__).resolve().parents[1] / "examples" / "duet.jps"


def tokens(notes):
    return [n.notation_token() for n in notes]


def bars(score, part=0):
    notes = score.parts[part].notes
    return [tokens(notes[a:b]) for a, b in score.part_bars(part)]


def bar_lines(score, part=0):
    """Tick position of every bar line plus the bar items on each bar (the thing that must not move)."""
    notes = score.parts[part].notes
    out, position = [], 0
    for a, b in score.part_bars(part):
        out.append((position, notes[a].bar_start, notes[b - 1].bar_end))
        position += sum(n.ticks for n in notes[a:b])
    return out


def with_notes(score, notes, part=0):
    out = score.copy()
    out.parts[part].notes = list(notes)
    return out


# ---------------------------------------------------------------- pure functions
def test_shorter_note_fills_its_own_bar_with_rests_and_later_bars_do_not_move():
    score = demo_project().score
    result = change_notes(score, 0, [1], lambda n: replace(n, duration=8))
    after = with_notes(score, result.notes)
    assert bars(after)[0] == ["c1", "q1", "q0", "c5", "c5"]
    assert bars(after)[1:] == bars(score)[1:]
    assert bar_lines(after)[1:] == bar_lines(score)[1:]
    assert after.rhythm_error() is None and after.notation_error() is None
    assert result.selection == (1,)


def test_shorter_run_keeps_the_notes_together_and_rests_follow_on_beats():
    score = Score(notes=[Note(1), Note(2), Note(3), Note(4)])
    result = change_notes(score, 0, [0, 1, 2, 3], lambda n: replace(n, duration=8))
    assert tokens(result.notes) == ["q1", "q2", "q3", "q4", "c0", "c0"]       # jianpu: one 0 per beat
    assert result.selection == (0, 1, 2, 3)


def test_longer_note_takes_following_rests_in_its_bar():
    score = Score(notes=[Note(1), Note(0), Note(0), Note(5), Note(6), Note(5), Note(3), Note(2)])
    result = change_notes(score, 0, [0], lambda n: replace(n, duration=2, dotted=True))
    after = with_notes(score, result.notes)
    assert bars(after) == [["1 - -", "c5"], ["c6", "c5", "c3", "c2"]]


def test_longer_note_without_room_is_refused_with_the_place_and_choices():
    score = demo_project().score
    with pytest.raises(RhythmRefused) as info:
        change_notes(score, 0, [0], lambda n: replace(n, duration=2))
    refused = info.value
    assert "第 1 小節第 1 拍" in str(refused) and "後面的小節線不會移動" in str(refused)
    assert "個音符" not in str(refused)
    assert [c.key for c in refused.choices] == ["overwrite"]
    # overwrite stays inside the bar
    result = change_notes(score, 0, [0], lambda n: replace(n, duration=2), choice="overwrite")
    after = with_notes(score, result.notes)
    assert bars(after)[0] == ["1 -", "c5", "c5"] and bars(after)[1:] == bars(score)[1:]


def test_fit_choice_uses_the_longest_value_that_fits():
    score = Score(notes=[Note(1), Note(0), Note(5), Note(5)])
    with pytest.raises(RhythmRefused) as info:
        change_notes(score, 0, [0], lambda n: replace(n, duration=2, dotted=True))
    assert [c.key for c in info.value.choices] == ["fit", "overwrite"]
    assert "改為二分" in info.value.choices[0].label
    result = change_notes(score, 0, [0], lambda n: replace(n, duration=2, dotted=True), choice="fit")
    assert tokens(result.notes) == ["1 -", "c5", "c5"]


def test_the_duet_case_is_local_in_both_parts_with_repeats_and_endings():
    score = load_project(DUET).score
    for part in range(len(score.parts)):
        notes = score.parts[part].notes
        row = next(i for i, n in enumerate(notes) if n.duration in (2, 4) and n.degree and not n.tuplet)
        result = change_notes(score, part, [row], lambda n: replace(n, duration=n.duration * 2))
        after = with_notes(score, result.notes, part)
        assert after.notation_error() is None and after.rhythm_error() is None
        assert bar_lines(after, 0) == bar_lines(score, 0)
        assert [sum(n.ticks for n in after.parts[part].notes[a:b]) for a, b in after.part_bars(part)] == \
            [sum(n.ticks for n in notes[a:b]) for a, b in score.part_bars(part)]


def test_bar_items_stay_with_the_bar_when_its_last_note_changes():
    notes = [Note(1, bar_start=("bar.repeat_start",)), Note(2), Note(3), Note(4, bar_end=("bar.repeat_end",)),
             Note(5), Note(5), Note(5), Note(5)]
    score = Score(notes=notes)
    result = change_notes(score, 0, [3], lambda n: replace(n, duration=16))
    after = with_notes(score, result.notes)
    assert after.notation_error() is None
    assert bars(after)[0] == ["R{ c1", "c2", "c3", "s4", "q0. }"]
    assert after.notes[3].bar_end == () and after.notes[4].bar_end == ("bar.repeat_end",)


def test_pickup_and_tie_are_handled():
    score = Score(pickup=48, notes=[Note(5, tie=True), Note(5), Note(1), Note(2), Note(3)])
    result = change_notes(score, 0, [0], lambda n: replace(n, duration=8))
    after = with_notes(score, result.notes)
    assert tokens(after.notes[:2]) == ["q5", "q0"] and not after.notes[0].tie   # a rest now follows: tie removed
    assert "1" in result.notice and after.notation_error() is None
    assert bar_lines(after)[1:] == bar_lines(score)[1:]


def test_triplet_member_changes_only_as_a_whole_group():
    group = [Note(1, duration=8, tuplet=3, tuplet_start=True), Note(2, duration=8, tuplet=3),
             Note(3, duration=8, tuplet=3, tuplet_end=True)]
    score = Score(notes=group + [Note(0), Note(0), Note(0)])
    with pytest.raises(RhythmRefused, match="三連音"):
        change_notes(score, 0, [1], lambda n: replace(n, duration=16))
    result = change_notes(score, 0, [0, 1, 2], lambda n: replace(n, duration=4))
    after = with_notes(score, result.notes)
    assert after.notation_error() is None and after.rhythm_error() is None
    assert sum(n.ticks for n in after.notes) == score.bar_ticks


def test_delete_in_the_middle_leaves_rests_and_at_the_end_removes():
    score = demo_project().score
    middle = delete_notes(score, 0, [2])
    assert tokens(middle.notes[:5]) == ["c1", "c1", "c0", "c5", "c6"]
    assert bar_lines(with_notes(score, middle.notes)) == bar_lines(score)
    end = delete_notes(score, 0, [len(score.notes) - 1])
    assert len(end.notes) == len(score.notes) - 1
    with pytest.raises(RhythmRefused, match="休止符會保留拍數"):
        delete_notes(with_notes(score, middle.notes), 0, [2])


def test_insert_takes_rests_after_the_point_and_refuses_with_place_otherwise():
    score = Score(notes=[Note(1), Note(2), Note(0), Note(0), Note(5), Note(5), Note(5), Note(5)])
    result = insert_notes(score, 0, 1, [Note(7)])
    assert bars(with_notes(score, result.notes)) == [["c1", "c7", "c2", "c0"], ["c5", "c5", "c5", "c5"]]
    with pytest.raises(RhythmRefused) as info:
        insert_notes(score, 0, 5, [Note(3)])
    assert "第 2 小節第 2 拍" in str(info.value) and [c.key for c in info.value.choices] == ["ripple"]
    ripple = insert_notes(score, 0, 5, [Note(3)], choice="ripple")
    assert len(ripple.notes) == len(score.notes) + 1


def test_insert_ripple_is_not_offered_when_bars_would_break():
    score = demo_project().score
    with pytest.raises(RhythmRefused) as info:
        insert_notes(score, 0, 1, [Note(3)])
    assert info.value.choices == () and "不能這樣做" in str(info.value)


def test_whole_bars_inserted_on_a_bar_line_add_rest_bars_to_the_other_parts():
    bar = [Note(1), Note(2), Note(3), Note(4)]
    score = Score(parts=[Part("A", bar * 2), Part("B", bar * 2)])
    result = insert_notes(score, 0, 4, [Note(5, duration=1)])
    assert len(result.others[1]) == 8 + 4 and all(n.degree == 0 for n in result.others[1][4:8])


def test_messages_locate_by_bar_and_beat_never_by_note_index():
    score = Score(meter="3/4", pickup=48, notes=[Note(5), Note(1), Note(2, duration=8), Note(3, duration=8), Note(4)])
    assert note_place(score, 0, 0) == "弱起小節第 3 拍"
    assert note_place(score, 0, 3) == "第 1 小節第 2 拍後半"
    bad = Score(notes=[Note(1, tie=True), Note(2), Note(3), Note(4)])
    problem = bad.notation_error()
    assert problem.startswith("第 1 小節第 1 拍") and problem.row == 0 and problem.fix == ("tie", True)
    duet = load_project(DUET).score
    two = duet.copy()
    two.parts[1].notes[1] = replace(two.parts[1].notes[1], tie=True)
    assert two.notation_error().startswith(duet.parts[1].name + "：第 1 小節")


# ---------------------------------------------------------------- the editor
@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(MainWindow, "render_score", lambda *a, **k: None)
    monkeypatch.setattr(MainWindow, "error", lambda self, title, error: pytest.fail(f"{title}: {error}"))
    win = MainWindow(recovery=False)
    win.recovery_path = tmp_path / "recovery.jps"
    win.autosave.stop()

    def before_close(w):
        w.dirty = False
    qtbot.addWidget(win, before_close_func=before_close)
    win.resize(1380, 850)
    return win


def edit_mode(window):
    window.input_mode.setCurrentIndex(window.input_mode.findData("edit"))


@pytest.mark.parametrize("example", ["twinkle", "duet"])
def test_editor_quarter_to_eighth_in_edit_mode_is_local_and_undoable(window, example):
    if example == "twinkle":
        window.reset(demo_project())
    else:
        window.reset(load_project(DUET))
    edit_mode(window)
    score = window.project.score
    notes = score.notes
    row = next(i for i, n in enumerate(notes) if n.duration == 4 and n.degree and not n.tuplet)
    before_lines = bar_lines(score)
    window.notes.select_rows([row], row)
    window.duration_clicked(8)
    after = window.project.score
    assert after.notes[row].duration == 8 and after.notes[row + 1].degree == 0
    assert bar_lines(after) == before_lines
    assert after.notation_error() is None and after.rhythm_error() is None
    window.undo()
    assert window.project.score.notes == notes


def test_editor_refusal_selects_the_place_and_cancel_changes_nothing(window, monkeypatch):
    window.reset(demo_project())
    edit_mode(window)
    asked = []
    monkeypatch.setattr(window, "choose_rhythm_option", lambda refused: asked.append(str(refused)) or None)
    original = list(window.project.score.notes)
    window.notes.select_rows([0], 0)
    window.duration_clicked(2)
    assert asked and "第 1 小節第 1 拍" in asked[0]
    assert window.project.score.notes == original
    monkeypatch.setattr(window, "choose_rhythm_option", lambda refused: "overwrite")
    window.duration_clicked(2)
    assert window.project.score.notes[0].duration == 2
    assert bar_lines(window.project.score)[1:] == bar_lines(Score(notes=original))[1:]


def test_editor_delete_key_keeps_bars_and_dot_toggle_is_local(window, qtbot):
    window.reset(demo_project())
    edit_mode(window)
    before = bar_lines(window.project.score)
    window.notes.select_rows([2], 2)
    window.notes.setFocus()
    qtbot.keyClick(window.notes, Qt.Key.Key_Delete)
    assert window.project.score.notes[2].degree == 0 and bar_lines(window.project.score) == before
    window.notes.select_rows([1], 1)
    window.note_command("dot")            # quarter -> dotted quarter takes half of the rest after it
    after = window.project.score
    assert after.notes[1].dotted and bar_lines(after) == before


def test_editor_insert_mode_in_the_middle_uses_rests_or_asks(window, monkeypatch):
    score = Score(notes=[Note(1), Note(2), Note(0), Note(0), Note(5), Note(5), Note(5), Note(5)])
    window.reset(Project(score))
    window.input_mode.setCurrentIndex(window.input_mode.findData("insert"))
    window.insert_position.setCurrentIndex(window.insert_position.findData("after"))
    window.notes.select_rows([0], 0)
    window.input_pitch(7)
    assert tokens(window.project.score.notes[:4]) == ["c1", "c7", "c2", "c0"]
    asked = []
    monkeypatch.setattr(window, "choose_rhythm_option", lambda refused: asked.append(refused.choices) or None)
    window.notes.select_rows([4], 4)
    before = list(window.project.score.notes)
    window.input_pitch(3)
    assert asked and window.project.score.notes == before


def test_editor_located_problem_jumps_and_fixes_orphan_mark(window):
    # Round 6: a lone :‖ is legal (repeat from the beginning); an orphan 「房子結束」 is not.
    notes = [Note(1), Note(2), Note(3), Note(4), Note(5), Note(5), Note(5), Note(5, bar_end=("volta.end",))]
    window.reset(Project(Score(notes=notes)))
    assert window.fill_button.isVisibleTo(window) and window.fill_button.text() == "移除孤立記號"
    window.goto_problem()
    assert window.notes.currentRow() == 7
    window.fill_bar()
    assert window.project.score.notation_error() is None
    assert window.project.score.notes[7].bar_end == ()


def test_meter_change_that_would_break_bars_asks_and_can_be_cancelled(window, monkeypatch):
    # Round 6: instead of a flat refusal the editor offers to re-bar, showing what changes.
    window.reset(demo_project())
    asked = []
    monkeypatch.setattr(window, "choose_rhythm_option", lambda refused: asked.append(refused) or None)
    before = window.project.to_dict()
    window.meter_field.setCurrentText("3/4")
    window.capture_fields()
    assert window.project.to_dict() == before and window.meter_field.currentText() == "4/4"
    assert [c.key for c in asked[0].choices] == ["rebar"]
    assert "小節 →" in str(asked[0]) and "延音線" in str(asked[0]) and "復原" in str(asked[0])


def test_meter_change_rebars_with_ties_in_one_undo_step(window, monkeypatch):
    window.reset(demo_project())
    monkeypatch.setattr(window, "choose_rhythm_option", lambda refused: "rebar")
    before = window.project.to_dict()
    pitches = [(n.degree, n.octave) for n in window.project.score.notes if n.degree]
    ticks = sum(n.ticks for n in window.project.score.notes)
    window.meter_field.setCurrentText("3/4")
    window.capture_fields()
    score = window.project.score
    assert score.meter == "3/4" and score.rhythm_error() is None and score.notation_error() is None
    # Same music: the sounding notes (tied pieces joined) and length (plus rests to finish the bar).
    joined, previous_tied = [], False
    for n in score.notes:
        if n.degree and not previous_tied:
            joined.append((n.degree, n.octave))
        previous_tied = n.tie
    assert joined == pitches and sum(n.ticks for n in score.notes) >= ticks
    window.undo()
    assert window.project.to_dict() == before


def test_pickup_on_an_all_rest_score_just_redraws_the_bars(window):
    rests = [Note(0, duration=1)] * 8
    window.reset(Project(Score(notes=rests)))
    window.pickup_field.setCurrentIndex(window.pickup_field.findData(48))
    score = window.project.score
    assert score.pickup == 48 and len(score.part_bars(0)) == 8
    assert score.rhythm_error() is None and score.notation_error() is None
    window.undo()
    assert window.project.score.pickup == 0 and window.project.score.notes == rests


def test_pickup_on_written_music_offers_trim_or_a_pickup_bar(window, monkeypatch):
    first = [Note(0, duration=2), Note(0), Note(5)]               # 3 beats of rest, then the upbeat 5
    music = first + [Note(1), Note(2), Note(3), Note(4)] * 2
    window.reset(Project(Score(notes=music)))
    asked = []
    monkeypatch.setattr(window, "choose_rhythm_option", lambda refused: asked.append(refused) or "trim")
    window.pickup_field.setCurrentIndex(window.pickup_field.findData(48))
    score = window.project.score
    assert [c.key for c in asked[0].choices] == ["trim", "pad", "rebar"]
    assert score.pickup == 48 and score.notes[0] == Note(5) and score.notes[1:5] == music[3:7]
    assert score.rhythm_error() is None
    window.undo()
    # No leading rests: a pickup bar of rests in front, the bars after it unchanged.
    music = [Note(1), Note(2), Note(3), Note(4)] * 2
    window.reset(Project(Score(notes=music)))
    asked.clear()
    monkeypatch.setattr(window, "choose_rhythm_option", lambda refused: asked.append(refused) or "pad")
    window.pickup_field.setCurrentIndex(window.pickup_field.findData(72))
    score = window.project.score
    assert [c.key for c in asked[0].choices] == ["pad", "rebar"]
    assert score.pickup == 72 and score.notes[0].degree == 0 and score.notes[-8:] == music
    assert score.part_bars(0)[1] == (1, 5)


def test_rest_helpers_split_on_beats():
    score = Score()
    assert tokens(rhythm_edit.beat_rests(score, 24, 72)) == ["q0", "c0"]
    assert tokens(rhythm_edit.beat_rests(score, 0, 192)) == ["c0", "c0", "c0", "c0"]
