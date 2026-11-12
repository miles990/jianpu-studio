from PySide6.QtCore import Qt

from jianpu_studio.app import MainWindow
from jianpu_studio.model import Project, load_project


def test_visual_edit_undo_save_and_keyboard(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(MainWindow, "render_score", lambda *a: None)
    window = MainWindow(recovery=False)
    window.recovery_path = tmp_path / "recovery.jps"
    qtbot.addWidget(window)
    window.reset(Project())
    window.show()
    window.notes.setFocus()
    for key in "123":
        qtbot.keyClicks(window.notes, key)
    assert [n.degree for n in window.project.score.notes] == [1, 2, 3]
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
    window.notes.setCurrentRow(1)
    qtbot.keyClick(window.notes, Qt.Key.Key_Delete)
    assert [n.degree for n in window.project.score.notes] == [1, 3, 0]
    window.write_recovery()
    assert load_project(window.recovery_path) == window.project
    window.dirty = False
    window.close()


def test_source_edit_invalidates_preview(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(MainWindow, "render_score", lambda *a: None)
    window = MainWindow(recovery=False)
    window.recovery_path = tmp_path / "recovery.jps"
    qtbot.addWidget(window)
    window.switch_mode()
    revision = window.revision
    window.source.insertPlainText("% comment\n")
    assert window.revision > revision
    assert window.project.mode == "source"
    assert "% comment" in window.project.source
    assert window.fresh_result() is None
    window.dirty = False
    window.close()
