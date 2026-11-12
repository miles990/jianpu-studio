import hashlib
import io
import threading
from dataclasses import replace

import pytest

from jianpu_studio import updates


def metadata(version="9.9.9", content=b"verified installer"):
    name = f"JianpuStudio-{version}-Windows-x64-Setup.exe"
    return {"tag_name": "v"+version, "draft": False, "prerelease": False, "body": "Release notes",
            "assets": [{"name": name, "state": "uploaded", "size": len(content),
                        "browser_download_url": f"https://github.com/{updates.REPOSITORY}/releases/download/v{version}/{name}",
                        "digest": "sha256:"+hashlib.sha256(content).hexdigest()}]}


def test_stable_version_and_downgrade_checks():
    assert updates.release_info(metadata("0.1.3"), "0.1.2").version == "0.1.3"
    assert updates.release_info(metadata("0.1.2"), "0.1.3") is None
    assert updates.release_info(metadata("0.1.3"), "0.1.3") is None
    assert updates.version_tuple("v0.10.0") > updates.version_tuple("0.9.9")


@pytest.mark.parametrize("change", [
    lambda d: d.update(prerelease=True),
    lambda d: d.update(draft=True),
    lambda d: d.update(tag_name="v1.2.3/../../x"),
    lambda d: d["assets"][0].update(browser_download_url="https://example.org/update.exe"),
    lambda d: d["assets"][0].update(digest=""),
    lambda d: d["assets"][0].update(size=updates.MAX_INSTALLER_BYTES+1),
    lambda d: d["assets"][0].update(state="new"),
])
def test_invalid_release_never_becomes_executable_update(change):
    data = metadata()
    change(data)
    with pytest.raises(ValueError):
        updates.release_info(data, "0.1.2")


def test_verified_download_and_partial_cleanup(tmp_path, monkeypatch):
    content = b"verified installer"
    info = updates.release_info(metadata(content=content), "0.1.2")
    monkeypatch.setattr(updates.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(content))
    progress = []
    path = updates.download_update(info, tmp_path, threading.Event(), lambda *p: progress.append(p))
    assert path.read_bytes() == content
    assert progress[-1] == (len(content), len(content))
    assert not list(tmp_path.glob("*.download"))
    with pytest.raises(ValueError, match="校驗失敗"):
        updates.download_update(replace(info, sha256="0"*64), tmp_path, threading.Event())
    assert path.read_bytes() == content  # failed download leaves verified old file intact
    assert not list(tmp_path.glob("*.download"))


def test_cancellation_removes_partial_file(tmp_path, monkeypatch):
    content = b"x" * (1024*1024+20)
    info = updates.release_info(metadata(content=content), "0.1.2")
    monkeypatch.setattr(updates.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(content))
    cancel = threading.Event()
    with pytest.raises(updates.UpdateCancelled):
        updates.download_update(info, tmp_path, cancel, lambda *_: cancel.set())
    assert list(tmp_path.iterdir()) == []


def test_size_mismatch_removes_download(tmp_path, monkeypatch):
    info = updates.release_info(metadata(), "0.1.2")
    monkeypatch.setattr(updates.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(b"x"*200))
    with pytest.raises(ValueError, match="大小不符"):
        updates.download_update(info, tmp_path, threading.Event())
    assert list(tmp_path.iterdir()) == []


def test_upgrade_preserves_exact_path_and_scope(tmp_path):
    target = updates.InstallTarget(tmp_path / "中文 folder", "CURRENTUSER")
    args = updates.installer_arguments(target, tmp_path / "log.txt")
    assert "/CURRENTUSER" in args
    assert f"/DIR={target.directory}" in args
    assert "/JIANPURESTART=1" in args
    assert "/ALLUSERS" not in args


def test_changed_installer_is_not_launched(tmp_path):
    path = tmp_path / "installer.exe"
    path.write_bytes(b"tampered")
    info = updates.release_info(metadata(), "0.1.2")
    with pytest.raises(ValueError, match="下載後已改變"):
        updates.launch_update(path, info, updates.InstallTarget(tmp_path, "CURRENTUSER"), tmp_path / "log")


def test_update_dialog_save_guard(qtbot, tmp_path, monkeypatch):
    from jianpu_studio import update_dialog
    from jianpu_studio.app import MainWindow
    from jianpu_studio.update_dialog import UpdateDialog
    monkeypatch.setattr(MainWindow, "render_score", lambda *a: None)
    info = updates.release_info(metadata(), "0.1.2")
    monkeypatch.setattr(update_dialog, "fetch_update", lambda _: info)
    monkeypatch.setattr(update_dialog, "installed_target", lambda _: updates.InstallTarget(tmp_path, "CURRENTUSER"))
    monkeypatch.setattr(update_dialog, "download_update", lambda *a: tmp_path / "ready.exe")
    launched = []
    monkeypatch.setattr(update_dialog, "launch_update", lambda *args: launched.append(args))
    w = MainWindow(recovery=False)
    qtbot.addWidget(w)
    dialog = UpdateDialog(w)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitUntil(lambda: dialog.info is not None and dialog.worker is None)
    dialog.next_step()
    qtbot.waitUntil(lambda: dialog.installer is not None and dialog.worker is None)
    monkeypatch.setattr(w, "guard_unsaved", lambda: False)
    dialog.next_step()
    assert not launched and not w.updating
    monkeypatch.setattr(w, "guard_unsaved", lambda: True)
    dialog.next_step()
    assert len(launched) == 1 and w.updating
    w.dirty = False
    w.close()
