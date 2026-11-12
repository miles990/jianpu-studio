from __future__ import annotations

import shutil
import tempfile
import threading
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, QSettings, QStandardPaths, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence, QPixmap
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from . import __version__
from .editing import EditActions
from .engine import Cancelled, find_tool, recognize, render, run_tool, worker_command, worker_env
from .model import (
    MAX_FILE_BYTES,
    Note,
    Project,
    Score,
    atomic_write,
    demo_project,
    load_project,
    save_project,
)
from .musicxml import read_xml, visual_import
from .photo import PhotoDialog
from .playback import MidiPlayer
from .widgets import STYLE
from .workspace import build_workspace


def label(text: str, name: str = "", wrap: bool = False) -> QLabel:
    item = QLabel(text)
    item.setObjectName(name)
    item.setWordWrap(wrap)
    item.setTextFormat(Qt.TextFormat.PlainText)
    return item


def button(text: str, fn, name: str = "") -> QPushButton:
    item = QPushButton(text)
    item.setObjectName(name)
    item.clicked.connect(fn)
    return item


class Task(QThread):
    completed = Signal(object, object)

    def __init__(self, fn, parent):
        super().__init__(parent)
        self.fn = fn
        self.cancel = threading.Event()

    def run(self):
        try:
            self.completed.emit(self.fn(self.cancel), None)
        except Exception as exc:
            self.completed.emit(None, exc)


class MainWindow(EditActions, QMainWindow):
    def __init__(self, recovery: bool = True):
        super().__init__()
        self.setMinimumSize(860, 560)
        self.resize(1380, 850)
        self.setStyleSheet(STYLE)
        self.settings = QSettings("JianpuStudio", "JianpuStudio")
        self.project = demo_project()
        self.path: Path | None = None
        self.dirty = False
        self.loading = False
        self.history = []
        self.future = []
        self.revision = 0
        self.rendered_revision = -1
        self.result = None
        self.player = MidiPlayer()
        self.task: Task | None = None
        self.task_blocks_edit = False
        self.render_pending = False
        self.render_pending_manual = False
        self.render_pending_pdf = False
        self.print_preview_active = False
        self.output_request = None
        self.render_cancelled = False
        self.pending_task = None
        self.close_requested = False
        self.close_preparing = False
        self.updating = False
        self.update_preparing = False
        self.play_after_render = False
        self.inspector_loading = False
        self.photo_pixmap = None
        self.photo_scale = 1.0
        self.recognized_xml: Path | None = None
        self.temp = tempfile.TemporaryDirectory(prefix="jianpu-studio-")
        self.pdf_buffer = None
        self.work = Path(self.temp.name)
        self.job_number = 0
        self.data_dir = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation))
        self.recovery_path = self.data_dir / "recovery.jps"
        self.autopreview = QTimer(self)
        self.autopreview.setSingleShot(True)
        self.autopreview.setInterval(1200)
        self.autopreview.timeout.connect(lambda: self.render_score(False))
        self.build_ui()
        self.build_menus()
        self.sync()
        self.input_mode.setCurrentIndex(self.input_mode.findData("edit"))
        self.autosave = QTimer(self)
        self.autosave.setInterval(15000)
        self.autosave.timeout.connect(self.write_recovery)
        self.autosave.start()
        if recovery and self.recovery_path.is_file():
            QTimer.singleShot(0, self.restore_recovery)
        else:
            QTimer.singleShot(250, lambda: self.render_score(False))

    def build_ui(self):
        build_workspace(self, label, button)

    def build_menus(self):
        def action(menu, text, fn, shortcut=None):
            a = QAction(text, self)
            a.triggered.connect(fn)
            if shortcut:
                a.setShortcut(shortcut)
            menu.addAction(a)
            return a
        file_menu = self.menuBar().addMenu("檔案")
        action(file_menu, "新建", self.new_project, QKeySequence.StandardKey.New)
        action(file_menu, "開啟…", self.open_file, QKeySequence.StandardKey.Open)
        action(file_menu, "開啟「小星星」範例", self.load_demo)
        action(file_menu, "儲存", self.save, QKeySequence.StandardKey.Save)
        action(file_menu, "另存新檔…", self.save_as, QKeySequence.StandardKey.SaveAs)
        file_menu.addSeparator()
        action(file_menu, "匯入 MusicXML…", self.import_musicxml)
        action(file_menu, "匯出 PDF…", lambda: self.export("pdf"), "Ctrl+E")
        action(file_menu, "離開", self.close, "Alt+F4")
        edit = self.menuBar().addMenu("編輯")
        self.undo_action = action(edit, "復原", self.undo, QKeySequence.StandardKey.Undo)
        self.redo_action = action(edit, "重做", self.redo, QKeySequence.StandardKey.Redo)
        measures = self.menuBar().addMenu("小節")
        measures.aboutToShow.connect(lambda: (measures.clear(), self.add_measure_menu(measures)))
        notation = self.notation_menu()
        notation.setTitle("記號")
        self.menuBar().addMenu(notation)
        view = self.menuBar().addMenu("檢視")
        action(view, "編輯樂譜", lambda: self.change_workspace(0))
        tools_menu = self.menuBar().addMenu("工具")
        action(tools_menu, "列印預覽", self.show_print_preview, "F5")
        action(tools_menu, "作業訊息", self.show_work_log)
        action(tools_menu, "工具與環境設定", self.tool_settings)
        help_menu = self.menuBar().addMenu("說明")
        action(help_menu, "操作說明", self.show_help, "F1")
        action(help_menu, "檢查更新…", self.check_updates)
        action(help_menu, "關於", lambda: QMessageBox.about(self, "關於簡譜工房", f"簡譜工房 {__version__}\nMIT\n\n排譜：GNU LilyPond\n轉換：jianpu-ly · Silas S. Brown (Apache-2.0)\n辨識：Audiveris (AGPL-3.0)\n介面：PySide6 / Qt (LGPL-3.0)\n\nhttps://github.com/miles990/jianpu-studio"))

    def current_tool(self, name):
        return find_tool(name, str(self.settings.value(name, "")))

    def switch_mode(self):
        if self.task and self.task_blocks_edit:
            return
        if self.print_preview_active:
            self.change_workspace(0)
        if self.project.mode == "visual":
            try:
                source = self.project.score.to_jianpu()
            except ValueError as exc:
                self.error("請先完成小節", exc)
                return
            if self.project.source and self.project.source != source:
                answer = QMessageBox.question(self, "繼續進階原始碼", "專案保留了上次的進階原始碼。\n\n按「是」繼續原始碼；按「否」使用目前視覺草稿重新產生（取代舊原始碼）。", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Yes)
                if answer == QMessageBox.StandardButton.Cancel:
                    return
                if answer == QMessageBox.StandardButton.Yes:
                    source = self.project.source
            def update(p):
                p.source = source
                p.mode = "source"
            self.mutate(update)
        else:
            answer = QMessageBox.question(self, "返回視覺草稿", "進階原始碼不能自動還原成視覺音符。\n返回後將使用切換前的視覺草稿；進階原始碼仍保留在專案內。\n\n確定返回？", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if answer == QMessageBox.StandardButton.Yes:
                self.mutate(lambda p: setattr(p, "mode", "visual"))

    def guard_unsaved(self) -> bool:
        if self.task and self.task_blocks_edit:
            self.statusBar().showMessage("請先等待作業完成，或按「取消作業」。", 5000)
            return False
        self.capture_fields()
        if not self.dirty:
            return True
        answer = QMessageBox.question(self, "尚未儲存", "要先儲存目前的樂譜嗎？", QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Save)
        if answer == QMessageBox.StandardButton.Save:
            return self.save()
        return answer == QMessageBox.StandardButton.Discard

    def reset(self, project: Project, path: Path | None = None, dirty=False):
        if self.task and not self.task_blocks_edit:
            self.task.cancel.set()
        self.autopreview.stop()
        self.render_pending = self.render_pending_manual = self.render_pending_pdf = False
        self.render_cancelled = False
        self.play_after_render = False
        self.output_request = None
        self.player.stop()
        self.project, self.path, self.dirty = project, path, dirty
        self.next_note = Note()
        self.loading = True
        self.input_mode.setCurrentIndex(0)
        self.insert_position.setCurrentIndex(0)
        self.loading = False
        self.revision += 1
        self.history.clear()
        self.future.clear()
        self.result = None
        self.rendered_revision = -1
        self.recognized_xml = None
        self.clear_pdf_preview()
        self.photo_label.clear()
        self.photo_pixmap = None
        self.photo_scale = 1.0
        self.photo_label.setText("匯入照片後，可在這裡對照原圖。")
        self.measure_notice.clear()
        self.measure_notice.hide()
        if self.print_preview_active:
            self.change_workspace(0)
        self.recovery_path.unlink(missing_ok=True)
        self.sync(selection=[], current=-1)
        if self.print_preview_active and self.auto_check.isChecked():
            self.autopreview.start()

    def new_project(self):
        if self.guard_unsaved():
            self.reset(Project())

    def load_demo(self):
        if self.guard_unsaved():
            self.reset(demo_project())
            self.input_mode.setCurrentIndex(self.input_mode.findData("edit"))

    def open_file(self, checked=False, path: Path | None = None):
        if not self.guard_unsaved():
            return
        if path is None:
            name, _ = QFileDialog.getOpenFileName(self, "開啟樂譜", "", "樂譜專案 (*.jps);;簡譜文字 (*.jly *.txt)")
            if not name:
                return
            path = Path(name)
        try:
            if path.suffix.lower() == ".jps":
                self.reset(load_project(path), path)
                self.input_mode.setCurrentIndex(self.input_mode.findData("edit"))
            else:
                if path.stat().st_size > MAX_FILE_BYTES:
                    raise ValueError("樂譜檔超過 4 MB。")
                source = path.read_text(encoding="utf-8-sig")
                self.reset(Project(Score(title=path.stem), "source", source), dirty=True)
        except Exception as exc:
            self.error("無法開啟樂譜", exc)

    def save(self, checked=False) -> bool:
        self.capture_fields()
        if self.path is None:
            return self.save_as()
        try:
            save_project(self.path, self.project)
            self.dirty = False
            self.recovery_path.unlink(missing_ok=True)
            self.sync()
            self.statusBar().showMessage(f"已儲存：{self.path}", 6000)
            return True
        except Exception as exc:
            self.error("儲存失敗，原檔未被覆蓋", exc)
            return False

    def save_as(self, checked=False) -> bool:
        name, _ = QFileDialog.getSaveFileName(self, "儲存專案", str(self.path or Path(self.project.score.title + ".jps")), "簡譜工房專案 (*.jps)")
        if not name:
            return False
        old_path = self.path
        self.path = Path(name) if Path(name).suffix else Path(name + ".jps")
        if self.save():
            return True
        self.path = old_path
        return False

    def write_recovery(self):
        if self.dirty:
            try:
                save_project(self.recovery_path, self.project)
            except OSError:
                self.statusBar().showMessage("自動復原暫存失敗，請手動儲存專案。", 5000)

    def restore_recovery(self):
        try:
            recovered = load_project(self.recovery_path)
        except Exception:
            self.statusBar().showMessage("找到無法讀取的復原檔，請從原專案開啟。", 8000)
            return
        if QMessageBox.question(self, "復原未儲存樂譜", "上次有未儲存的樂譜，現在復原嗎？") == QMessageBox.StandardButton.Yes:
            self.reset(recovered, dirty=True)
        else:
            self.recovery_path.unlink(missing_ok=True)
            self.autopreview.start()

    def job_dir(self) -> Path:
        self.job_number += 1
        path = self.work / str(self.job_number)
        path.mkdir()
        return path

    def start_task(self, title, fn, callback, show_error=True, *, blocks_edit=True, revision=None):
        if self.task:
            if blocks_edit and not self.task_blocks_edit:
                self.pending_task = (title, fn, callback, show_error)
                self.task.cancel.set()
                self.task_blocks_edit = True
                self.editor.setEnabled(False)
            return
        self.autopreview.stop()
        task = Task(fn, self)
        task.revision = revision
        self.task = task
        self.task_blocks_edit = blocks_edit
        self.editor.setEnabled(not blocks_edit)
        self.preview_button.setEnabled(not blocks_edit)
        self.progress.show()
        self.cancel_button.show()
        self.statusBar().showMessage(title)
        def finish(value, error):
            task.wait()
            self.task = None
            self.task_blocks_edit = False
            task.deleteLater()
            self.editor.setEnabled(True)
            self.preview_button.setEnabled(True)
            self.progress.hide()
            self.cancel_button.hide()
            stale = revision is not None and revision != self.revision
            if error is not None and not stale and not self.close_requested:
                self.play_after_render = False
                self.output_request = None
                self.preview_status.setText("作業已取消。" if isinstance(error, Cancelled) else "未完成；請查看訊息並修正。")
                self.log.setPlainText(str(error) + "\n\n" + getattr(error, "details", ""))
                self.statusBar().showMessage((str(error) or "作業未完成").splitlines()[0][:180], 10000)
                if show_error and not isinstance(error, Cancelled):
                    self.error("作業未完成", error)
            elif error is None and not self.close_requested:
                callback(value)
                if not stale:
                    self.statusBar().showMessage("作業完成", 5000)
            if self.close_requested:
                self.pending_task = None
                self.render_pending = False
                QTimer.singleShot(0, self.close)
            elif self.pending_task:
                pending, self.pending_task = self.pending_task, None
                self.start_task(*pending)
            elif self.render_pending:
                manual = self.render_pending_manual
                create_pdf = self.render_pending_pdf
                self.render_pending = self.render_pending_manual = False
                self.render_pending_pdf = False
                if manual or self.auto_check.isChecked():
                    QTimer.singleShot(0, lambda: self.render_score(manual, create_pdf=create_pdf))
            elif stale and self.print_preview_active and self.auto_check.isChecked() and not self.autopreview.isActive() and not self.render_cancelled:
                self.autopreview.start()
        task.completed.connect(finish)
        task.start()

    def cancel_task(self):
        self.autopreview.stop()
        self.render_pending = self.render_pending_manual = False
        self.render_pending_pdf = False
        self.pending_task = None
        self.play_after_render = False
        self.output_request = None
        self.render_cancelled = True
        if self.task:
            self.task.cancel.set()
            self.statusBar().showMessage("正在取消…")

    def render_score(self, manual=True, *, create_pdf=True):
        if self.update_preparing or self.close_preparing:
            if manual:
                self.render_pending = self.render_pending_manual = True
                self.render_pending_pdf = self.render_pending_pdf or create_pdf
            return
        if not manual and not self.print_preview_active:
            return
        self.render_cancelled = False
        self.capture_fields()
        if self.task:
            if not self.task_blocks_edit:
                if self.task.revision != self.revision or (create_pdf and not getattr(self.task, "creates_pdf", True)):
                    self.render_pending = True
                    self.render_pending_manual = self.render_pending_manual or manual
                    self.render_pending_pdf = self.render_pending_pdf or create_pdf
                    self.statusBar().showMessage("已排入最新版本；目前排版完成後會更新。", 4000)
            return
        try:
            source = self.project.notation()
        except ValueError as exc:
            self.play_after_render = False
            self.output_request = None
            self.preview_status.setText(str(exc))
            if manual:
                self.error("請先完成樂譜", exc)
            return
        lilypond = self.current_tool("lilypond")
        if not lilypond:
            self.play_after_render = False
            self.output_request = None
            self.preview_status.setText("尚未找到 LilyPond。請使用完整版安裝檔，或在「工具與環境設定」指定位置。")
            if manual:
                self.tool_settings()
            return
        revision = self.revision
        paper_size, staff_size = self.project.score.paper_size, self.project.score.staff_size
        output = self.job_dir()
        self.preview_status.setText("正在準備列印排版…" if create_pdf else "正在準備試聽 / MIDI…")
        def complete(result):
            if revision != self.revision:
                shutil.rmtree(result.pdf.parent, ignore_errors=True)
                return
            old_result = self.result
            self.result = result
            self.rendered_revision = revision
            self.clear_pdf_preview()
            if create_pdf:
                self.load_pdf_preview(result.pdf)
            if old_result and old_result.pdf.parent != result.pdf.parent:
                shutil.rmtree(old_result.pdf.parent, ignore_errors=True)
            self.preview_status.setText(f"排版完成 · {self.pdf_document.pageCount()} 頁 · 可列印 / 匯出 PDF" if create_pdf else "MIDI 已準備完成。")
            self.log.setPlainText(result.log)
            if self.play_after_render:
                self.play_after_render = False
                self.play_midi()
            if self.output_request:
                action, kind = self.output_request
                # A MIDI job may complete before a queued PDF request.
                if kind != "pdf" or result.pdf.is_file():
                    self.output_request = None
                    if action == "export":
                        self.export(kind)
                    else:
                        self.open_pdf()
        def job(cancel):
            try:
                return render(source, output, lilypond, cancel, paper_size=paper_size, staff_size=staff_size, create_pdf=create_pdf)
            except Exception:
                shutil.rmtree(output, ignore_errors=True)
                raise
        self.start_task("正在準備列印 PDF；可繼續編輯…" if create_pdf else "正在準備 MIDI 試聽資料…", job, complete, manual, blocks_edit=False, revision=revision)
        if self.task:
            self.task.creates_pdf = create_pdf

    def clear_pdf_preview(self):
        self.pdf_document.close()
        if self.pdf_buffer is not None:
            self.pdf_buffer.close()
            self.pdf_buffer.deleteLater()
            self.pdf_buffer = None

    def load_pdf_preview(self, path):
        # Qt can keep a filename-backed PDF open while queued pages render.
        # Loading an owned memory buffer lets Windows release the job directory
        # immediately, including when a preview is replaced or the app closes.
        payload = path.read_bytes()
        self.clear_pdf_preview()
        buffer = QBuffer(self.pdf_document)
        buffer.setData(payload)
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        self.pdf_buffer = buffer
        self.pdf_document.load(buffer)
        if self.pdf_document.pageCount() < 1:
            self.clear_pdf_preview()
            raise ValueError("無法開啟產生的 PDF 預覽，請查看排版紀錄並重新排版。")

    def resume_pending_output(self):
        if self.task or self.update_preparing or self.close_preparing:
            return
        if self.output_request or (self.render_pending and self.render_pending_manual):
            create_pdf = self.render_pending_pdf or (self.output_request is not None and self.output_request[1] == "pdf")
            self.render_pending = self.render_pending_manual = self.render_pending_pdf = False
            self.render_score(True, create_pdf=create_pdf)

    def fresh_result(self):
        self.capture_fields()
        if not self.result or self.rendered_revision != self.revision:
            self.statusBar().showMessage("請使用「匯出 / 列印」或試聽；需要時會自動準備輸出。", 6000)
            return None
        return self.result

    def export(self, kind):
        try:
            self.capture_fields()
            if kind == "jly":
                self.capture_fields()
                content, suffix, description = self.project.notation(), ".jly", "簡譜文字"
                source = None
            elif kind == "musicxml":
                if not self.recognized_xml:
                    raise ValueError("目前沒有照片辨識產生的 MusicXML。請先匯入照片。")
                source, content = self.recognized_xml, None
                suffix, description = source.suffix, "MusicXML 辨識結果"
            else:
                result = self.result
                available = result is not None and self.rendered_revision == self.revision
                if available:
                    file = {"pdf": result.pdf, "midi": result.midi, "ly": result.lilypond}[kind]
                    available = file is not None and file.is_file()
                if not available:
                    self.output_request = ("export", kind)
                    self.render_score(True, create_pdf=kind == "pdf")
                    return
                source = {"pdf": result.pdf, "midi": result.midi, "ly": result.lilypond}[kind]
                if not source:
                    raise ValueError("這份樂譜未產生 MIDI。")
                suffix, description, content = source.suffix, kind.upper(), None
            # Snapshot before the native dialog runs its nested event loop.
            # A completed render may remove the previous result directory.
            payload = content if content is not None else source.read_bytes()
            export_revision = self.revision
            name, _ = QFileDialog.getSaveFileName(self, f"匯出 {description}", self.project.score.title + suffix, f"{description} (*{suffix})")
            if name:
                path = Path(name) if Path(name).suffix else Path(name + suffix)
                if self.revision != export_revision:
                    self.statusBar().showMessage("樂譜在選擇檔名期間已改變，請重新匯出最新內容。", 8000)
                    return
                atomic_write(path, payload)
                self.statusBar().showMessage(f"已匯出：{path}", 6000)
        except Exception as exc:
            self.error("無法匯出", exc)

    def open_pdf(self):
        self.capture_fields()
        result = self.result
        if not result or self.rendered_revision != self.revision or not result.pdf.is_file():
            self.output_request = ("open", "pdf")
            self.render_score(True)
            return
        from .printing import prepare_print_copy
        try:
            copy = prepare_print_copy(result.pdf, self.data_dir / "print-cache")
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(copy))):
                self.error("無法開啟 PDF", "請設定 Windows 的預設 PDF 閱讀器，或先匯出 PDF。")
        except OSError as exc:
            self.error("無法開啟 PDF", exc)

    def play_midi(self):
        if self.player.playing:
            self.player.stop()
            return
        if self.play_after_render:
            self.play_after_render = False
            self.statusBar().showMessage("已取消排版完成後的試聽。", 4000)
            return
        self.capture_fields()
        if not self.result or self.rendered_revision != self.revision:
            self.play_after_render = True
            self.render_score(True, create_pdf=False)
            return
        result = self.result
        if result and result.midi:
            try:
                self.player.play(result.midi)
            except RuntimeError as exc:
                self.error("無法播放 MIDI", exc)

    def fit_pdf(self):
        self.pdf_view.setZoomMode(QPdfView.ZoomMode.FitToWidth)

    def zoom(self, factor):
        zoom = self.pdf_view.zoomFactor() * factor
        self.pdf_view.setZoomMode(QPdfView.ZoomMode.Custom)
        self.pdf_view.setZoomFactor(max(0.2, min(4.0, zoom)))

    def import_musicxml(self):
        if not self.guard_unsaved():
            return
        name, _ = QFileDialog.getOpenFileName(self, "匯入 MusicXML", "", "MusicXML (*.musicxml *.xml *.mxl)")
        if not name:
            return
        output = self.job_dir()
        self.start_task("正在匯入 MusicXML…", lambda cancel: self.convert_xml(Path(name), output, cancel), self.import_complete)

    @staticmethod
    def convert_xml(path, output, cancel):
        xml = read_xml(path)
        project, message = visual_import(xml)
        if project is None:
            target = output / "imported.jly"
            try:
                run_tool(worker_command("import", str(path), str(target)), output, cancel, env=worker_env())
            except Exception:
                error = target.with_suffix(".error.txt")
                if error.is_file():
                    raise ValueError(error.read_text(encoding="utf-8")) from None
                raise
            project = Project(Score(title="匯入樂譜"), "source", target.read_text(encoding="utf-8"))
        return project, message

    def import_complete(self, result):
        project, message = result
        self.reset(project, dirty=True)
        self.log.setPlainText(message)
        self.preview_status.setText(message)
        QMessageBox.information(self, "匯入完成 · 請校對", message)

    def import_photo(self):
        if not self.guard_unsaved():
            return
        audiveris = self.current_tool("audiveris")
        if not audiveris:
            self.error("尚未找到照片辨識工具", "完整版安裝檔已附 Audiveris 與 Java。若使用原始碼啟動，請在工具設定指定 Audiveris 執行檔。")
            self.tool_settings()
            return
        name, _ = QFileDialog.getOpenFileName(self, "選擇五線譜照片", "", "樂譜照片 (*.png *.jpg *.jpeg *.tif *.tiff *.bmp)")
        if not name:
            return
        try:
            dialog = PhotoDialog(Path(name), self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            output = self.job_dir()
            prepared = output / "photo.png"
            dialog.save(prepared)
        except Exception as exc:
            self.error("無法讀取照片", exc)
            return
        def job(cancel):
            xml, log = recognize(prepared, output / "recognition", audiveris, cancel)
            project, message = self.convert_xml(xml, output, cancel)
            return project, message, xml, log
        def complete(result):
            project, message, xml, log = result
            self.reset(project, dirty=True)
            self.recognized_xml = xml
            self.photo_pixmap = QPixmap(str(prepared))
            self.workspace_view.setCurrentIndex(1)
            self.zoom_photo(0)
            self.log.setPlainText(message + "\n\n" + log)
            self.preview_status.setText("照片已轉為簡譜。請對照原圖核對調號、音高、拍數與反覆記號。")
            QMessageBox.information(self, "辨識完成 · 請校對", message + "\n\n辨識可能有誤，請使用「原圖對照」逐小節校對。可匯出 MusicXML，在 Audiveris 等工具進一步修正。")
        self.start_task("正在辨識五線譜照片…通常需要 1–5 分鐘，可隨時取消。", job, complete)

    def tool_settings(self):
        if self.task:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("工具與環境設定")
        dialog.resize(720, 280)
        layout = QVBoxLayout(dialog)
        layout.addWidget(label("完整版會自動找到內附工具。通常不需要更改；清空欄位可恢復自動偵測。", wrap=True))
        form = QFormLayout()
        fields = {}
        for name, display in (("lilypond", "LilyPond 排譜引擎"),):
            row = QHBoxLayout()
            field = QLineEdit(str(self.settings.value(name, "")))
            found = self.current_tool(name)
            field.setPlaceholderText(str(found) if found else "尚未偵測到")
            fields[name] = field
            row.addWidget(field)
            def browse(_, target=field):
                path, _ = QFileDialog.getOpenFileName(dialog, "選取執行檔", "", "執行檔 (*.exe);;所有檔案 (*)")
                if path:
                    target.setText(path)
            row.addWidget(button("瀏覽…", browse))
            form.addRow(display, row)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            for name, field in fields.items():
                self.settings.setValue(name, field.text().strip())
            self.autopreview.start()

    def open_audiveris(self):
        import subprocess
        tool = self.current_tool("audiveris")
        if not tool:
            self.tool_settings()
            return
        try:
            subprocess.Popen([str(tool)], cwd=tool.parent)
        except OSError as exc:
            self.error("無法開啟 Audiveris", exc)

    def show_help(self):
        QMessageBox.information(self, "快速上手 / 快捷鍵", "小節：點小節號選取；＋新增小節，或用小節選單前插／後插／曲尾新增。清空保留拍數，刪除會讓後段前移。\n填寫小節：點休止符 0，用數字取代休止，保留後面小節位置。\n新增模式：0–7 輸入音符；屬性列設定下一音符。可選擇插在選取前、後或曲尾。\n修改模式：先選音符，再調整屬性；只改動那個屬性。混合值顯示「不同」。\n\n音符區快捷鍵（相容部分 EOP 習慣）：\nSpace 切換新增 / 修改，Enter 試聽 / 停止。\nQ / W / E / R / A / S：全、二分、四分、八分、十六分、三十二分音符。\n↑ / ↓ 八度，. 附點，[ / ] 縮短 / 延長。\nShift 連選、Ctrl 加選、Ctrl+A 全選，M 選小節。\nCtrl+C / X / V 複製 / 剪下 / 貼上，Ctrl+D 重複，Delete 刪除。\nInsert 前插小節、Ctrl+Insert 新增多節、Ctrl+B 曲尾加小節、Ctrl+Delete 刪除小節。\nCtrl+Z 復原，Ctrl+Y 重做，Ctrl+S 儲存，F5 列印預覽。\n平常在同一張主譜面閱讀與編輯。PDF 只在列印／匯出時產生，PDF 本身不能直接編輯音符。\n\n「記號 / 二胡」可設定力度、延長、常用演奏記號、前後倚音、指法、弓法、滑音。連續選取可加連音 / 延音 / 漸強弱 / 小節反覆。\n三連音：選一個未附點音符或休止符，用「記號」選單拆成三份，總拍數不變。進階語法另支援弱起、多聲部等；返回時保留視覺草稿。\n\n每 15 秒儲存本機復原暫存。MIDI 試聽目前使用 Windows 內建合成器。")

    def check_updates(self):
        from .update_dialog import UpdateDialog
        UpdateDialog(self).exec()

    def error(self, title, error):
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(title)
        box.setTextFormat(Qt.TextFormat.PlainText)
        message = str(error)
        details = getattr(error, "details", message)
        box.setText(message[:400])
        if details != message or len(message) > 400:
            box.setDetailedText(details)
            copy_button = box.addButton("複製完整紀錄", QMessageBox.ButtonRole.ActionRole)
            copy_button.clicked.connect(lambda: QApplication.clipboard().setText(message + "\n\n" + details))
        box.exec()

    def closeEvent(self, event):
        if self.task:
            self.close_requested = True
            self.cancel_task()
            self.statusBar().showMessage("正在取消作業，完成後關閉視窗…")
            event.ignore()
            return
        self.close_requested = False
        self.close_preparing = True
        self.autopreview.stop()
        if not self.updating and not self.guard_unsaved():
            self.close_preparing = False
            QTimer.singleShot(0, self.resume_pending_output)
            if self.auto_check.isChecked() and self.dirty:
                self.autopreview.start()
            event.ignore()
            return
        self.autosave.stop()
        self.player.stop()
        self.autopreview.stop()
        self.recovery_path.unlink(missing_ok=True)
        self.clear_pdf_preview()
        self.temp.cleanup()
        event.accept()
