import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# Before anything imports Qt or the app: every settings object and the data folder (recovery,
# print cache, preview MIDI) go to this session's temporary folder, never the user's real store.
_SESSION_ROOT = Path(tempfile.mkdtemp(prefix="jianpu-test-store-")).resolve()
os.environ["JIANPU_SETTINGS_DIR"] = str(_SESSION_ROOT / "settings")
os.environ["JIANPU_DATA_DIR"] = str(_SESSION_ROOT / "data")

import pytest  # noqa: E402
from PySide6 import QtCore as _QtCore  # noqa: E402

from jianpu_studio import i18n, storage  # noqa: E402

REAL_QSETTINGS = _QtCore.QSettings


class _NoNativeSettings(REAL_QSETTINGS):
    """Only QSettings(file_name, IniFormat) is allowed in tests. QSettings(organization,
    application) always opens the native store (the real macOS plist or Windows registry) and
    ignores setDefaultFormat/setPath; QSettings() and the scope forms reach the user's home too.
    Tests read the app's settings through storage.open_settings()."""

    def __init__(self, *args, **kwargs):
        if not (len(args) >= 2 and isinstance(args[0], str) and args[1] == REAL_QSETTINGS.Format.IniFormat):
            raise AssertionError(f"QSettings{args!r} would open the user's real settings; use storage.open_settings()")
        super().__init__(*args, **kwargs)


_QtCore.QSettings = _NoNativeSettings


def _real_settings_snapshot():
    """What the user's real settings store holds now, read without the test override, or None."""
    try:
        if sys.platform == "darwin":
            # Read through cfprefsd so unflushed writes count, not just the plist file on disk.
            out = subprocess.run(["defaults", "export", "com.jianpustudio.JianpuStudio", "-"],
                                 capture_output=True, timeout=20)
            return hashlib.sha256(out.stdout).hexdigest() if out.returncode == 0 else None
        if sys.platform == "win32":
            out = subprocess.run(["reg", "query", r"HKCU\Software\JianpuStudio\JianpuStudio", "/s"],
                                 capture_output=True, timeout=20)
            return hashlib.sha256(out.stdout).hexdigest() if out.returncode == 0 else None
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
        path = base / "JianpuStudio" / "JianpuStudio.conf"
        return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    except (OSError, subprocess.SubprocessError):
        return "unreadable"


@pytest.fixture(scope="session", autouse=True)
def _real_settings_untouched():
    """Fails the session if the user's real settings changed while it ran. A real Jianpu Studio
    used at the same time on this machine also writes there; rerun if that was the cause."""
    before = _real_settings_snapshot()
    yield
    after = _real_settings_snapshot()
    assert after == before, "the test session changed the user's real Jianpu Studio settings"


def _assert_store_is_isolated():
    for location in (storage.open_settings().fileName(), storage.data_dir()):
        assert Path(location).resolve().is_relative_to(_SESSION_ROOT), location


@pytest.fixture(autouse=True)
def _store_is_isolated():
    _assert_store_is_isolated()


@pytest.hookimpl(wrapper=True)
def pytest_runtest_teardown(item):
    """Again after every fixture is torn down: a test may point the store at its own tmp_path
    with monkeypatch.setenv, but must not leave it anywhere else."""
    result = yield
    _assert_store_is_isolated()
    return result


@pytest.fixture(autouse=True)
def traditional_chinese_ui():
    # The suite asserts Traditional texts.
    storage.open_settings().setValue(i18n.SETTINGS_KEY, i18n.TRADITIONAL)
    i18n.set_language(i18n.TRADITIONAL)
    yield
    i18n.set_language(i18n.TRADITIONAL)


@pytest.fixture(autouse=True)
def _reset_macos_event_dispatcher():
    """Work around Qt's macOS Core Foundation dispatcher in pytest-qt.

    A modal exec() started outside QApplication.exec() (as GUI tests do)
    leaves later processEvents()/qWait() calls without timers or queued
    signals. One short real event loop restores normal delivery. The app
    itself always runs nested dialogs inside QApplication.exec().
    """
    yield
    if sys.platform == "darwin":
        from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer
        if QCoreApplication.instance() is not None:
            loop = QEventLoop()
            QTimer.singleShot(0, loop.quit)
            loop.exec()


@pytest.fixture(autouse=True)
def _no_unexpected_rhythm_choice(monkeypatch):
    """A refused rhythm edit asks with a modal box; offscreen that would hang. Tests that expect
    the question replace ``choose_rhythm_option`` themselves."""
    from jianpu_studio.editing import EditActions

    def unexpected(self, refused):
        pytest.fail(f"unexpected rhythm choice: {refused}")
    monkeypatch.setattr(EditActions, "choose_rhythm_option", unexpected)
