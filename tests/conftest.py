import sys
import tempfile

import pytest
from PySide6.QtCore import QSettings

from jianpu_studio import i18n

# The suite asserts Traditional texts: isolate settings and force zh_TW.
_SETTINGS_DIR = tempfile.mkdtemp(prefix="jianpu-test-settings-")
QSettings.setDefaultFormat(QSettings.Format.IniFormat)
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, _SETTINGS_DIR)


class _IsolatedSettings(QSettings):
    """QSettings(organization, application) always uses the native store (the real macOS plist
    or Windows registry), ignoring setDefaultFormat. The suite must never touch the user's
    settings, so that form is redirected to the INI files above."""

    def __init__(self, *args, **kwargs):
        if len(args) == 2 and all(isinstance(a, str) for a in args) and not kwargs:
            super().__init__(QSettings.Format.IniFormat, QSettings.Scope.UserScope, *args)
        else:
            super().__init__(*args, **kwargs)


import PySide6.QtCore as _QtCore  # noqa: E402

_QtCore.QSettings = _IsolatedSettings
QSettings = _IsolatedSettings


@pytest.fixture(autouse=True)
def traditional_chinese_ui():
    QSettings("JianpuStudio", "JianpuStudio").setValue(i18n.SETTINGS_KEY, i18n.TRADITIONAL)
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
