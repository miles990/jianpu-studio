from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from . import __version__
from .engine import app_root
from .updates import (
    RELEASE_URL,
    UpdateCancelled,
    download_update,
    fetch_update,
    installed_target,
    launch_update,
)


class UpdateWorker(QThread):
    done = Signal(object, object)
    progress = Signal(int, int)

    def __init__(self, operation, parent):
        super().__init__(parent)
        self.operation = operation
        self.cancel = threading.Event()

    def run(self):
        try:
            self.done.emit(self.operation(self.cancel, self.progress.emit), None)
        except Exception as error:
            self.done.emit(None, error)


class UpdateDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.worker = None
        self.info = None
        self.installer: Path | None = None
        self.target = installed_target(app_root())
        self.closing = False
        self.setWindowTitle("簡譜工房 · 檢查更新")
        self.resize(610, 460)
        layout = QVBoxLayout(self)
        self.status = QLabel(f"目前版本 {__version__}。正在檢查正式版本…")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.notes = QPlainTextEdit()
        self.notes.setReadOnly(True)
        self.notes.setPlaceholderText("只從本專案 GitHub 正式 Release 取得更新。")
        layout.addWidget(self.notes, 1)
        self.progress_bar = QProgressBar()
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)
        self.destination = QLabel("更新會保留原安裝位置、設定與樂譜。Program Files 需要管理員授權。" if self.target else
                                  "目前未找到這個位置的安裝紀錄。免安裝版請從下載頁取得新版，再完整解壓縮使用。")
        self.destination.setWordWrap(True)
        layout.addWidget(self.destination)
        row = QHBoxLayout()
        self.check_button = QPushButton("重新檢查")
        self.check_button.clicked.connect(self.check)
        row.addWidget(self.check_button)
        web = QPushButton("開啟下載頁")
        web.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(RELEASE_URL)))
        row.addWidget(web)
        row.addStretch()
        self.primary = QPushButton("下載更新")
        self.primary.setObjectName("primary")
        self.primary.setEnabled(False)
        self.primary.clicked.connect(self.next_step)
        row.addWidget(self.primary)
        self.cancel_button = QPushButton("關閉")
        self.cancel_button.clicked.connect(self.reject)
        row.addWidget(self.cancel_button)
        layout.addLayout(row)
        QTimer.singleShot(0, self.check)

    def start(self, operation, complete):
        if self.worker:
            return
        self.check_button.setEnabled(False)
        self.primary.setEnabled(False)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.show()
        self.cancel_button.setText("取消")
        self.worker = UpdateWorker(operation, self)
        self.worker.progress.connect(self.show_progress)

        def finish(value, error):
            worker = self.worker
            worker.wait()
            worker.deleteLater()
            self.worker = None
            self.progress_bar.hide()
            self.check_button.setEnabled(True)
            self.cancel_button.setText("關閉")
            if self.closing:
                super(UpdateDialog, self).reject()
                return
            if error:
                self.status.setText(str(error) if isinstance(error, (ValueError, RuntimeError, UpdateCancelled)) else "更新連線或檔案處理失敗，請確認網路與磁碟空間後重試。")
                self.notes.setPlainText(str(error))
                self.primary.setEnabled(self.info is not None and self.target is not None)
            else:
                complete(value)

        self.worker.done.connect(finish)
        self.worker.start()

    def check(self):
        self.info, self.installer = None, None
        self.primary.setText("下載更新")
        self.status.setText("正在檢查 GitHub 正式版本…")

        def complete(info):
            self.info = info
            if info is None:
                self.status.setText(f"目前版本 {__version__}，沒有較新的正式版本。")
                self.notes.setPlainText("可以繼續離線編輯樂譜；稍後需要時再檢查更新。")
                return
            self.status.setText(f"可更新至 {info.version} · 下載約 {info.size / 1024 / 1024:.0f} MB")
            self.notes.setPlainText(info.notes)
            self.primary.setEnabled(self.target is not None)

        self.start(lambda *_: fetch_update(__version__), complete)

    def show_progress(self, received, total):
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(int(received * 100 / total))
        self.status.setText(f"正在下載 {received / 1024 / 1024:.0f} / {total / 1024 / 1024:.0f} MB；完成後會校驗檔案。")

    def next_step(self):
        if self.worker or not self.info or not self.target:
            return
        if self.installer:
            self.window.update_preparing = True
            was_preview_pending = self.window.autopreview.isActive()
            self.window.autopreview.stop()
            try:
                if not self.window.guard_unsaved():
                    return
                if self.window.task:
                    self.status.setText("請先等待目前作業完成，再按「關閉並更新」。")
                    return
                launch_update(self.installer, self.info, self.target, self.window.data_dir / "update-install.log")
                self.window.updating = True
            except Exception as error:
                self.status.setText(f"尚未套用更新：{error}")
                return
            finally:
                if not self.window.updating:
                    self.window.update_preparing = False
                    QTimer.singleShot(0, self.window.resume_pending_output)
                    if was_preview_pending or self.window.dirty:
                        self.window.autopreview.start()
            self.accept()
            QTimer.singleShot(0, self.window.close)
            return
        self.status.setText("正在下載更新，尚未變更已安裝的程式…")

        def complete(path):
            self.installer = path
            self.status.setText("下載完成，SHA-256 校驗通過。按下方按鈕後會儲存確認、關閉程式並更新；完成後重新開啟。")
            self.primary.setText("關閉並更新")
            self.primary.setEnabled(True)

        self.start(lambda cancel, progress: download_update(self.info, self.window.data_dir / "updates", cancel, progress), complete)

    def reject(self):
        if self.worker:
            self.closing = True
            self.worker.cancel.set()
            self.status.setText("正在取消，等待目前的網路讀取結束…")
            self.cancel_button.setEnabled(False)
            return
        super().reject()

    def closeEvent(self, event):
        if self.worker:
            self.reject()
            event.ignore()
        else:
            event.accept()
