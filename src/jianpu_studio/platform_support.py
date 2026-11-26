"""Small operating-system differences kept out of the main window."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtWidgets import QApplication


def initial_path_from_args(args: list[str]) -> Path | None:
    # Old macOS versions passed a "-psn_..." process serial number argument.
    if len(args) > 1 and not args[1].startswith("-psn_"):
        return Path(args[1])
    return None


class FileOpenRouter(QObject):
    """Deliver macOS Finder "open document" events to the main window.

    Finder sends QFileOpenEvent instead of a command-line path, also to an
    already running instance. Requests wait until no modal dialog (for
    example the recovery prompt), blocking task, update or close is in
    progress, then use the window's normal unsaved-work guarded open.
    """

    RETRY_MS = 300

    def __init__(self, app: QApplication):
        super().__init__(app)
        self.window = None
        self.pending: list[Path] = []
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.deliver)
        app.installEventFilter(self)

    def attach(self, window):
        self.window = window
        if self.pending:
            self.timer.start(self.RETRY_MS)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.FileOpen and watched is QApplication.instance():
            path = event.file()
            if path:
                self.pending.append(Path(path))
                self.timer.start(0 if self.window else self.RETRY_MS)
            return True
        return False

    def busy(self) -> bool:
        window = self.window
        return (window is None or QApplication.activeModalWidget() is not None
                or bool(window.task and window.task_blocks_edit)
                or getattr(window, "update_preparing", False) or getattr(window, "close_preparing", False)
                or getattr(window, "updating", False))

    def deliver(self):
        if not self.pending:
            return
        if self.busy():
            self.timer.start(self.RETRY_MS)
            return
        # Only the most recent request matters; each open replaces the score.
        path, self.pending = self.pending[-1], []
        self.window.raise_()
        self.window.activateWindow()
        self.window.open_file(path=path)
