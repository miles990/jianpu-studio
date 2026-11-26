import sys
import tempfile

import pytest
from PySide6.QtCore import QSettings

from jianpu_studio import i18n

# The suite asserts Traditional texts: isolate settings and force zh_TW.
_SETTINGS_DIR = tempfile.mkdtemp(prefix="jianpu-test-settings-")
QSettings.setDefaultFormat(QSettings.Format.IniFormat)
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, _SETTINGS_DIR)


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
