"""Independent review 2: local edits never destroy music silently.

Each test names the finding it covers (work/review2/codex-review2.md). An edit either keeps
every note, mark, line, bar item and the other parts intact, or names what it removes before
doing it; parts keep the same length; commands never escape with an exception.
"""
import sys
from dataclasses import replace

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from jianpu_studio import i18n
from jianpu_studio.app import MainWindow
from jianpu_studio.editing import EditActions
from jianpu_studio.model import Note, Part, Project, Score
from jianpu_studio.notation_rules import bar_items, crossing, span_pairs

REAL_CHOOSE = EditActions.choose_rhythm_option      # conftest replaces it on the class during tests


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(MainWindow, "render_score", lambda *a, **k: None)
    win = MainWindow(recovery=False)
    win.recovery_path = tmp_path / "recovery.jps"
    win.autosave.stop()
    win.errors = []
    monkeypatch.setattr(win, "error", lambda title, error: win.errors.append(str(error)))
    win.asked = []

    def decline(refused):
        win.asked.append(refused)
        return None
    win.choose_rhythm_option = decline

    def before_close(w):
        w.dirty = False
    qtbot.addWidget(win, before_close_func=before_close)
    win.resize(1380, 850)
    return win


def load(window, score, *, mode="edit", position="before"):
    window.reset(Project(score))
    window.input_mode.setCurrentIndex(window.input_mode.findData(mode))
    window.insert_position.setCurrentIndex(window.insert_position.findData(position))


def select(window, *rows):
    window.notes.select_rows(list(rows), rows[0] if rows else -1)


def totals(window):
    return [sum(n.ticks for n in p.notes) for p in window.project.score.parts]


def answer(window, key):
    """Accept the first question with ``key`` and record every question."""
    def choose(refused):
        window.asked.append(refused)
        return key if any(c.key == key for c in refused.choices) else None
    window.choose_rhythm_option = choose


# ---------------------------------------------------------------- P0 1
def test_two_runs_in_one_bar_never_overwrite_a_selected_note_or_desync_parts(window):
    notes = [Note(7)] + [Note(1, duration=8), Note(2, duration=8), Note(3, duration=8), Note(4, duration=8), Note(5)]
    load(window, Score(pickup=48, parts=[Part("upper", notes), Part("lower", list(notes))]))
    answer(window, "overwrite")
    select(window, 2, 3, 5)
    window.edit_property("duration", 2)
    score = window.project.score
    degrees = [n.degree for n in score.parts[0].notes]
    assert all(d in degrees for d in (2, 3, 5))                 # no selected note is discarded
    assert len(set(totals(window))) == 1
    assert crossing(score) is None and score.rhythm_error() is None


def test_two_runs_in_one_bar_use_their_own_room(window):
    # Each run takes the rests right after it; neither takes the other's.
    load(window, Score(notes=[Note(1), Note(0), Note(2), Note(0)]))
    select(window, 0, 2)
    window.edit_property("duration", 2)
    assert [(n.degree, n.duration) for n in window.part_notes()] == [(1, 2), (2, 2)]


# ---------------------------------------------------------------- P0 2
def test_overwrite_question_names_every_bar_and_every_note_it_removes(window):
    load(window, Score(notes=[Note(n) for n in [1, 2, 3, 4, 5, 6, 7, 1]]))
    answer(window, "overwrite")
    select(window, 0, 4)
    window.edit_property("duration", 2)
    question = window.asked[0]
    text = str(question)
    assert "第 1 小節第 2 拍的音符 2" in text and "第 2 小節第 2 拍的音符 6" in text
    assert "只在這一小節" not in [c.label for c in question.choices][0]
    assert len(window.asked) == 1                               # the overwrite answer was the confirmation
    bars = [[n.degree for n in window.part_notes()[a:b]] for a, b in window.project.score.part_bars()]
    assert bars == [[1, 3, 4], [5, 7, 1]]


def test_declining_the_overwrite_question_changes_nothing(window):
    load(window, Score(notes=[Note(n) for n in [1, 2, 3, 4, 5, 6, 7, 1]]))
    before = window.project.to_dict()
    select(window, 0, 4)
    window.edit_property("duration", 2)
    assert window.asked and window.project.to_dict() == before


# ---------------------------------------------------------------- P0 3
def test_deleting_the_last_note_keeps_jumps_and_bar_lines(window):
    score = Score(notes=[Note(1), Note(1), Note(1), Note(1, bar_end=("nav.fine",)),
                         Note(2), Note(2), Note(2), Note(2, bar_end=("nav.dc",))])
    load(window, score)
    select(window, 7)
    window.delete_note()
    assert bar_items(window.project.score) == [((), ("nav.fine",)), ((), ("nav.dc",))]
    assert window.project.score.rhythm_error() is None and not window.asked
    window.undo()
    assert window.project.score.parts[0].notes == score.parts[0].notes


def test_deleting_the_last_note_of_one_part_keeps_the_parts_equally_long(window):
    load(window, Score(parts=[Part("upper", [Note(1)] * 7 + [Note(1, bar_end=("bar.final",))]),
                              Part("lower", [Note(2)] * 8)]))
    select(window, 7)
    window.delete_note()
    assert totals(window) == [384, 384]
    assert bar_items(window.project.score)[-1] == ((), ("bar.final",))
    assert window.project.score.rhythm_error() is None


def test_deleting_the_end_of_a_single_part_draft_still_shortens_it(window):
    load(window, Score(notes=[Note(1), Note(2), Note(3)]))
    select(window, 2)
    window.delete_note()
    assert [n.degree for n in window.part_notes()] == [1, 2]


# ---------------------------------------------------------------- P0 4
def test_triplet_split_puts_text_once_and_collapse_refuses_to_drop_independent_text(window):
    load(window, Score(notes=[Note(1, text="中速"), Note(2, duration=2, dotted=True)]))
    select(window, 0)
    window.triplet_action()
    assert [n.text for n in window.part_notes()] == ["中速", "", "", ""]
    select(window, 1)
    window.ask_note_text = lambda current: "獨立表情"
    assert window.edit_note_text()
    before = window.project.to_dict()
    window.triplet_action(collapse=True)
    assert window.project.to_dict() == before
    assert window.errors and "上方文字" in window.errors[-1]


def test_triplet_collapse_keeps_the_first_notes_text(window):
    load(window, Score(notes=[Note(1, text="中速"), Note(2, duration=2, dotted=True)]))
    select(window, 0)
    window.triplet_action()
    select(window, 0)
    window.triplet_action(collapse=True)
    assert [(n.text, n.duration) for n in window.part_notes()] == [("中速", 4), ("", 2)]
    assert not window.asked


# ---------------------------------------------------------------- P0 5
def test_filling_a_rest_keeps_its_text_marks_and_lines(window):
    load(window, Score(notes=[Note(0, text="引子", marks=("dyn.p",), spans_start=("hairpin.cresc",)), Note(0),
                              Note(2, spans_end=("hairpin.cresc",)), Note(3)]), mode="fill")
    select(window, 0)
    window.input_pitch(3)
    first = window.part_notes()[0]
    assert (first.degree, first.text, first.marks, first.spans_start) == (3, "引子", ("dyn.p",), ("hairpin.cresc",))
    assert window.project.score.notation_error() is None and not window.asked


def test_partly_used_rest_keeps_its_marks_and_text(window):
    load(window, Score(notes=[Note(0, marks=("dyn.p",), text="安靜"), Note(3, duration=2, dotted=True)]),
         mode="insert")
    window.next_note = replace(window.next_note, duration=8)
    select(window, 0)
    window.input_pitch(5)
    rests = [(n.ticks, n.marks, n.text) for n in window.part_notes() if not n.degree]
    assert rests == [(24, ("dyn.p",), "安靜")]
    assert not window.asked


def test_a_fully_used_rest_with_marks_is_named_before_it_goes(window):
    load(window, Score(notes=[Note(1), Note(0, marks=("dyn.p",), text="安靜"), Note(3, duration=2)]),
         mode="insert", position="after")
    before = window.project.to_dict()
    select(window, 0)
    window.input_pitch(5)
    assert window.asked and "安靜" in str(window.asked[0]) and "第 1 小節第 2 拍" in str(window.asked[0])
    assert window.project.to_dict() == before                  # declined: nothing changed
    answer(window, "confirm")
    window.input_pitch(5)
    assert [n.degree for n in window.part_notes()] == [1, 5, 3]


# ---------------------------------------------------------------- P1 6
def test_unwritable_remainders_never_escape_as_exceptions(window, qtbot, monkeypatch):
    score = Score(notes=[Note(1, duration=32, dotted=True), Note(2, duration=32, dotted=True), Note(3, duration=32),
                         Note(4, duration=8), Note(5, duration=2, dotted=True)])
    load(window, score)
    escaped = []
    monkeypatch.setattr(sys, "excepthook", lambda *exc: escaped.append(exc))
    window.show()
    select(window, 0)
    qtbot.keyClick(window.notes, Qt.Key.Key_Period)
    QApplication.processEvents()
    assert not escaped
    result = window.project.score
    assert result.rhythm_error() is None and crossing(result) is None
    if result.parts[0].notes == score.parts[0].notes:
        assert "第 1 小節第 1 拍" in window.statusBar().currentMessage()


def test_inserting_into_a_dotted_32nd_rest_is_refused_with_a_place_or_done(window):
    score = Score(notes=[Note(0, duration=32, dotted=True), Note(2, duration=32, dotted=True), Note(3, duration=32),
                         Note(4, duration=8), Note(5, duration=2, dotted=True)])
    load(window, score, mode="insert")
    window.next_note = replace(window.next_note, duration=32)
    select(window, 0)
    window.input_pitch(5)                                      # must not raise
    assert window.project.score.rhythm_error() is None


def test_overwriting_part_of_a_triplet_takes_the_whole_group_after_naming_it(window):
    load(window, Score(notes=[Note(1), Note(2, duration=8, tuplet=3, tuplet_start=True), Note(3, duration=8, tuplet=3),
                              Note(4, duration=8, tuplet=3, tuplet_end=True), Note(5, duration=2)]))
    answer(window, "overwrite")
    select(window, 0)
    window.note_command("dot")
    assert window.asked and "音符 2" in str(window.asked[0]) and "音符 4" in str(window.asked[0])
    score = window.project.score
    assert score.parts[0].notes[0].dotted and score.rhythm_error() is None and score.notation_error() is None


# ---------------------------------------------------------------- P1 7
def test_notes_added_at_the_end_of_one_part_keep_all_parts_equally_long(window):
    load(window, Score(parts=[Part("upper", [Note(1, duration=1)]), Part("lower", [Note(2, duration=1)])]),
         mode="insert", position="end")
    window.input_pitch(3)
    assert totals(window) == [240, 240]
    assert window.project.score.parts[1].notes[-1].degree == 0


def test_pasting_at_the_end_of_the_third_part_keeps_the_parts_equally_long(window):
    bars = [Note(1)] * 8
    load(window, Score(parts=[Part("a", list(bars)), Part("b", list(bars)), Part("c", list(bars))]),
         mode="insert", position="end")
    window.switch_part(2)
    window.insert_clip([Note(5, duration=2)])
    assert len(set(totals(window))) == 1


# ---------------------------------------------------------------- P1 8
def test_deleting_inner_ends_of_two_slurs_never_joins_them(window):
    load(window, Score(notes=[Note(1, spans_start=("line.slur",)), Note(2, spans_end=("line.slur",)),
                              Note(3, spans_start=("line.slur",)), Note(4, spans_end=("line.slur",))]))
    answer(window, "confirm")
    select(window, 1, 2)
    window.delete_note()
    assert span_pairs(window.part_notes()) == []
    assert window.asked and "連線" in str(window.asked[0])     # the two surviving ends were named first


def test_inserting_between_tied_notes_never_ties_the_new_note(window):
    load(window, Score(notes=[Note(1, tie=True, text="left"), Note(1, text="right"), Note(0), Note(2)]),
         mode="insert")
    window.next_note = replace(window.next_note, duration=4)
    select(window, 1)
    before = window.project.to_dict()
    window.input_pitch(1)
    assert window.asked and "延音線" in str(window.asked[0])
    assert window.project.to_dict() == before
    answer(window, "confirm")
    window.input_pitch(1)
    notes = window.part_notes()
    assert [(n.text, n.tie) for n in notes[:3]] == [("left", False), ("", False), ("right", False)]


# ---------------------------------------------------------------- P1 14 / 15
def test_next_problem_reaches_a_notation_problem_behind_a_bar_problem(window):
    load(window, Score(parts=[Part("A", [Note(1)] * 4 + [Note(2)]),
                              Part("B", [Note(3, spans_start=("line.slur",))] + [Note(4)] * 3)]))
    visited = set()
    for _ in range(3):
        window.goto_problem()
        visited.add((window.part, tuple(window.notes.selected_rows())))
    assert (1, (0,)) in visited


def test_a_note_across_the_bar_line_is_not_offered_rests(window):
    load(window, Score(notes=[Note(1, duration=2, dotted=True), Note(2, duration=2)]))
    assert "少" not in window.rhythm_label.text()
    assert not window.fill_button.isVisibleTo(window)
    before = window.project.to_dict()
    window.fix_problem()
    assert window.project.to_dict() == before
    assert "超出小節線" in window.statusBar().currentMessage()


def test_one_click_removes_a_far_glissando_completely(window):
    load(window, Score(notes=[Note(1, spans_start=("slide.gliss",)), Note(2), Note(3, spans_end=("slide.gliss",)),
                              Note(4)]))
    assert window.fill_button.isVisibleTo(window)
    window.fix_problem()
    assert window.project.score.notation_error() is None
    assert not any(n.spans_start or n.spans_end for n in window.part_notes())


# ---------------------------------------------------------------- P2 16
def test_cancelled_paste_does_not_report_success(window):
    load(window, Score(notes=[Note(1, bar_start=("bar.repeat_start",)), Note(2), Note(3),
                              Note(4, bar_end=("bar.repeat_end",))] + [Note(5)] * 4), position="after")
    before = window.project.to_dict()
    select(window, 0, 1, 2, 3)
    assert window.copy_notes()
    select(window, 0)
    window.statusBar().clearMessage()
    window.paste_notes()
    assert window.asked and window.project.to_dict() == before
    assert "已貼上" not in window.statusBar().currentMessage()
    QApplication.clipboard().clear()


# ---------------------------------------------------------------- P2 17
TRADITIONAL_ONLY = set("節後蓋這點裡個傳會連記")


def test_an_open_choice_follows_a_language_switch_completely(window):
    load(window, Score(notes=[Note(1), Note(0, duration=8), Note(2, duration=8), Note(3), Note(4)]))
    window.choose_rhythm_option = REAL_CHOOSE.__get__(window)
    window.show()
    select(window, 0)
    seen = {}

    def switch_and_answer():
        box = QApplication.activeModalWidget()
        if not isinstance(box, QMessageBox):
            box = next((w for w in QApplication.topLevelWidgets() if isinstance(w, QMessageBox) and w.isVisible()), None)
        if not isinstance(box, QMessageBox):
            seen["texts"] = ["no question open"]
            return
        window.change_language(i18n.SIMPLIFIED)
        seen["texts"] = [box.windowTitle(), box.text()] + [b.text() for b in box.buttons()]
        next(b for b in box.buttons() if "覆盖" in b.text()).click()
    QTimer.singleShot(50, switch_and_answer)
    window.edit_property("duration", 2)
    texts = "".join(seen["texts"])
    assert texts != "no question open" and not (TRADITIONAL_ONLY & set(texts)), seen["texts"]
    assert window.part_notes()[0].duration == 2


# ---------------------------------------------------------------- the guard everywhere
def test_typing_a_rest_over_a_fingered_note_names_the_mark_first(window):
    load(window, Score(notes=[Note(1, marks=("finger.1",)), Note(2), Note(3), Note(4)]))
    select(window, 0)
    before = window.project.to_dict()
    window.input_pitch(0)
    assert window.asked and "第 1 小節第 1 拍" in str(window.asked[0])
    assert window.project.to_dict() == before


def test_deleting_selected_notes_with_their_own_marks_asks_nothing(window):
    load(window, Score(notes=[Note(1, marks=("finger.1",), text="甲"), Note(2), Note(3), Note(4)]))
    select(window, 0)
    window.delete_note()
    assert not window.asked and window.part_notes()[0].degree == 0
