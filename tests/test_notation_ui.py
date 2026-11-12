"""Notation editing preserves musical time, complete groups, and saved data."""

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QComboBox, QDialogButtonBox, QLineEdit

from jianpu_studio import __version__
from jianpu_studio.app import MainWindow
from jianpu_studio.model import Note, Project, Score, load_project, tuplet_ranges

CTRL = Qt.KeyboardModifier.ControlModifier


@pytest.fixture
def notation_window(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(MainWindow, "render_score", lambda *args, **kwargs: None)
    monkeypatch.setattr(MainWindow, "error", lambda self, title, error: pytest.fail(f"{title}: {error}"))
    window = MainWindow(recovery=False)
    window.recovery_path = tmp_path / "recovery.jps"
    window.autosave.stop()

    def before_close(widget):
        widget.dirty = False
        QApplication.clipboard().clear()

    qtbot.addWidget(window, before_close_func=before_close)
    window.show()
    window.activateWindow()
    window.notes.setFocus()
    return window


def load_notes(window, notes):
    window.reset(Project(Score(notes=list(notes))))
    window.input_mode.setCurrentIndex(window.input_mode.findData("edit"))
    window.notes.setFocus()
    QApplication.processEvents()


def click_note(qtbot, window, row):
    view = window.notes
    index = view.model().index(row, 0)
    view.scrollTo(index)
    QApplication.processEvents()
    qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=view.visualRect(index).center())


def edit_dialog(qtbot, window, values):
    def accept():
        dialog = QApplication.activeModalWidget()
        try:
            assert dialog.objectName() == "notation_dialog"
            for name, value in values.items():
                if control := dialog.findChild(QComboBox, "notation_" + name):
                    index = control.findData(value)
                    assert index >= 0, (name, value)
                    control.setCurrentIndex(index)
                else:
                    dialog.findChild(QLineEdit, "notation_" + name).setText(value)
            buttons = dialog.findChild(QDialogButtonBox)
            qtbot.mouseClick(buttons.button(QDialogButtonBox.StandardButton.Ok), Qt.MouseButton.LeftButton)
        except Exception:
            dialog.reject()
            raise
    QTimer.singleShot(0, accept)
    window.notation_settings()


@pytest.mark.parametrize("duration", [2, 4, 8, 16])
def test_split_triplet_preserves_original_span_and_following_music(notation_window, qtbot, duration):
    window = notation_window
    score = Score(notes=[Note(3, duration=duration, finger=1, bow="upbow")])
    score.fill_last_bar()
    score.notes += [Note(6, finger=3)] * 4
    load_notes(window, score.notes)
    before = window.project.snapshot()
    original_beats = sum(note.beats for note in before.score.notes)
    click_note(qtbot, window, 0)
    menu = window.notation_menu()
    next(action for action in menu.actions() if action.text().startswith("拆成三連音")).trigger()
    notes = window.project.score.notes
    assert [note.degree for note in notes[:3]] == [3, 3, 3]
    assert [note.duration for note in notes[:3]] == [duration * 2] * 3
    assert tuplet_ranges(notes) == [(0, 3)]
    assert sum(note.beats for note in notes[:3]) == before.score.notes[0].beats
    assert notes[3:] == before.score.notes[1:]
    assert sum(note.beats for note in notes) == original_beats
    assert window.bar_jump.maximum() == 2
    assert len(window.notes.score_model.bars) == 2
    assert notes[0].finger == 1 and notes[1].finger == notes[2].finger == -1
    assert window.input_mode.currentData() == "edit"
    assert window.notes.selected_rows() == [0, 1, 2]
    after = window.project.snapshot()
    window.undo()
    assert window.project == before
    assert window.notes.selected_rows() == [0]
    window.redo()
    assert window.project == after
    assert window.notes.selected_rows() == [0, 1, 2]


def test_collapse_preserves_outer_marks_and_rejects_inner_mark_loss(notation_window, qtbot, monkeypatch):
    window = notation_window
    original = [Note(1, finger=0, dynamic="p", slur_start=True, tie=True,
                     repeat_start=True, hairpin="crescendo", grace_before="23"),
                Note(1), Note(2), Note(3, slur_end=True, repeat_end=True, hairpin_end=True)]
    load_notes(window, original + [Note(5)] * 4)
    before = window.project.snapshot()
    click_note(qtbot, window, 0)
    window.triplet_action()
    split = window.project.snapshot()
    assert split.score.notes[0].slur_start and split.score.notes[0].hairpin == "crescendo"
    assert split.score.notes[2].tie
    click_note(qtbot, window, 1)
    window.triplet_action(collapse=True)
    assert window.project == before
    window.undo()
    assert window.project == split
    assert window.notes.selected_rows() == [1]
    window.redo()
    assert window.project == before
    window.undo()
    errors = []
    monkeypatch.setattr(window, "error", lambda title, error: errors.append(str(error)))
    click_note(qtbot, window, 1)
    window.apply_notation_changes({"articulation": "fermata"})
    marked, history_count = window.project.snapshot(), len(window.history)
    window.triplet_action(collapse=True)
    assert window.project == marked
    assert len(window.history) == history_count
    assert errors
    window.apply_notation_changes({"articulation": ""})
    window.triplet_action(collapse=True)
    assert window.project == before


def test_triplet_preserves_last_after_grace_and_rejects_losing_first_after_grace(notation_window, qtbot, monkeypatch):
    window = notation_window
    load_notes(window, [Note(1, grace_after="45"), Note(2), Note(3), Note(4)])
    before = window.project.snapshot()
    click_note(qtbot, window, 0)
    window.triplet_action()
    assert [note.grace_after for note in window.project.score.notes[:3]] == ["", "", "45"]
    window.triplet_action(collapse=True)
    assert window.project == before
    window.undo()
    click_note(qtbot, window, 0)
    window.apply_notation_changes({"grace_after": "23"})
    marked, history_count = window.project.snapshot(), len(window.history)
    errors = []
    monkeypatch.setattr(window, "error", lambda title, error: errors.append(str(error)))
    window.triplet_action(collapse=True)
    assert window.project == marked
    assert len(window.history) == history_count
    assert len(errors) == 1 and "後倚音" in errors[0]
    window.apply_notation_changes({"grace_after": ""})
    window.triplet_action(collapse=True)
    assert window.project == before


def test_partial_triplet_operations_reject_without_data_or_inspector_drift(notation_window, qtbot, monkeypatch):
    window = notation_window
    load_notes(window, [Note(1)] * 4)
    click_note(qtbot, window, 0)
    window.triplet_action()
    click_note(qtbot, window, 1)
    before, history_count = window.project.snapshot(), len(window.history)
    errors = []
    monkeypatch.setattr(window, "error", lambda title, error: errors.append(str(error)))
    window.duration.setCurrentIndex(window.duration.findData(16))
    assert window.project == before
    assert window.duration.currentData() == 8
    window.dotted.setChecked(True)
    assert window.project == before
    assert not window.dotted.isChecked()
    QApplication.clipboard().setText("keep clipboard")
    for key, modifier in ((Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier),
                          (Qt.Key.Key_C, CTRL), (Qt.Key.Key_X, CTRL), (Qt.Key.Key_D, CTRL)):
        qtbot.keyClick(window.notes, key, modifier)
        assert window.project == before
        assert window.notes.selected_rows() == [1]
        assert len(window.history) == history_count
    assert QApplication.clipboard().text() == "keep clipboard"
    assert len(errors) == 6


def test_whole_triplet_copy_paste_cut_and_undo_keep_complete_groups(notation_window, qtbot):
    window = notation_window
    load_notes(window, [Note(1)] * 4)
    click_note(qtbot, window, 0)
    window.triplet_action()
    original = list(window.project.score.notes)
    qtbot.keyClick(window.notes, Qt.Key.Key_C, CTRL)
    window.insert_position.setCurrentIndex(window.insert_position.findData("end"))
    qtbot.keyClick(window.notes, Qt.Key.Key_V, CTRL)
    assert window.project.score.notes == original + original[:3]
    assert tuplet_ranges(window.project.score.notes) == [(0, 3), (len(original), len(original) + 3)]
    pasted_rows = list(range(len(original), len(original) + 3))
    assert window.notes.selected_rows() == pasted_rows
    qtbot.keyClick(window.notes, Qt.Key.Key_X, CTRL)
    assert window.project.score.notes == original
    window.undo()
    assert window.project.score.notes == original + original[:3]
    assert window.notes.selected_rows() == pasted_rows
    window.redo()
    assert window.project.score.notes == original


def test_range_marks_and_fermata_remove_both_ends_without_changing_beats(notation_window, qtbot):
    window = notation_window
    load_notes(window, [Note(1)] * 8)
    window.notes.select_rows([0, 1, 2, 3])
    edit_dialog(qtbot, window, {"slur": "add", "tie": "add", "hairpin": "crescendo"})
    click_note(qtbot, window, 3)
    window.quick_notation({"articulation": "fermata"})
    marked = window.project.snapshot()
    notes = marked.score.notes
    assert notes[0].slur_start and notes[3].slur_end
    assert notes[0].hairpin == "crescendo" and notes[3].hairpin_end
    assert [note.tie for note in notes[:4]] == [True, True, True, False]
    assert notes[3].articulation == "fermata"
    assert sum(note.beats for note in notes) == 8
    source = marked.notation()
    assert all(token in source for token in (r"\<", r"\!", r"\fermata", "~"))
    click_note(qtbot, window, 1)
    edit_dialog(qtbot, window, {"slur": "remove", "tie": "remove", "hairpin": "remove"})
    notes = window.project.score.notes
    assert not any(note.slur_start or note.slur_end or note.hairpin or note.hairpin_end for note in notes)
    assert [note.tie for note in notes[:4]] == [False, False, True, False]
    assert notes[3].articulation == "fermata"
    assert window.project.score.notation_error() is None
    window.undo()
    assert window.project == marked


def test_adjacent_slurs_and_hairpins_share_endpoint_without_erasing_previous_phrase(notation_window):
    window = notation_window
    load_notes(window, [Note(1)] * 8)
    window.notes.select_rows([0, 1, 2])
    window.apply_notation_changes({}, {"slur": "add", "hairpin": "crescendo"})
    first_phrase = window.project.snapshot()
    window.notes.select_rows([2, 3, 4])
    window.apply_notation_changes({}, {"slur": "add", "hairpin": "diminuendo"})
    notes = window.project.score.notes
    assert notes[0].slur_start and notes[0].hairpin == "crescendo"
    assert notes[2].slur_end and notes[2].slur_start
    assert notes[2].hairpin_end and notes[2].hairpin == "diminuendo"
    assert notes[4].slur_end and notes[4].hairpin_end
    assert window.project.score.notation_error() is None
    second_phrase = window.project.snapshot()
    window.undo()
    assert window.project == first_phrase
    window.redo()
    assert window.project == second_phrase
    window.notes.select_rows([3])
    window.apply_notation_changes({}, {"slur": "remove", "hairpin": "remove"})
    assert window.project == first_phrase
    window.notes.select_rows([2])
    window.apply_notation_changes({}, {"slur": "remove", "hairpin": "remove"})
    assert not any(note.slur_start or note.slur_end or note.hairpin or note.hairpin_end
                   for note in window.project.score.notes)


def test_grace_natural_triplet_and_new_marks_survive_save_and_reopen(notation_window, qtbot, tmp_path):
    window = notation_window
    load_notes(window, [Note(1, accidental="#"), Note(1), Note(3), Note(4)])
    click_note(qtbot, window, 1)
    edit_dialog(qtbot, window, {"dynamic": "fff", "articulation": "breathe",
                               "grace_before": "#4'5"})
    window.accidental.setCurrentIndex(window.accidental.findData("n"))
    note = window.project.score.notes[1]
    assert (note.dynamic, note.articulation, note.accidental) == ("fff", "breathe", "n")
    assert (note.grace_before, note.grace_after) == ("#4'5", "")
    click_note(qtbot, window, 2)
    edit_dialog(qtbot, window, {"grace_after": "23", "articulation": "breathe"})
    assert window.project.score.notes[2].grace_after == "23"
    assert window.project.score.notes[2].articulation == "breathe"
    assert sum(note.beats for note in window.project.score.notes) == 4
    click_note(qtbot, window, 0)
    window.triplet_action()
    expected = window.project.snapshot()
    window.undo()
    window.redo()
    assert window.project == expected
    window.path = tmp_path / "三連音 倚音與記號.jps"
    assert window.save()
    assert load_project(window.path) == expected
    path = window.path
    window.new_project()
    window.open_file(path=path)
    assert window.project == expected
    assert window.project.notation() == expected.notation()
    assert tuplet_ranges(window.project.score.notes) == [(0, 3)]


def test_unsupported_after_grace_combinations_are_rejected_atomically(notation_window, monkeypatch):
    window = notation_window
    load_notes(window, [Note(1, grace_after="23"), Note(1), Note(3), Note(4)])
    errors = []
    monkeypatch.setattr(window, "error", lambda title, error: errors.append(str(error)))
    before, history_count = window.project.snapshot(), len(window.history)
    for changes, ranges in (({"dynamic": "f"}, {}), ({"articulation": "fermata"}, {}),
                            ({"finger": 2}, {}), ({}, {"slur": "add"}),
                            ({}, {"hairpin": "crescendo"}), ({}, {"tie": "add"})):
        window.notes.select_rows([0, 1] if ranges else [0])
        window.apply_notation_changes(changes, ranges)
        assert window.project == before
        assert len(window.history) == history_count
    assert len(errors) == 6
    assert all("後倚音" in error for error in errors)
    window.notes.select_rows([0])
    window.apply_notation_changes({"articulation": "breathe"})
    assert window.project.score.notes[0].articulation == "breathe"
    window.project.score.validate()
    window.undo()
    assert window.project == before


def test_unsupported_triplet_requests_are_atomic(notation_window, monkeypatch):
    window = notation_window
    errors = []
    monkeypatch.setattr(window, "error", lambda title, error: errors.append(str(error)))
    cases = [([Note(1)] * 4, [0, 1, 2]),
             ([Note(1, dotted=True), Note(2, duration=8), Note(3), Note(4)], [0]),
             ([Note(1, duration=1)], [0])]
    for notes, rows in cases:
        load_notes(window, notes)
        window.notes.select_rows(rows)
        before, history_count = window.project.snapshot(), len(window.history)
        window.triplet_action()
        assert window.project == before
        assert len(window.history) == history_count
    assert len(errors) == 3


def test_source_mode_cannot_change_hidden_visual_notation(notation_window):
    window = notation_window
    load_notes(window, [Note(1)] * 4)
    window.notes.select_rows([0])
    window.project.mode = "source"  # Defensive backend guard; no source editor is exposed.
    before, history_count = window.project.snapshot(), len(window.history)
    window.triplet_action()
    window.quick_notation({"articulation": "fermata"})
    window.apply_notation_changes({"grace_before": "23"}, {"hairpin": "crescendo"})
    assert window.project == before
    assert len(window.history) == history_count


@pytest.mark.parametrize("size", [(1000, 630), (1380, 850)])
def test_current_version_visible_and_photo_ui_absent(notation_window, size):
    window = notation_window
    window.resize(*size)
    QApplication.processEvents()
    label = window.version_label
    assert __version__ in label.text()
    assert __version__ in window.windowTitle()
    assert label.isVisible()
    assert window.childAt(label.mapTo(window, label.rect().center())) is label
    assert not window.workspace_view.isVisible()
    assert window.workspace_view.count() == 1
    assert window.workspace_view.currentData() == "score"
    assert not window.photo_scroll.isVisible()
    assert not window.preview_tabs.isTabVisible(1)
    actions = [action.text() for action in window.findChildren(QAction)]
    assert not any(token in text for text in actions for token in ("照片", "Audiveris", "辨識結果"))
