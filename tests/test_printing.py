import os
import time
from pathlib import Path

from PySide6.QtWidgets import QApplication

from jianpu_studio import app as app_module
from jianpu_studio.engine import RenderResult
from jianpu_studio.printing import prepare_print_copy


def test_print_copies_reuse_content_and_only_old_owned_copies_are_cleaned(tmp_path, monkeypatch):
    source = tmp_path / "score.pdf"
    source.write_bytes(b"%PDF-test")
    cache = tmp_path / "print-cache"
    first = prepare_print_copy(source, cache)
    assert prepare_print_copy(source, cache) == first
    source.write_bytes(b"%PDF-test revised score")
    second = prepare_print_copy(source, cache)
    assert first != second
    assert first.read_bytes() == b"%PDF-test"
    assert second.read_bytes() == source.read_bytes()
    user_file = cache / "my-score.pdf"
    user_file.write_bytes(b"keep")
    old = time.time() - 8 * 24 * 60 * 60
    for path in (first, second, user_file):
        os.utime(path, (old, old))
    original_unlink = Path.unlink

    def locked(path, *args, **kwargs):
        if path == second:
            raise PermissionError("Reader still has this PDF open")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", locked)
    fresh = prepare_print_copy(source, cache)
    assert fresh == second
    assert fresh.exists() and second.exists() and user_file.exists()
    assert not first.exists()


def test_external_print_reader_receives_copy_outside_app_temporary_directory(qtbot, tmp_path, monkeypatch):
    window = app_module.MainWindow(recovery=False)
    window.data_dir = tmp_path / "user-data"
    window.recovery_path = window.data_dir / "recovery.jps"
    window.autosave.stop()
    qtbot.addWidget(window, before_close_func=lambda w: setattr(w, "dirty", False))
    source = window.work / "score.pdf"
    source.write_bytes(b"%PDF-test external print copy")
    window.result = RenderResult(source, window.work / "score.midi", window.work / "score.ly", "")
    window.rendered_revision = window.revision
    opened = []
    monkeypatch.setattr(app_module.QDesktopServices, "openUrl", lambda url: opened.append(Path(url.toLocalFile())) or True)
    window.open_pdf()
    assert len(opened) == 1 and opened[0].is_relative_to(window.data_dir)
    assert not opened[0].is_relative_to(window.work)
    content = source.read_bytes()
    window.dirty = False
    window.close()
    QApplication.processEvents()
    assert not window.work.exists()
    assert opened[0].read_bytes() == content
