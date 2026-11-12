"""Workspace regressions driven by the same keys and clicks as score editing."""

import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox, QScrollArea

from jianpu_studio import app as app_module
from jianpu_studio.app import MainWindow
from jianpu_studio.model import Note, Project, Score, load_project

REAL_RENDER_SCORE = MainWindow.render_score
CTRL = Qt.KeyboardModifier.ControlModifier
SHIFT = Qt.KeyboardModifier.ShiftModifier


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(MainWindow, "render_score", lambda *a: None)
    monkeypatch.setattr(MainWindow, "error", lambda self, title, error: pytest.fail(f"{title}: {error}"))
    widget = MainWindow(recovery=False)
    widget.recovery_path = tmp_path / "recovery.jps"
    widget.autosave.stop()
    def before_close(w):
        w.dirty = False
        QApplication.clipboard().clear()
    qtbot.addWidget(widget, before_close_func=before_close)
    widget.reset(Project())
    widget.show()
    widget.activateWindow()
    widget.notes.setFocus()
    QApplication.processEvents()
    return widget


def set_notes(window, notes):
    window.reset(Project(Score(notes=list(notes))))
    window.notes.setFocus()
    QApplication.processEvents()


def click_note(qtbot, window, row, modifier=Qt.KeyboardModifier.NoModifier):
    view = window.notes
    index = view.model().index(row, 0)
    view.scrollTo(index)
    QApplication.processEvents()
    point = view.visualRect(index).center()
    assert view.viewport().rect().contains(point)
    qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, modifier, point)


def test_keyboard_insert_edit_duration_and_play(window, qtbot, monkeypatch):
    qtbot.keyClicks(window.notes, "1230")
    assert [note.degree for note in window.project.score.notes] == [1, 2, 3, 0]
    click_note(qtbot, window, 1)
    qtbot.keyClick(window.notes, Qt.Key.Key_Space)
    assert window.input_mode.currentData() == "edit"
    qtbot.keyClicks(window.notes, "7")
    assert [note.degree for note in window.project.score.notes] == [1, 7, 3, 0]
    for key, duration in zip("qweras", (1, 2, 4, 8, 16, 32)):
        qtbot.keyClicks(window.notes, key)
        assert window.project.score.notes[1].duration == duration
        assert window.project.score.notes[1].degree == 7
    calls = []
    monkeypatch.setattr(window, "play_midi", lambda: calls.append("play"))
    qtbot.keyClick(window.notes, Qt.Key.Key_Return)
    assert calls == ["play"]
    qtbot.keyClick(window.notes, Qt.Key.Key_Space)
    assert window.input_mode.currentData() == "insert"


@pytest.mark.parametrize("position, expected", [
    ("before", [1, 5, 2, 3]),
    ("after", [1, 2, 5, 3]),
    ("end", [1, 2, 3, 5]),
])
def test_insert_location(window, qtbot, position, expected):
    set_notes(window, [Note(1), Note(2), Note(3)])
    click_note(qtbot, window, 1)
    window.insert_position.setCurrentIndex(window.insert_position.findData(position))
    qtbot.keyClicks(window.notes, "5")
    assert [note.degree for note in window.project.score.notes] == expected


def test_ctrl_multiselect_changes_only_one_property(window, qtbot):
    original = [
        Note(1, octave=-1, duration=4, finger=0, bow="downbow"),
        Note(2, duration=8),
        Note(5, octave=1, duration=2, dotted=True, accidental="#", finger=3, dynamic="f"),
    ]
    set_notes(window, original)
    window.input_mode.setCurrentIndex(window.input_mode.findData("edit"))
    click_note(qtbot, window, 0)
    click_note(qtbot, window, 2, CTRL)
    assert window.notes.selected_rows() == [0, 2]
    window.duration.setCurrentIndex(window.duration.findData(16))
    assert window.project.score.notes == [replace(original[0], duration=16), original[1],
                                           replace(original[2], duration=16)]
    assert window.notes.selected_rows() == [0, 2]
    window.undo()
    assert window.project.score.notes == original
    assert window.notes.selected_rows() == [0, 2]


def test_shift_delete_undo_redo_restores_selection(window, qtbot):
    original = [Note(i % 7 + 1) for i in range(20)]
    set_notes(window, original)
    click_note(qtbot, window, 1)
    click_note(qtbot, window, 13, SHIFT)
    assert window.notes.selected_rows() == list(range(1, 14))
    current = window.notes.currentRow()
    qtbot.keyClick(window.notes, Qt.Key.Key_Delete)
    assert window.project.score.notes == original[:1] + original[14:]
    remaining_selection = window.notes.selected_rows()
    qtbot.keyClick(window.notes, Qt.Key.Key_Z, CTRL)
    assert window.project.score.notes == original
    assert window.notes.selected_rows() == list(range(1, 14))
    assert window.notes.currentRow() == current
    qtbot.keyClick(window.notes, Qt.Key.Key_Y, CTRL)
    assert window.project.score.notes == original[:1] + original[14:]
    assert window.notes.selected_rows() == remaining_selection


def test_copy_cut_paste_duplicate_preserves_notation(window, qtbot):
    original = [Note(1, finger=0), Note(2, duration=8, bow="upbow"),
                Note(3), Note(4, octave=1, dynamic="mp")]
    set_notes(window, original)
    click_note(qtbot, window, 1)
    click_note(qtbot, window, 3, CTRL)
    qtbot.keyClick(window.notes, Qt.Key.Key_C, CTRL)
    window.insert_position.setCurrentIndex(window.insert_position.findData("end"))
    qtbot.keyClick(window.notes, Qt.Key.Key_V, CTRL)
    assert window.project.score.notes == original + [original[1], original[3]]
    assert window.notes.selected_rows() == [4, 5]
    qtbot.keyClick(window.notes, Qt.Key.Key_X, CTRL)
    assert window.project.score.notes == original
    qtbot.keyClick(window.notes, Qt.Key.Key_V, CTRL)
    assert window.project.score.notes == original + [original[1], original[3]]
    qtbot.keyClick(window.notes, Qt.Key.Key_D, CTRL)
    assert window.project.score.notes == original + [original[1], original[3]] * 2
    assert window.notes.selected_rows() == [6, 7]


def test_double_click_selects_measure(window, qtbot):
    set_notes(window, [Note(1)] * 12)
    index = window.notes.model().index(5, 0)
    window.notes.scrollTo(index)
    QApplication.processEvents()
    qtbot.mouseDClick(window.notes.viewport(), Qt.MouseButton.LeftButton,
                     pos=window.notes.visualRect(index).center())
    assert window.notes.selected_rows() == [4, 5, 6, 7]


def test_fill_save_and_recovery_roundtrip(window, qtbot, tmp_path):
    qtbot.keyClicks(window.notes, "123")
    window.fill_bar()
    assert window.project.score.notes[-1].degree == 0
    assert window.project.score.rhythm_error() is None
    window.undo()
    assert len(window.project.score.notes) == 3
    window.redo()
    assert len(window.project.score.notes) == 4
    window.path = tmp_path / "中文 樂譜.jps"
    assert window.save()
    assert load_project(window.path) == window.project
    click_note(qtbot, window, 1)
    qtbot.keyClick(window.notes, Qt.Key.Key_Delete)
    window.write_recovery()
    assert load_project(window.recovery_path) == window.project


def test_source_edit_invalidates_preview_and_preserves_both_drafts(window, monkeypatch):
    original = [Note(1), Note(2), Note(3), Note(4)]
    set_notes(window, original)
    window.switch_mode()
    revision = window.revision
    window.result = SimpleNamespace()
    window.rendered_revision = revision
    window.source.insertPlainText("% retained source edit\n")
    source = window.project.source
    assert window.revision > revision
    assert window.fresh_result() is None
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.Yes)
    window.switch_mode()
    assert window.project.mode == "visual"
    assert window.project.score.notes == original
    assert window.project.source == source
    window.switch_mode()
    assert window.project.mode == "source"
    assert window.source.toPlainText() == source


def test_unsaved_new_cancel_failed_save_and_discard(window, qtbot, monkeypatch):
    qtbot.keyClicks(window.notes, "123")
    window.duration.setCurrentIndex(window.duration.findData(8))
    qtbot.keyClick(window.notes, Qt.Key.Key_Space)
    original = window.project.snapshot()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.Cancel)
    window.new_project()
    assert window.project == original
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.Save)
    monkeypatch.setattr(window, "save", lambda: False)
    window.new_project()
    assert window.project == original
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.Discard)
    window.new_project()
    assert window.project == Project()
    assert not window.dirty
    assert window.input_mode.currentData() == "insert"
    qtbot.keyClicks(window.notes, "1")
    assert window.project.score.notes == [Note(1)]


def test_cancel_close_with_autopreview_due_keeps_project_open(window, qtbot, monkeypatch):
    set_notes(window, [Note(1)] * 4)
    window.input_mode.setCurrentIndex(window.input_mode.findData("edit"))
    click_note(qtbot, window, 0)
    qtbot.keyClicks(window.notes, "7")
    tool_requests = []
    monkeypatch.setattr(window, "render_score", REAL_RENDER_SCORE.__get__(window, MainWindow))
    monkeypatch.setattr(window, "current_tool", lambda name: tool_requests.append(name))

    def cancel_dialog(*args, **kwargs):
        # A modal save question runs an event loop: the preview timer can fire
        # while the close decision is pending.
        window.autopreview.start(0)
        QApplication.processEvents()
        return QMessageBox.StandardButton.Cancel

    monkeypatch.setattr(QMessageBox, "question", cancel_dialog)
    assert not window.close()
    assert window.isVisible()
    assert window.project.score.notes[0].degree == 7
    assert window.dirty
    assert window.task is None
    assert tool_requests == []


def test_background_render_keeps_editor_live_and_discards_stale_result(window, qtbot, monkeypatch):
    set_notes(window, [Note(1)] * 4)
    started, release = threading.Event(), threading.Event()
    obsolete_result = SimpleNamespace(pdf=window.work / "obsolete-render" / "score.pdf")

    def render(*args, **kwargs):
        started.set()
        if not release.wait(5):
            raise RuntimeError("test did not release rendering worker")
        return obsolete_result

    monkeypatch.setattr(app_module, "render", render)
    monkeypatch.setattr(window, "current_tool", lambda name: "test-lilypond")
    monkeypatch.setattr(window, "render_score", REAL_RENDER_SCORE.__get__(window, MainWindow))
    window.auto_check.setChecked(False)
    window.render_score(True)
    try:
        qtbot.waitUntil(started.is_set)
        assert window.editor.isEnabled()
        click_note(qtbot, window, 1)
        qtbot.keyClick(window.notes, Qt.Key.Key_Space)
        qtbot.keyClicks(window.notes, "7")
        assert window.project.score.notes[1].degree == 7
        window.switch_mode()
        assert window.project.mode == "source"
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.Discard)
        window.new_project()
        assert window.project == Project()
    finally:
        release.set()
        qtbot.waitUntil(lambda: window.task is None)
    assert window.result is not obsolete_result
    assert window.fresh_result() is None


@pytest.mark.parametrize("size", [(1000, 630), (1380, 850)])
def test_workspace_controls_reachable_at_supported_sizes(window, size):
    window.resize(*size)
    QApplication.processEvents()
    assert (window.width(), window.height()) == size
    for control in (window.notes, window.pitch_buttons[0], window.duration,
                    window.staff, window.workspace_view, window.save_button):
        for scroll in window.findChildren(QScrollArea):
            if scroll.widget() and scroll.widget().isAncestorOf(control):
                scroll.ensureWidgetVisible(control)
        QApplication.processEvents()
        point = control.mapTo(window, control.rect().center())
        assert control.isVisible(), control.objectName()
        assert window.rect().contains(point), (control, point)
        target = window.childAt(point)
        assert target is control or control.isAncestorOf(target), control
    assert window.notes.viewport().height() >= 100
