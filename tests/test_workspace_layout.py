"""Visible editing tools remain clickable, synchronized, and safe at small sizes."""

from dataclasses import replace

import pytest
from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QAbstractButton, QApplication, QDialogButtonBox, QLineEdit, QTabWidget

from jianpu_studio.app import MainWindow
from jianpu_studio.model import Note, Project, Score


@pytest.fixture
def layout_window(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(MainWindow, "render_score", lambda *args, **kwargs: None)
    monkeypatch.setattr(MainWindow, "error", lambda self, title, error: pytest.fail(f"{title}: {error}"))
    window = MainWindow(recovery=False)
    window.recovery_path = tmp_path / "recovery.jps"
    window.autosave.stop()
    window.reset(Project(Score(notes=[Note(i % 7 + 1) for i in range(12)])))

    def before_close(widget):
        widget.dirty = False
        QApplication.clipboard().clear()

    qtbot.addWidget(window, before_close_func=before_close)
    window.show()
    window.activateWindow()
    QApplication.processEvents()
    return window


def assert_clickable(window, control):
    assert control.isVisible(), control.objectName()
    assert control.isEnabled(), control.objectName()
    point = control.mapTo(window, control.rect().center())
    assert window.rect().contains(point), (control.objectName(), point)
    target = window.childAt(point)
    assert target is control or control.isAncestorOf(target), control.objectName()


def click_note(qtbot, window, row, modifier=Qt.KeyboardModifier.NoModifier):
    index = window.notes.model().index(row, 0)
    window.notes.scrollTo(index)
    QApplication.processEvents()
    point = window.notes.visualRect(index).center()
    assert window.notes.viewport().rect().contains(point)
    qtbot.mouseClick(window.notes.viewport(), Qt.MouseButton.LeftButton, modifier, point)


def assert_mode(window, expected):
    assert window.input_mode.currentData() == expected
    buttons = {"insert": window.insert_mode_button, "edit": window.edit_mode_button,
               "fill": window.fill_mode_button}
    assert [name for name, button in buttons.items() if button.isChecked()] == [expected]


def test_score_settings_are_visible_and_editable_without_opening_a_panel(layout_window, qtbot):
    window = layout_window
    assert window.score_settings.isVisible()
    for control in (window.title_field, window.composer_field, window.key_field,
                    window.meter_field, window.tempo_field):
        assert_clickable(window, control)
    qtbot.mouseClick(window.title_field, Qt.MouseButton.LeftButton)
    window.title_field.selectAll()
    qtbot.keyClicks(window.title_field, "Visible settings")
    qtbot.keyClick(window.title_field, Qt.Key.Key_Tab)
    assert window.project.score.title == "Visible settings"
    assert window.score_settings.isVisible()
    assert not any(button.isVisible() and button.text() == "樂曲設定"
                   for button in window.findChildren(QAbstractButton))


def test_source_and_photo_features_have_no_visible_entry(layout_window):
    window = layout_window
    forbidden = ("進階語法", "原始碼", "返回視覺草稿", "照片", "Audiveris", "辨識結果")
    visible_buttons = [button.text() for button in window.findChildren(QAbstractButton) if button.isVisible()]
    action_texts = [action.text() for action in window.findChildren(QAction) if action.isVisible()]
    assert not any(token in text for text in visible_buttons + action_texts for token in forbidden)
    assert not hasattr(window, "source")
    assert window.project.mode == "visual"
    assert window.notes.isVisible()


def test_note_mode_buttons_and_space_key_stay_in_sync(layout_window, qtbot):
    window = layout_window
    assert not window.input_mode.isVisible()
    for button in (window.insert_mode_button, window.edit_mode_button, window.fill_mode_button):
        assert window.note_tools.isAncestorOf(button)
        assert_clickable(window, button)
    for control in window.measure_controls:
        assert window.measure_tools.isAncestorOf(control)
        assert not window.note_tools.isAncestorOf(control)
    click_note(qtbot, window, 0)
    qtbot.mouseClick(window.edit_mode_button, Qt.MouseButton.LeftButton)
    assert_mode(window, "edit")
    assert window.notes.hasFocus(), "mode buttons must return keyboard input to the score"
    qtbot.keyClick(QApplication.focusWidget(), Qt.Key.Key_Space)
    assert_mode(window, "insert")
    qtbot.keyClick(QApplication.focusWidget(), Qt.Key.Key_Space)
    assert_mode(window, "edit")
    qtbot.mouseClick(window.fill_mode_button, Qt.MouseButton.LeftButton)
    assert_mode(window, "fill")
    assert window.notes.hasFocus()
    qtbot.keyClick(QApplication.focusWidget(), Qt.Key.Key_Space)
    assert_mode(window, "edit")


def test_add_measure_button_selects_fill_mode_and_preserves_later_music(layout_window, qtbot):
    window = layout_window
    original = list(window.project.score.notes)
    click_note(qtbot, window, 0)
    qtbot.mouseClick(window.edit_mode_button, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(window.add_bar_button, Qt.MouseButton.LeftButton)
    assert_mode(window, "fill")
    assert window.notes.selected_rows() == [4]
    assert window.project.score.notes[4].degree == 0
    window.duration.setCurrentIndex(window.duration.findData(4))
    qtbot.mouseClick(window.pitch_buttons[4], Qt.MouseButton.LeftButton)
    notes = window.project.score.notes
    assert notes[:4] == original[:4]
    assert notes[4] == Note(5)
    assert notes[-8:] == original[4:]
    assert sum(note.beats for note in notes[:-8]) == 8
    assert sum(note.beats for note in notes) == 16
    assert_mode(window, "fill")


def test_mode_switch_preserves_selection_and_next_note_inspector(layout_window, qtbot):
    window = layout_window
    original = Note(6, octave=-1, duration=2, finger=2, dynamic="p")
    window.reset(Project(Score(notes=[original, Note(3), Note(4)])))
    click_note(qtbot, window, 0)
    qtbot.mouseClick(window.edit_mode_button, Qt.MouseButton.LeftButton)
    assert window.degree.currentData() == 6
    assert window.duration.currentData() == 2
    assert window.octave.currentData() == -1
    qtbot.mouseClick(window.insert_mode_button, Qt.MouseButton.LeftButton)
    window.duration.setCurrentIndex(window.duration.findData(8))
    window.octave.setCurrentIndex(window.octave.findData(1))
    assert window.project.score.notes[0] == original
    qtbot.mouseClick(window.edit_mode_button, Qt.MouseButton.LeftButton)
    assert window.notes.selected_rows() == [0]
    assert window.duration.currentData() == 2
    assert window.octave.currentData() == -1
    window.degree.setCurrentIndex(window.degree.findData(5))
    assert window.project.score.notes[0] == replace(original, degree=5)
    qtbot.mouseClick(window.fill_mode_button, Qt.MouseButton.LeftButton)
    assert window.duration.currentData() == 8
    assert window.octave.currentData() == 1
    qtbot.mouseClick(window.edit_mode_button, Qt.MouseButton.LeftButton)
    click_note(qtbot, window, 0, Qt.KeyboardModifier.ControlModifier)
    assert window.notes.selected_rows() == []
    assert not window.duration.isEnabled()
    assert window.duration.placeholderText() == "未選取"
    assert not window.dotted.isEnabled() and not window.dotted.isChecked()


def click_palette_symbol(qtbot, window, symbol_id):
    """Click the palette cell of ``symbol_id`` like a user (scrolling the grid to it first)."""
    grid = window.palette.grid
    entry = next(e for e in grid.entries if e.kind == "cell" and e.sid == symbol_id)
    grid.verticalScrollBar().setValue(int(entry.rect.top()) - 10)
    QApplication.processEvents()
    point = entry.rect.center().toPoint() - QPoint(0, grid.verticalScrollBar().value())
    assert grid.viewport().rect().contains(point)
    qtbot.mouseClick(grid.viewport(), Qt.MouseButton.LeftButton, pos=point)


@pytest.mark.parametrize("symbol_id, field, value", [
    ("dyn.mf", "dynamic", "mf"),
    ("line.slur", "slur", "add"),
    ("finger.2", "finger", 2),
])
def test_palette_cells_apply_directly_to_the_selection(layout_window, qtbot, symbol_id, field, value):
    """0.3.x opened a dialog per group; 0.4.0 applies a palette cell in one click (same intent)."""
    window = layout_window
    qtbot.mouseClick(window.edit_mode_button, Qt.MouseButton.LeftButton)
    window.notes.select_rows([0, 1])
    before = window.project.snapshot()
    click_palette_symbol(qtbot, window, symbol_id)
    notes = window.project.score.notes
    if field == "slur":
        assert notes[0].slur_start and notes[1].slur_end
    else:
        assert [getattr(note, field) for note in notes[:2]] == [value, value]
    assert notes[2:] == before.score.notes[2:]
    assert sum(note.beats for note in notes) == sum(note.beats for note in before.score.notes)
    window.undo()
    assert window.project == before


def test_grace_dialog_opens_from_the_inspector_and_applies(layout_window, qtbot):
    window = layout_window
    qtbot.mouseClick(window.edit_mode_button, Qt.MouseButton.LeftButton)
    click_note(qtbot, window, 0)
    before = window.project.snapshot()

    def fill():
        dialog = QApplication.activeModalWidget()
        try:
            assert dialog.objectName() == "notation_dialog"
            assert dialog.findChild(QTabWidget).currentIndex() == 2
            dialog.findChild(QLineEdit, "notation_grace_before").setText("23")
            qtbot.mouseClick(dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok),
                             Qt.MouseButton.LeftButton)
        except Exception:
            dialog.reject()
            raise

    QTimer.singleShot(0, fill)
    qtbot.mouseClick(window.inspector.grace_button, Qt.MouseButton.LeftButton)
    assert window.project.score.notes[0].grace_before == "23"
    window.undo()
    assert window.project == before


def test_direct_triplet_button_preserves_time_and_selects_the_group(layout_window, qtbot):
    window = layout_window
    click_note(qtbot, window, 0)
    before = window.project.snapshot()
    qtbot.mouseClick(window.triplet_button, Qt.MouseButton.LeftButton)
    notes = window.project.score.notes
    assert [note.tuplet for note in notes[:3]] == [3, 3, 3]
    assert sum(note.beats for note in notes[:3]) == before.score.notes[0].beats
    assert notes[3:] == before.score.notes[1:]
    assert window.notes.selected_rows() == [0, 1, 2]
    assert_mode(window, "edit")
    window.undo()
    assert window.project == before


@pytest.mark.parametrize("size", [(1000, 630), (1380, 850)])
def test_grouped_tools_and_first_measure_are_visible_without_scrolling(layout_window, size):
    """0.4.0 layout: settings row, note bar and palette replace the 0.3.x side panel.

    Controls that the design collapses in small windows (source field, accidentals,
    triplet, 小節 menu) must then be reachable through the visible 「⋯」/「更多」 buttons.
    """
    window = layout_window
    window.resize(*size)
    QApplication.processEvents()
    assert (window.width(), window.height()) == size
    wide = size[0] > 1300
    controls = [window.title_field, window.key_field, window.meter_field, window.tempo_field,
                window.pickup_field, window.insert_mode_button, window.edit_mode_button, window.fill_mode_button,
                window.duration_buttons[4], window.octave_up_button, window.pitch_buttons[0],
                window.palette.search, window.palette.grid, window.save_button, window.version_label]
    controls += ([window.composer_field, window.accidental_buttons["#"], window.add_bar_button, window.triplet_button]
                 if wide else [window.more_button, window.note_more_button])
    for control in controls:
        assert_clickable(window, control)
    assert window.notes.verticalScrollBar().value() == 0
    first_note = window.notes.visualRect(window.notes.model().index(0, 0))
    assert window.notes.viewport().rect().contains(first_note), "the entire first note must be visible initially"
    point = window.notes.viewport().mapTo(window, first_note.center())
    assert window.childAt(point) is window.notes.viewport()
