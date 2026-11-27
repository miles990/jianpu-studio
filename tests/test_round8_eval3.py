"""Third evaluation (eval3) fixes in the editor window.

1. ↑ ↓ . ♯ ♭ ♮ act on the selected note in every mode; with nothing to act on they prepare the
   next note, visibly, for one note only.
3. A pickup set on written music asks before the whole piece is re-barred.
5. A Coda sign next to the automatic "⊕ Coda" is refused (and never drawn twice).
6. The refusal toast wraps instead of being cut off.
7. 「新建」 and the start page's 「空白樂譜」 give the same score.
"""
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from jianpu_studio import storage
from jianpu_studio.app import MainWindow, demo_project
from jianpu_studio.model import Note, Project, Score


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(MainWindow, "render_score", lambda *a, **k: None)
    errors = []
    monkeypatch.setattr(MainWindow, "error", lambda self, title, error: errors.append((title, str(error))))
    storage.open_settings().remove("palette")
    widget = MainWindow(recovery=False)
    widget.errors = errors
    widget.recovery_path = tmp_path / "recovery.jps"
    widget.autosave.stop()
    qtbot.addWidget(widget, before_close_func=lambda w: setattr(w, "dirty", False))
    widget.resize(1280, 800)
    widget.show()
    QApplication.processEvents()
    yield widget
    QApplication.clipboard().clear()


def mode(window, name):
    window.input_mode.setCurrentIndex(window.input_mode.findData(name))


def octaves(window):
    return [n.octave for n in window.project.score.notes]


# ---------------------------------------------------------------- 1. modifiers act on the selection
@pytest.mark.parametrize("name", ["insert", "fill", "edit"])
def test_octave_keys_and_buttons_change_the_selected_note_in_every_mode(window, qtbot, name):
    window.reset(Project(Score(notes=[Note(1), Note(2), Note(3), Note(4)])))
    mode(window, name)
    window.notes.select_rows([1], 1)
    qtbot.keyClick(window.notes, Qt.Key.Key_Up)
    assert octaves(window) == [0, 1, 0, 0]
    qtbot.mouseClick(window.octave_down_button, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(window.octave_down_button, Qt.MouseButton.LeftButton)
    assert octaves(window) == [0, -1, 0, 0]
    assert window.next_note.octave == 0                      # nothing was left for later notes
    window.undo()
    assert octaves(window) == [0, 0, 0, 0]                   # each press is one undo step


@pytest.mark.parametrize("name", ["insert", "fill", "edit"])
def test_accidental_and_dot_change_the_selected_note_in_every_mode(window, qtbot, name):
    window.reset(Project(Score(notes=[Note(1), Note(2), Note(0, duration=2)])))
    mode(window, name)
    window.notes.select_rows([1], 1)
    qtbot.mouseClick(window.accidental_buttons["#"], Qt.MouseButton.LeftButton)
    assert [n.accidental for n in window.project.score.notes] == ["", "#", ""]
    assert window.accidental_buttons["#"].isChecked()        # the button shows the selected note
    qtbot.keyClick(window.notes, Qt.Key.Key_Period)
    notes = window.project.score.notes
    assert notes[1].dotted and notes[1].degree == 2 and sum(n.ticks for n in notes) == 4 * 48
    assert window.dot_button.isChecked()
    assert not window.next_note.dotted and not window.next_note.accidental


def test_up_after_typing_raises_the_note_just_typed_not_the_following_ones(window, qtbot):
    """The evaluator's trap: 填寫, type 5 3, press ↑, go on typing."""
    window.new_from_template("blank")
    for key in "53":
        qtbot.keyClick(window.notes, key)
    qtbot.keyClick(window.notes, Qt.Key.Key_Up)
    assert octaves(window)[:2] == [0, 1]
    qtbot.keyClick(window.notes, Qt.Key.Key_Up)              # again: the same note, two octaves
    assert octaves(window)[:2] == [0, 2]
    for key in "12":
        qtbot.keyClick(window.notes, key)
    assert [(n.degree, n.octave) for n in window.project.score.notes[:4]] == [(5, 0), (3, 2), (1, 0), (2, 0)]


def test_a_prepared_octave_is_shown_and_used_by_one_note_only(window, qtbot):
    window.new_from_template("blank")                        # the caret is on a rest, nothing typed yet
    qtbot.keyClick(window.notes, Qt.Key.Key_Up)
    assert window.next_note.octave == 1
    assert window.octave_up_button.isChecked() and not window.octave_down_button.isChecked()
    assert "高八度" in window.selection_label.text() and "一個音" in window.selection_label.text()
    for key in "65":
        qtbot.keyClick(window.notes, key)
    assert [(n.degree, n.octave) for n in window.project.score.notes[:2]] == [(6, 1), (5, 0)]
    assert window.next_note.octave == 0 and not window.octave_up_button.isChecked()
    assert "高八度" not in window.selection_label.text()


def test_a_prepared_accidental_is_used_by_one_note_only(window, qtbot):
    window.new_from_template("blank")
    qtbot.mouseClick(window.accidental_buttons["b"], Qt.MouseButton.LeftButton)
    assert window.accidental_buttons["b"].isChecked() and "♭" in window.selection_label.text()
    for key in "76":
        qtbot.keyClick(window.notes, key)
    assert [(n.degree, n.accidental) for n in window.project.score.notes[:2]] == [(7, "b"), (6, "")]
    assert not window.accidental_buttons["b"].isChecked()


def test_modifiers_on_a_selected_rest_in_insert_mode_never_touch_other_notes(window, qtbot):
    window.reset(Project(Score(notes=[Note(1), Note(0), Note(3), Note(4)])))
    mode(window, "insert")
    window.notes.select_rows([1], 1)
    qtbot.keyClick(window.notes, Qt.Key.Key_Up)              # a rest has no octave: the next note's
    assert octaves(window) == [0, 0, 0, 0] and window.next_note.octave == 1
    assert window.octave_up_button.isChecked()


# ---------------------------------------------------------------- 7. 新建 = 空白樂譜
def test_new_and_the_blank_template_give_the_same_score(window, qtbot):
    window.new_from_template("blank")
    template = (window.project.snapshot(), window.input_mode.currentData(), window.notes.selected_rows())
    score = window.project.score
    assert (score.key, score.meter, score.tempo, len(score.part_bars(0))) == ("D", "4/4", 72, 8)
    assert all(n.degree == 0 for n in score.notes) and template[1] == "fill"

    window.reset(demo_project())
    qtbot.mouseClick(window.new_button, Qt.MouseButton.LeftButton)        # toolbar 「新建」
    assert (window.project, window.input_mode.currentData(), window.notes.selected_rows()) == template
    assert not window.dirty

    window.reset(demo_project())
    window.new_project()                                                  # File → 新建 / Ctrl+N
    assert (window.project, window.input_mode.currentData(), window.notes.selected_rows()) == template
    qtbot.keyClick(window.notes, "5")                                     # typing fills the first bar
    assert window.project.score.notes[0] == Note(5) and len(window.project.score.part_bars(0)) == 8


# ---------------------------------------------------------------- 6. the refusal toast wraps
def toast_shows_all_of_its_text(window):
    """The toast lies inside the canvas, and its label is tall enough for every wrapped line."""
    from PySide6.QtCore import QRect
    toast, label = window.toast, window.toast.text
    assert toast.isVisible() and label.wordWrap()
    assert window.notes.rect().contains(toast.geometry()), (toast.geometry(), window.notes.rect())
    assert toast.rect().contains(label.geometry())
    needed = label.fontMetrics().boundingRect(QRect(0, 0, label.width(), 100000),
                                              int(Qt.TextFlag.TextWordWrap), label.text())
    return needed.height() <= label.height() and needed.width() <= label.width()


@pytest.mark.parametrize("size", [(960, 600), (1280, 800)])
def test_a_long_refusal_is_wrapped_inside_the_window_with_its_suggestion_readable(window, qtbot, size):
    window.resize(*size)
    window.reset(demo_project())
    mode(window, "insert")
    window.notes.select_rows([9], 9)                     # a note in a full bar: inserting is refused
    before = window.project.snapshot()
    qtbot.keyClick(window.notes, "7")
    assert window.project == before
    text = window.toast.text.text()
    assert text.startswith("⚠") and text.rstrip("。").endswith("改用「填寫」")       # the fix is the last clause
    assert toast_shows_all_of_its_text(window)
    if size[0] == 960:
        assert window.toast.text.height() > 1.5 * window.toast.text.fontMetrics().height()    # really wrapped


def test_a_short_notice_with_undo_stays_one_line(window):
    window.resize(960, 600)
    window.reset(demo_project())
    window.toast.show_text("已加上「一指」。", window.undo)
    QApplication.processEvents()
    assert toast_shows_all_of_its_text(window) and window.toast.undo.isVisible()
    assert window.toast.text.height() < 1.5 * window.toast.text.fontMetrics().height()
    assert window.toast.rect().contains(window.toast.undo.geometry())
