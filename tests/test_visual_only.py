"""Removing source controls must not replace authoritative legacy music."""

import threading
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QStandardPaths
from PySide6.QtWidgets import QFileDialog, QMessageBox

from jianpu_studio import app as app_module
from jianpu_studio.app import MainWindow
from jianpu_studio.engine import Cancelled
from jianpu_studio.model import Note, Project, Score, load_project, save_project

XML = '''<score-partwise version="4.0"><part-list>
<score-part id="P1"><part-name>Erhu</part-name></score-part></part-list>
<part id="P1"><measure number="1"><attributes><divisions>1</divisions>
<key><fifths>0</fifths></key><time><beats>4</beats><beat-type>4</beat-type></time>
</attributes><note><pitch><step>C</step><octave>4</octave></pitch>
<duration>4</duration><type>whole</type></note></measure></part></score-partwise>'''


@pytest.fixture
def window(qtbot, monkeypatch, tmp_path):
    errors = []
    monkeypatch.setattr(MainWindow, "render_score", lambda *a: None)
    monkeypatch.setattr(MainWindow, "error", lambda self, title, error: errors.append((title, str(error))))
    widget = MainWindow(recovery=False)
    widget.errors = errors
    widget.recovery_path = tmp_path / "recovery.jps"
    widget.autosave.stop()
    qtbot.addWidget(widget, before_close_func=lambda w: setattr(w, "dirty", False))
    widget.reset(Project(Score(notes=[Note(5, duration=1)])))
    widget.show()
    return widget


def legacy_project(*, equivalent=False):
    score = Score(title="舊樂譜", notes=[Note(1, duration=1)])
    source = score.to_jianpu() if equivalent else "title=獨立原始樂譜\n1=C\n4/4\n2 - - -\n"
    return Project(score=score, mode="source", source=source)


def test_equivalent_legacy_file_opens_visually_without_rewriting_file(window, tmp_path):
    path = tmp_path / "舊專案.jps"
    original = legacy_project(equivalent=True)
    save_project(path, original)
    data = path.read_bytes()
    window.open_file(path=path)
    assert not window.errors
    assert window.project.mode == "visual"
    assert window.project.score == original.score
    assert original.mode == "source"
    assert path.read_bytes() == data
    assert window.save()
    assert load_project(path).mode == "visual"


def test_divergent_legacy_file_keeps_current_unsaved_score_and_history(window, tmp_path, monkeypatch):
    window.mutate(lambda project: setattr(project.score, "title", "正在編輯"))
    window.path = tmp_path / "目前樂譜.jps"
    original, history, revision = window.project.snapshot(), list(window.history), window.revision
    original_path = window.path
    result = window.result = SimpleNamespace(pdf="existing")
    path = tmp_path / "舊原始碼.jps"
    save_project(path, legacy_project())
    data = path.read_bytes()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.Discard)
    window.open_file(path=path)
    assert "原檔" in window.errors[-1][1]
    assert (window.project, window.history, window.revision) == (original, history, revision)
    assert window.path == original_path
    assert window.result is result
    assert window.dirty
    assert path.read_bytes() == data


def test_invalid_reset_does_not_cancel_render_or_change_state(window):
    original = window.project.snapshot()
    cancel = threading.Event()
    window.task = SimpleNamespace(cancel=cancel)
    window.task_blocks_edit = False
    try:
        with pytest.raises(ValueError, match="未載入"):
            window.reset(legacy_project())
        assert not cancel.is_set()
        assert window.project == original
    finally:
        window.task = None


def test_source_file_open_has_no_fallback_editor(window, tmp_path):
    original = window.project.snapshot()
    path = tmp_path / "score.jly"
    path.write_text("1=C\n4/4\n1 - - -", encoding="utf-8")
    window.open_file(path=path)
    assert window.project == original
    assert ".jps" in window.errors[-1][1]
    assert not hasattr(window, "source")


@pytest.mark.parametrize("queued", [False, True])
@pytest.mark.parametrize("fails", [False, True])
def test_import_locks_score_settings_until_worker_finishes(window, qtbot, queued, fails):
    release = threading.Event()
    started = threading.Event()
    def import_worker(cancel):
        started.set()
        if not release.wait(5):
            raise RuntimeError("worker was not released")
        if fails:
            raise ValueError("unsupported music")
        return Project(Score(title="匯入樂譜", notes=[Note(1, duration=1)]))
    def background_worker(cancel):
        cancel.wait(5)
        raise Cancelled("cancelled for import")
    try:
        if queued:
            window.start_task("背景排版", background_worker, lambda result: None, blocks_edit=False)
            assert window.score_settings.isEnabled()
            assert window.title_field.isEnabled()
        window.start_task("匯入", import_worker, lambda project: window.reset(project, dirty=True))
        # Also locked while a renderer is cancelling before the import starts.
        assert not window.editor.isEnabled()
        assert not window.score_settings.isEnabled()
        assert not window.title_field.isEnabled()
        qtbot.waitUntil(started.is_set)
        assert not window.title_field.isEnabled()
    finally:
        release.set()
        qtbot.waitUntil(lambda: window.task is None)
    assert window.score_settings.isEnabled()
    assert window.title_field.isEnabled()
    assert window.editor.isEnabled()
    assert bool(window.errors) == fails


@pytest.mark.parametrize("supported", [True, False])
def test_import_uses_visual_score_or_preserves_current_project(window, qtbot, tmp_path, monkeypatch, supported):
    original = window.project.snapshot()
    path = tmp_path / "樂譜.musicxml"
    xml = XML if supported else XML.replace("<duration>4</duration>", '<duration>4</duration><lyric><text>歌</text></lyric>')
    path.write_text(xml, encoding="utf-8")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a: (str(path), ""))
    monkeypatch.setattr(QMessageBox, "information", lambda *a: None)
    window.import_musicxml()
    qtbot.waitUntil(lambda: window.task is None, timeout=5000)
    assert path.read_text(encoding="utf-8") == xml
    if supported:
        assert not window.errors
        assert window.project.mode == "visual"
        assert window.project.score.notes == [Note(1, duration=1)]
        assert window.dirty
    else:
        assert window.project == original
        assert not window.dirty
        assert "本次未匯入" in window.errors[-1][1]


def test_legacy_recovery_archives_original_bytes_before_new_autosave(window):
    save_project(window.recovery_path, legacy_project())
    data = window.recovery_path.read_bytes()
    window.restore_recovery()
    backups = list(window.recovery_path.parent.glob("recovery-legacy-*.jps"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == data
    assert not window.recovery_path.exists()
    assert not window.recovery_protected
    assert str(backups[0]) in window.errors[-1][1]
    window.mutate(lambda project: setattr(project.score, "title", "新編輯"))
    window.write_recovery()
    assert load_project(window.recovery_path) == window.project
    window.reset(Project())
    assert backups[0].read_bytes() == data


@pytest.mark.parametrize("corrupt", [False, True])
def test_unbacked_recovery_survives_autosave_reset_save_and_close(window, tmp_path, monkeypatch, corrupt):
    if corrupt:
        window.recovery_path.write_bytes(b"damaged recovery, preserve for repair")
    else:
        save_project(window.recovery_path, legacy_project())
    data = window.recovery_path.read_bytes()
    def cannot_backup(*args):
        raise OSError("disk full")
    monkeypatch.setattr(app_module, "atomic_write", cannot_backup)
    window.restore_recovery()
    assert window.recovery_protected
    window.mutate(lambda project: setattr(project.score, "title", "新樂譜"))
    window.write_recovery()
    assert window.recovery_path.read_bytes() == data
    window.path = tmp_path / "手動儲存.jps"
    assert window.save()
    assert load_project(window.path) == window.project
    window.reset(Project())
    assert window.close()
    assert window.recovery_path.read_bytes() == data


@pytest.mark.parametrize("recovery_kind", ["legacy", "corrupt", "visual"])
def test_launching_file_finishes_recovery_before_opening(window, qtbot, tmp_path, monkeypatch, recovery_kind):
    recovery_path = tmp_path / "recovery.jps"
    recovered = Project(Score(title="尚未儲存的樂譜", notes=[Note(7, duration=1)]))
    if recovery_kind == "corrupt":
        recovery_path.write_bytes(b"keep this damaged recovery")
    else:
        save_project(recovery_path, legacy_project() if recovery_kind == "legacy" else recovered)
    recovery_bytes = recovery_path.read_bytes()
    path = tmp_path / "雙擊開啟的樂譜.jps"
    requested = Project(Score(title="要求開啟", notes=[Note(2, duration=1)]))
    save_project(path, requested)
    original_file = path.read_bytes()
    monkeypatch.setattr(QStandardPaths, "writableLocation", lambda location: str(tmp_path))
    answers = []
    def question(parent, title, *args):
        answers.append(title)
        # A nested event loop is allowed to run, but no open callback may run
        # until this recovery question returns.
        qtbot.wait(10)
        if title == "復原未儲存樂譜":
            assert recovery_path.read_bytes() == recovery_bytes
            return QMessageBox.StandardButton.Yes
        assert title == "尚未儲存"
        return QMessageBox.StandardButton.Cancel
    monkeypatch.setattr(QMessageBox, "question", question)
    launched = MainWindow(recovery=True, initial_path=path)
    launched.autosave.stop()
    qtbot.addWidget(launched, before_close_func=lambda w: setattr(w, "dirty", False))
    launched.show()
    if recovery_kind == "visual":
        qtbot.waitUntil(lambda: len(answers) == 2)
        assert launched.project == recovered
        assert launched.dirty
        assert launched.path is None
    else:
        qtbot.waitUntil(lambda: launched.path == path)
        assert launched.project == requested
        if recovery_kind == "legacy":
            backup, = tmp_path.glob("recovery-legacy-*.jps")
            assert backup.read_bytes() == recovery_bytes
        else:
            assert launched.recovery_protected
            assert recovery_path.read_bytes() == recovery_bytes
    assert path.read_bytes() == original_file
