import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from jianpu_studio import app as app_module
from jianpu_studio import update_dialog, updates
from jianpu_studio.engine import Cancelled


def wait_for_cancel(cancel):
    cancel.wait(5)
    raise Cancelled("Test preview cancelled")


@pytest.fixture
def ready_update(qtbot, tmp_path, monkeypatch):
    original_render = app_module.MainWindow.render_score
    initial_render = []
    monkeypatch.setattr(app_module.MainWindow, "render_score", lambda *args: initial_render.append(True))
    monkeypatch.setattr(update_dialog.UpdateDialog, "check", lambda self: None)
    monkeypatch.setattr(update_dialog, "installed_target", lambda _: updates.InstallTarget(tmp_path, "CURRENTUSER"))
    window = app_module.MainWindow(recovery=False)
    window.data_dir = tmp_path
    window.recovery_path = tmp_path / "recovery.jps"
    window.autosave.stop()

    def before_close(widget):
        widget.autopreview.stop()
        if widget.task:
            widget.task.cancel.set()
            qtbot.waitUntil(lambda: widget.task is None)
        widget.dirty = False

    qtbot.addWidget(window, before_close_func=before_close)
    window.show()
    # Drain the constructor's one-shot preview before restoring the real method.
    qtbot.waitUntil(lambda: bool(initial_render))
    monkeypatch.setattr(app_module.MainWindow, "render_score", original_render)
    monkeypatch.setattr(window, "current_tool", lambda _: tmp_path / "lilypond.exe")
    monkeypatch.setattr(app_module, "render", lambda source, output, tool, cancel, **options: wait_for_cancel(cancel))
    dialog = update_dialog.UpdateDialog(window)
    qtbot.addWidget(dialog)
    dialog.info = updates.UpdateInfo("9.9.9", "", 1, "")
    dialog.installer = tmp_path / "ready.exe"
    dialog.show()
    launched_with_tasks = []
    monkeypatch.setattr(update_dialog, "launch_update", lambda *args: launched_with_tasks.append(window.task))
    window.dirty = True
    window.autopreview.start(60000)
    return window, dialog, launched_with_tasks


def answer_save_prompt(monkeypatch, during_prompt, answer):
    original_question = QMessageBox.question

    def question(*args, **kwargs):
        def respond():
            prompt = QApplication.activeModalWidget()
            assert isinstance(prompt, QMessageBox)
            during_prompt()
            prompt.button(answer).click()

        # This executes inside the real QMessageBox nested event loop.
        QTimer.singleShot(0, respond)
        return original_question(*args, **kwargs)

    monkeypatch.setattr(QMessageBox, "question", question)


def test_pending_preview_during_save_prompt_cannot_race_update(ready_update, qtbot, monkeypatch):
    window, dialog, launched_with_tasks = ready_update
    preview_attempts = []

    def pending_preview():
        preview_attempts.append(True)
        window.render_score(False)

    answer_save_prompt(monkeypatch, pending_preview, QMessageBox.StandardButton.Discard)
    dialog.next_step()

    assert preview_attempts == [True]
    assert launched_with_tasks == [None]
    assert window.task is None
    assert window.updating
    qtbot.waitUntil(lambda: not window.isVisible())


def test_task_started_inside_save_prompt_prevents_installer_launch(ready_update, monkeypatch):
    window, dialog, launched_with_tasks = ready_update

    def start_other_task():
        window.start_task("Test operation", wait_for_cancel, lambda result: None)

    answer_save_prompt(monkeypatch, start_other_task, QMessageBox.StandardButton.Discard)
    dialog.next_step()

    assert window.task is not None
    assert launched_with_tasks == []
    assert window.isVisible()
    assert not window.updating
    assert not window.update_preparing


def test_cancelled_save_prompt_restores_automatic_preview(ready_update, qtbot, monkeypatch):
    window, dialog, launched_with_tasks = ready_update
    answer_save_prompt(monkeypatch, lambda: None, QMessageBox.StandardButton.Cancel)
    dialog.next_step()

    assert launched_with_tasks == []
    assert not window.updating
    assert not window.update_preparing
    assert window.autopreview.isActive()
    assert window.isVisible()
    window.autopreview.start(0)
    qtbot.waitUntil(lambda: window.task is not None)
