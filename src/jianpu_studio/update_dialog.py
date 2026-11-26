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
from . import updates as update_core
from .engine import app_root
from .i18n import tr
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
        # macOS: check only, then open the release page (unsigned app, no
        # in-app installation). Windows keeps the verified in-place update.
        self.page_only = update_core.PLATFORM == "macos"
        self.target = None if self.page_only else installed_target(app_root())
        self.closing = False
        self.setWindowTitle(tr("簡譜工房 · 檢查更新"))
        self.resize(610, 460)
        layout = QVBoxLayout(self)
        self.status = QLabel(tr("目前版本 {version}。正在檢查正式版本…").format(version=__version__))
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.notes = QPlainTextEdit()
        self.notes.setReadOnly(True)
        self.notes.setPlaceholderText(tr("只從本專案 GitHub 正式 Release 取得更新。"))
        layout.addWidget(self.notes, 1)
        self.progress_bar = QProgressBar()
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)
        if self.page_only:
            text = tr("macOS 版請從下載頁取得新的 DMG，將程式拖到「應用程式」取代舊版；樂譜與設定會保留。")
        elif self.target:
            text = tr("更新會保留原安裝位置、設定與樂譜。Program Files 需要管理員授權。")
        else:
            text = tr("目前未找到這個位置的安裝紀錄。免安裝版請從下載頁取得新版，再完整解壓縮使用。")
        self.destination = QLabel(text)
        self.destination.setWordWrap(True)
        layout.addWidget(self.destination)
        row = QHBoxLayout()
        self.check_button = QPushButton(tr("重新檢查"))
        self.check_button.clicked.connect(self.check)
        row.addWidget(self.check_button)
        web = QPushButton(tr("開啟下載頁"))
        web.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(RELEASE_URL)))
        row.addWidget(web)
        row.addStretch()
        self.primary = QPushButton(self.primary_text())
        self.primary.setObjectName("primary")
        self.primary.setEnabled(False)
        self.primary.clicked.connect(self.next_step)
        row.addWidget(self.primary)
        self.cancel_button = QPushButton(tr("關閉"))
        self.cancel_button.clicked.connect(self.reject)
        row.addWidget(self.cancel_button)
        layout.addLayout(row)
        QTimer.singleShot(0, self.check)

    def primary_text(self) -> str:
        return tr("前往下載新版") if self.page_only else tr("下載更新")

    def can_continue(self) -> bool:
        return self.info is not None and (self.page_only or self.target is not None)

    def start(self, operation, complete):
        if self.worker:
            return
        self.check_button.setEnabled(False)
        self.primary.setEnabled(False)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.show()
        self.cancel_button.setText(tr("取消"))
        self.worker = UpdateWorker(operation, self)
        self.worker.progress.connect(self.show_progress)

        def finish(value, error):
            worker = self.worker
            worker.wait()
            worker.deleteLater()
            self.worker = None
            self.progress_bar.hide()
            self.check_button.setEnabled(True)
            self.cancel_button.setText(tr("關閉"))
            if self.closing:
                super(UpdateDialog, self).reject()
                return
            if error:
                self.status.setText(str(error) if isinstance(error, (ValueError, RuntimeError, UpdateCancelled)) else tr("更新連線或檔案處理失敗，請確認網路與磁碟空間後重試。"))
                self.notes.setPlainText(str(error))
                self.primary.setEnabled(self.can_continue())
            else:
                complete(value)

        self.worker.done.connect(finish)
        self.worker.start()

    def check(self):
        self.info, self.installer = None, None
        self.primary.setText(self.primary_text())
        self.status.setText(tr("正在檢查 GitHub 正式版本…"))

        def complete(info):
            self.info = info
            if info is None:
                self.status.setText(tr("目前版本 {version}，沒有較新的正式版本。").format(version=__version__))
                self.notes.setPlainText(tr("可以繼續離線編輯樂譜；稍後需要時再檢查更新。"))
                return
            self.status.setText(tr("可更新至 {version} · 下載約 {size} MB").format(version=info.version, size=f"{info.size / 1024 / 1024:.0f}"))
            self.notes.setPlainText(info.notes)
            self.primary.setEnabled(self.can_continue())

        self.start(lambda *_: fetch_update(__version__), complete)

    def show_progress(self, received, total):
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(int(received * 100 / total))
        self.status.setText(tr("正在下載 {received} / {total} MB；完成後會校驗檔案。").format(
            received=f"{received / 1024 / 1024:.0f}", total=f"{total / 1024 / 1024:.0f}"))

    def next_step(self):
        if self.worker or not self.can_continue():
            return
        if self.page_only:
            QDesktopServices.openUrl(QUrl(self.info.page or RELEASE_URL))
            return
        if self.installer:
            self.window.update_preparing = True
            was_preview_pending = self.window.autopreview.isActive()
            self.window.autopreview.stop()
            try:
                if not self.window.guard_unsaved():
                    return
                if self.window.task:
                    self.status.setText(tr("請先等待目前作業完成，再按「關閉並更新」。"))
                    return
                launch_update(self.installer, self.info, self.target, self.window.data_dir / "update-install.log")
                self.window.updating = True
            except Exception as error:
                self.status.setText(tr("尚未套用更新：{error}").format(error=error))
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
        self.status.setText(tr("正在下載更新，尚未變更已安裝的程式…"))

        def complete(path):
            self.installer = path
            self.status.setText(tr("下載完成，SHA-256 校驗通過。按下方按鈕後會儲存確認、關閉程式並更新；完成後重新開啟。"))
            self.primary.setText(tr("關閉並更新"))
            self.primary.setEnabled(True)

        self.start(lambda cancel, progress: download_update(self.info, self.window.data_dir / "updates", cancel, progress), complete)

    def reject(self):
        if self.worker:
            self.closing = True
            self.worker.cancel.set()
            self.status.setText(tr("正在取消，等待目前的網路讀取結束…"))
            self.cancel_button.setEnabled(False)
            return
        super().reject()

    def closeEvent(self, event):
        if self.worker:
            self.reject()
            event.ignore()
        else:
            event.accept()
