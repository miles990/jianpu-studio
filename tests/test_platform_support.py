from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtGui import QFileOpenEvent
from PySide6.QtWidgets import QApplication, QDialog

from jianpu_studio.platform_support import FileOpenRouter, initial_path_from_args


class FakeWindow(SimpleNamespace):
    def __init__(self):
        super().__init__(task=None, task_blocks_edit=False, update_preparing=False,
                         close_preparing=False, updating=False, opened=[])

    def open_file(self, checked=False, path=None):
        self.opened.append(path)

    def raise_(self):
        pass

    def activateWindow(self):
        pass


def test_finder_open_waits_for_modal_and_blocking_task(qtbot, tmp_path):
    router = FileOpenRouter(QApplication.instance())
    try:
        path = tmp_path / "中文 樂譜.jps"
        QCoreApplication.sendEvent(QApplication.instance(), QFileOpenEvent(str(path)))
        assert router.pending == [path]
        window = FakeWindow()
        window.task, window.task_blocks_edit = object(), True
        modal = QDialog()
        qtbot.addWidget(modal)
        modal.setWindowModality(Qt.WindowModality.ApplicationModal)
        modal.show()
        router.attach(window)
        qtbot.wait(router.RETRY_MS * 2)
        assert window.opened == []
        modal.close()
        qtbot.wait(router.RETRY_MS * 2)
        assert window.opened == []  # blocking import still running
        window.task = None
        qtbot.waitUntil(lambda: window.opened == [path])
        assert router.pending == []
    finally:
        QApplication.instance().removeEventFilter(router)
        router.deleteLater()


def test_initial_path_from_finder_or_command_line():
    assert initial_path_from_args(["app", "-psn_0_12345"]) is None
    assert initial_path_from_args(["app", "我的 樂譜.jps"]) == Path("我的 樂譜.jps")
    assert initial_path_from_args(["app"]) is None
