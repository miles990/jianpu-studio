"""Output requests survive background jobs and nested file-dialog event loops."""

import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtGui import QPainter, QPdfWriter
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox

from jianpu_studio import app as app_module
from jianpu_studio import update_dialog, updates
from jianpu_studio.engine import Cancelled, RenderResult


@pytest.fixture
def output_window(qtbot, tmp_path, monkeypatch):
    template = tmp_path / "template.pdf"
    writer = QPdfWriter(str(template))
    painter = QPainter(writer)
    painter.drawText(100, 100, "Output regression")
    painter.end()
    del writer
    pdf_bytes = template.read_bytes()
    jobs, errors = [], []

    def render(source, output, tool, cancel, **options):
        job = SimpleNamespace(create_pdf=options["create_pdf"], output=output, release=threading.Event())
        jobs.append(job)
        if not job.release.wait(5):
            raise RuntimeError("Test did not release the output worker")
        if cancel.is_set():
            raise Cancelled("Test output cancelled")
        midi = output / "score.midi"
        midi.write_bytes(b"MThd" + output.name.encode("ascii"))
        lilypond = output / "score.ly"
        lilypond.write_text("test notation", encoding="utf-8")
        pdf = output / "score.pdf"
        if job.create_pdf:
            pdf.write_bytes(pdf_bytes)
        return RenderResult(pdf, midi, lilypond, "Test output")

    monkeypatch.setattr(app_module, "render", render)
    window = app_module.MainWindow(recovery=False)
    window.data_dir = tmp_path
    window.recovery_path = tmp_path / "recovery.jps"
    window.autosave.stop()
    monkeypatch.setattr(window, "current_tool", lambda _: tmp_path / "lilypond")
    monkeypatch.setattr(window, "error", lambda title, error: errors.append((title, str(error))))

    def before_close(widget):
        widget.cancel_task()
        for job in jobs:
            job.release.set()
        qtbot.waitUntil(lambda: widget.task is None)
        widget.dirty = False

    qtbot.addWidget(window, before_close_func=before_close)
    window.show()
    # The normal startup timer must remain idle in the editing workspace.
    qtbot.wait(300)
    assert jobs == []
    return SimpleNamespace(window=window, jobs=jobs, errors=errors, pdf_bytes=pdf_bytes)


def test_disabling_auto_keeps_manual_pdf_export_queued(output_window, qtbot, tmp_path, monkeypatch):
    state = output_window
    window = state.window
    destination = tmp_path / "exported.pdf"
    dialogs = []

    def choose_file(*args, **kwargs):
        dialogs.append(True)
        return str(destination), ""

    monkeypatch.setattr(QFileDialog, "getSaveFileName", choose_file)
    window.render_score(True, create_pdf=False)
    qtbot.waitUntil(lambda: len(state.jobs) == 1)
    window.show_print_preview()
    window.export("pdf")
    window.auto_check.setChecked(False)
    state.jobs[0].release.set()

    qtbot.waitUntil(lambda: len(state.jobs) == 2)
    assert state.jobs[1].create_pdf
    state.jobs[1].release.set()
    qtbot.waitUntil(lambda: window.task is None and destination.exists())
    assert destination.read_bytes() == state.pdf_bytes
    assert dialogs == [True]
    assert window.output_request is None
    assert state.errors == []


def test_cached_midi_export_survives_pdf_finishing_inside_file_dialog(output_window, qtbot, tmp_path, monkeypatch):
    state = output_window
    window = state.window
    window.render_score(True, create_pdf=False)
    qtbot.waitUntil(lambda: len(state.jobs) == 1)
    state.jobs[0].release.set()
    qtbot.waitUntil(lambda: window.task is None and window.result is not None)
    original_source = window.result.midi
    expected = original_source.read_bytes()
    destination = tmp_path / "exported.midi"
    window.show_print_preview()
    qtbot.waitUntil(lambda: len(state.jobs) == 2)
    removed_while_dialog_open = []

    def choose_file(*args, **kwargs):
        dialog = QFileDialog(window, "Export MIDI", str(tmp_path))
        dialog.setOption(QFileDialog.Option.DontUseNativeDialog)
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        dialog.setFileMode(QFileDialog.FileMode.AnyFile)
        dialog.selectFile(str(destination))
        timer = QTimer(dialog)
        deadline = QTimer(dialog)
        deadline.setSingleShot(True)
        deadline.timeout.connect(dialog.reject)

        def after_pdf_finishes():
            if window.task is None:
                removed_while_dialog_open.append(not original_source.exists())
                timer.stop()
                dialog.accept()

        timer.timeout.connect(after_pdf_finishes)
        timer.start(10)
        deadline.start(3000)
        state.jobs[1].release.set()
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        timer.stop()
        deadline.stop()
        selected = Path(dialog.selectedFiles()[0]) if accepted else None
        dialog.deleteLater()
        return (str(selected), "") if selected else ("", "")

    monkeypatch.setattr(QFileDialog, "getSaveFileName", choose_file)
    window.export("midi")

    assert removed_while_dialog_open == [True]
    assert state.errors == []
    assert destination.read_bytes() == expected
    assert window.result.midi.exists()
    assert window.task is None


def test_cancelled_update_resumes_pdf_queued_during_save_prompt(output_window, qtbot, tmp_path, monkeypatch):
    state = output_window
    window = state.window
    destination = tmp_path / "resumed-export.pdf"
    launched, deferred_in_prompt = [], []
    monkeypatch.setattr(update_dialog.UpdateDialog, "check", lambda self: None)
    monkeypatch.setattr(update_dialog, "installed_target", lambda _: updates.InstallTarget(tmp_path, "CURRENTUSER"))
    monkeypatch.setattr(update_dialog, "launch_update", lambda *args: launched.append(args))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args, **kwargs: (str(destination), ""))
    real_render = window.render_score

    def observe_render(*args, **kwargs):
        if window.update_preparing:
            deferred_in_prompt.append(True)
        return real_render(*args, **kwargs)

    monkeypatch.setattr(window, "render_score", observe_render)
    window.dirty = True
    window.render_score(True, create_pdf=False)
    qtbot.waitUntil(lambda: len(state.jobs) == 1)
    window.export("pdf")
    updater = update_dialog.UpdateDialog(window)
    qtbot.addWidget(updater)
    updater.info = updates.UpdateInfo("9.9.9", "", 1, "")
    updater.installer = tmp_path / "ready.exe"
    updater.show()
    original_question = QMessageBox.question

    def question(*args, **kwargs):
        timer = QTimer(window)
        deadline = QTimer(window)
        deadline.setSingleShot(True)

        def cancel_prompt():
            prompt = QApplication.activeModalWidget()
            if isinstance(prompt, QMessageBox):
                prompt.button(QMessageBox.StandardButton.Cancel).click()

        def after_pending_render_is_deferred():
            if deferred_in_prompt:
                timer.stop()
                cancel_prompt()

        timer.timeout.connect(after_pending_render_is_deferred)
        deadline.timeout.connect(cancel_prompt)
        timer.start(10)
        deadline.start(3000)
        state.jobs[0].release.set()
        try:
            # The completed MIDI job queues its PDF while this real dialog runs.
            return original_question(*args, **kwargs)
        finally:
            timer.stop()
            deadline.stop()
            timer.deleteLater()
            deadline.deleteLater()

    monkeypatch.setattr(QMessageBox, "question", question)
    updater.next_step()

    assert deferred_in_prompt == [True]
    assert launched == []
    assert not window.updating and not window.update_preparing
    assert updater.isVisible()
    qtbot.waitUntil(lambda: len(state.jobs) == 2)
    assert state.jobs[1].create_pdf
    state.jobs[1].release.set()
    qtbot.waitUntil(lambda: window.task is None and destination.exists())
    assert destination.read_bytes() == state.pdf_bytes
    assert window.output_request is None
    assert state.errors == []
