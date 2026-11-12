from __future__ import annotations

import copy
import shutil
import tempfile
import threading
from dataclasses import asdict
from fractions import Fraction
from pathlib import Path

from PySide6.QtCore import QSettings, QSize, QStandardPaths, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QFont, QKeySequence, QPixmap
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .engine import Cancelled, find_tool, recognize, render, run_tool, worker_command, worker_env
from .model import (
    DURATIONS,
    KEYS,
    MAX_FILE_BYTES,
    METERS,
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
from .widgets import STYLE, NoteList


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


class MainWindow(QMainWindow):
    def __init__(self, recovery: bool = True):
        super().__init__()
        self.setMinimumSize(940, 600)
        self.resize(1480, 940)
        self.setStyleSheet(STYLE)
        self.settings = QSettings("JianpuStudio", "JianpuStudio")
        self.project = demo_project()
        self.path: Path | None = None
        self.dirty = False
        self.loading = False
        self.history: list[dict] = []
        self.future: list[dict] = []
        self.revision = 0
        self.rendered_revision = -1
        self.result = None
        self.player = MidiPlayer()
        self.task: Task | None = None
        self.recognized_xml: Path | None = None
        self.temp = tempfile.TemporaryDirectory(prefix="jianpu-studio-")
        self.work = Path(self.temp.name)
        self.job_number = 0
        self.data_dir = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation))
        self.recovery_path = self.data_dir / "recovery.jps"
        self.autopreview = QTimer(self)
        self.autopreview.setSingleShot(True)
        self.autopreview.setInterval(1000)
        self.autopreview.timeout.connect(lambda: self.render_score(False))
        self.build_ui()
        self.build_menus()
        self.sync()
        self.autosave = QTimer(self)
        self.autosave.setInterval(15000)
        self.autosave.timeout.connect(self.write_recovery)
        self.autosave.start()
        if recovery and self.recovery_path.is_file():
            QTimer.singleShot(0, self.restore_recovery)
        else:
            QTimer.singleShot(250, lambda: self.render_score(False))

    def build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        sidebar = QWidget()
        self.sidebar = sidebar
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(180)
        nav = QVBoxLayout(sidebar)
        nav.setContentsMargins(20, 30, 20, 22)
        nav.addWidget(label("1  2  3", "brand"))
        nav.addSpacing(6)
        nav.addWidget(label("簡譜工房", "brand"))
        nav.addWidget(label("JIANPU STUDIO"))
        nav.addSpacing(35)
        nav.addWidget(button("＋  新建樂譜", self.new_project))
        nav.addWidget(button("↗  開啟專案", self.open_file))
        nav.addWidget(button("▧  照片轉簡譜", self.import_photo))
        nav.addWidget(button("♫  匯入 MusicXML", self.import_musicxml))
        nav.addSpacing(26)
        nav.addWidget(label("從一個音符開始"))
        nav.addWidget(label("① 點選下方數字加入音符\n② 選取音符調整時值\n③ 看右側排版並匯出", wrap=True))
        nav.addStretch()
        nav.addWidget(button("範例：小星星", self.load_demo))
        nav.addWidget(button("工具與環境設定", self.tool_settings))
        nav.addWidget(button("操作說明", self.show_help))
        nav.addSpacing(10)
        nav.addWidget(label(f"v{__version__}  ·  離線創作"))
        root.addWidget(sidebar)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(24, 20, 24, 12)
        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.addWidget(label("YOUR MELODY, BEAUTIFULLY WRITTEN", "eyebrow"))
        title_box.addWidget(label("把旋律，寫成簡譜。", "hero"))
        header.addLayout(title_box)
        header.addStretch()
        self.save_button = button("儲存專案", self.save)
        header.addWidget(self.save_button)
        self.export_button = QPushButton("匯出  ▾")
        self.export_button.setObjectName("primary")
        export_menu = QMenu(self)
        for text, kind in (("PDF 樂譜…", "pdf"), ("MIDI 音樂…", "midi"), ("簡譜文字 .jly…", "jly"), ("LilyPond 原始碼…", "ly"), ("辨識結果 MusicXML…", "musicxml")):
            export_menu.addAction(text, lambda k=kind: self.export(k))
        self.export_button.setMenu(export_menu)
        header.addWidget(self.export_button)
        body_layout.addLayout(header)
        body_layout.addSpacing(12)
        split = QSplitter(Qt.Orientation.Horizontal)
        self.editor = QWidget()
        edit_layout = QVBoxLayout(self.editor)
        edit_layout.setContentsMargins(0, 0, 16, 0)
        edit_layout.setSpacing(12)
        row = QHBoxLayout()
        row.addWidget(label("01  編輯樂譜", "section"))
        row.addStretch()
        self.mode_button = button("切換進階原始碼", self.switch_mode)
        row.addWidget(self.mode_button)
        edit_layout.addLayout(row)
        self.editor_stack = QStackedWidget()
        visual = QWidget()
        vis = QVBoxLayout(visual)
        vis.setContentsMargins(0, 0, 0, 0)
        grid = QGridLayout()
        self.title_field = QLineEdit()
        self.title_field.setAccessibleName("曲名")
        self.composer_field = QLineEdit()
        self.composer_field.setPlaceholderText("作曲者 / 來源")
        grid.addWidget(label("曲名"), 0, 0)
        grid.addWidget(self.title_field, 0, 1, 1, 3)
        grid.addWidget(self.composer_field, 0, 4, 1, 2)
        self.key_field = QComboBox()
        self.key_field.addItems(KEYS)
        self.meter_field = QComboBox()
        self.meter_field.addItems(METERS)
        self.tempo_field = QSpinBox()
        self.tempo_field.setRange(20, 300)
        self.tempo_field.setSuffix(" BPM")
        for col, text, widget in ((0, "1 =", self.key_field), (2, "拍號", self.meter_field), (4, "速度", self.tempo_field)):
            grid.addWidget(label(text), 1, col)
            grid.addWidget(widget, 1, col + 1)
        vis.addLayout(grid)
        self.title_field.editingFinished.connect(self.capture_fields)
        self.composer_field.editingFinished.connect(self.capture_fields)
        self.key_field.currentTextChanged.connect(self.capture_fields)
        self.meter_field.currentTextChanged.connect(self.capture_fields)
        self.tempo_field.valueChanged.connect(self.capture_fields)
        vis.addWidget(label("點選音符可修改；選取後加入的音符會插在它後面。", "muted", True))
        self.notes = NoteList()
        self.notes.pitch.connect(self.add_note)
        self.notes.delete.connect(self.delete_note)
        self.notes.currentRowChanged.connect(self.select_note)
        vis.addWidget(self.notes, 1)
        row = QHBoxLayout()
        for degree in range(1, 8):
            row.addWidget(button(str(degree), lambda _, d=degree: self.add_note(d), "pitch"))
        row.addWidget(button("0", lambda: self.add_note(0), "pitch"))
        vis.addLayout(row)
        row = QHBoxLayout()
        self.degree = QComboBox()
        for value in range(8):
            self.degree.addItem("休止 0" if value == 0 else f"音高 {value}", value)
        self.degree.setCurrentIndex(1)
        row.addWidget(self.degree)
        self.duration = QComboBox()
        for value, text in DURATIONS.items():
            self.duration.addItem(text, value)
        self.duration.setCurrentIndex(2)
        self.octave = QComboBox()
        for value, text in ((-2, "低兩個八度"), (-1, "低八度"), (0, "中音"), (1, "高八度"), (2, "高兩個八度")):
            self.octave.addItem(text, value)
        self.octave.setCurrentIndex(2)
        self.accidental = QComboBox()
        for text, value in (("無升降", ""), ("升 ♯", "#"), ("降 ♭", "b")):
            self.accidental.addItem(text, value)
        self.dotted = QCheckBox("附點")
        row.addWidget(self.duration, 2)
        row.addWidget(self.octave)
        row.addWidget(self.accidental)
        row.addWidget(self.dotted)
        vis.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(button("套用到選取音符", self.apply_note))
        row.addWidget(button("複製", self.duplicate_note))
        row.addWidget(button("刪除", self.delete_note))
        row.addWidget(button("移前", lambda: self.move_note(-1)))
        row.addWidget(button("移後", lambda: self.move_note(1)))
        vis.addLayout(row)
        row = QHBoxLayout()
        self.rhythm_label = label("", "muted", True)
        row.addWidget(self.rhythm_label, 1)
        row.addWidget(button("補齊休止符", self.fill_bar))
        vis.addLayout(row)
        self.lyrics = QLineEdit()
        self.lyrics.setPlaceholderText("歌詞（可不填）：中文字自動按字配音；多音同字可用進階模式調整")
        self.lyrics.editingFinished.connect(self.capture_fields)
        vis.addWidget(self.lyrics)
        self.staff = QCheckBox("同時顯示五線譜，方便對照")
        self.staff.toggled.connect(self.capture_fields)
        vis.addWidget(self.staff)
        self.editor_stack.addWidget(visual)
        advanced = QWidget()
        advanced_layout = QVBoxLayout(advanced)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        advanced_layout.addWidget(label("進階模式保留多聲部與特殊記號。此模式直接編輯 jianpu-ly 語法。\n1 2 3 4 是四分音符；q1 是八分；1 - 是二分；1' 是高八度。", "notice", True))
        self.source = QPlainTextEdit()
        self.source.setFont(QFont("Consolas", 12))
        self.source.setAccessibleName("簡譜原始碼")
        self.source.textChanged.connect(self.source_changed)
        advanced_layout.addWidget(self.source, 1)
        advanced_layout.addWidget(button("開啟完整語法文件", lambda: QDesktopServices.openUrl(QUrl("https://github.com/ssb22/jianpu-ly#jianpu-ly"))))
        self.editor_stack.addWidget(advanced)
        edit_layout.addWidget(self.editor_stack, 1)
        editor_scroll = QScrollArea()
        editor_scroll.setWidgetResizable(True)
        editor_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        editor_scroll.setWidget(self.editor)
        split.addWidget(editor_scroll)
        preview = QWidget()
        preview_layout = QVBoxLayout(preview)
        preview_layout.setContentsMargins(16, 0, 0, 0)
        row = QHBoxLayout()
        row.addWidget(label("02  樂譜預覽", "section"))
        row.addStretch()
        self.preview_button = button("更新排版", lambda: self.render_score(True), "primary")
        row.addWidget(self.preview_button)
        preview_layout.addLayout(row)
        self.preview_status = label("準備排版…", "muted", True)
        preview_layout.addWidget(self.preview_status)
        self.preview_tabs = QTabWidget()
        pdf_page = QWidget()
        pdf_layout = QVBoxLayout(pdf_page)
        pdf_layout.setContentsMargins(0, 8, 0, 0)
        row = QHBoxLayout()
        row.addWidget(button("−", lambda: self.zoom(0.8)))
        row.addWidget(button("符合寬度", self.fit_pdf))
        row.addWidget(button("＋", lambda: self.zoom(1.25)))
        row.addStretch()
        self.play_button = button("▶ 試聽 / 停止", self.play_midi)
        self.play_button.setToolTip("使用 Windows 內建 MIDI 合成器試聽")
        row.addWidget(self.play_button)
        row.addWidget(button("開啟 / 列印 ↗", self.open_pdf))
        pdf_layout.addLayout(row)
        self.pdf_document = QPdfDocument(self)
        self.pdf_view = QPdfView()
        self.pdf_view.setDocument(self.pdf_document)
        self.pdf_view.setPageMode(QPdfView.PageMode.MultiPage)
        self.pdf_view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        self.pdf_view.setDocumentMargins(__import__("PySide6.QtCore", fromlist=["QMargins"]).QMargins(14, 16, 14, 16))
        pdf_layout.addWidget(self.pdf_view, 1)
        self.preview_tabs.addTab(pdf_page, "正式簡譜")
        self.photo_label = QLabel("匯入照片後，可在這裡對照原圖。")
        self.photo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.photo_label.setWordWrap(True)
        photo_scroll = QScrollArea()
        photo_scroll.setWidgetResizable(True)
        photo_scroll.setWidget(self.photo_label)
        self.preview_tabs.addTab(photo_scroll, "照片原圖")
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        log_page = QWidget()
        log_layout = QVBoxLayout(log_page)
        log_layout.addWidget(self.log)
        log_layout.addWidget(button("複製完整紀錄", lambda: QApplication.clipboard().setText(self.log.toPlainText())))
        self.preview_tabs.addTab(log_page, "排版 / 辨識訊息")
        preview_layout.addWidget(self.preview_tabs, 1)
        preview_layout.addWidget(label("排版引擎  LilyPond × jianpu-ly   ·   照片辨識  Audiveris", "muted"))
        split.addWidget(preview)
        split.setSizes([620, 590])
        body_layout.addWidget(split, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.hide()
        body_layout.addWidget(self.progress)
        root.addWidget(body, 1)
        self.cancel_button = button("取消作業", self.cancel_task)
        self.cancel_button.hide()
        self.statusBar().addPermanentWidget(self.cancel_button)
        self.statusBar().showMessage("就緒 · 點選音符區後，可用數字鍵 0–7 輸入")

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
        action(file_menu, "儲存", self.save, QKeySequence.StandardKey.Save)
        action(file_menu, "另存新檔…", self.save_as, QKeySequence.StandardKey.SaveAs)
        file_menu.addSeparator()
        action(file_menu, "匯入照片…", self.import_photo)
        action(file_menu, "匯入 MusicXML…", self.import_musicxml)
        action(file_menu, "匯出 PDF…", lambda: self.export("pdf"), "Ctrl+E")
        action(file_menu, "離開", self.close, "Alt+F4")
        edit = self.menuBar().addMenu("編輯")
        self.undo_action = action(edit, "復原", self.undo, QKeySequence.StandardKey.Undo)
        self.redo_action = action(edit, "重做", self.redo, QKeySequence.StandardKey.Redo)
        tools_menu = self.menuBar().addMenu("工具")
        action(tools_menu, "更新排版", lambda: self.render_score(True), "F5")
        action(tools_menu, "工具與環境設定", self.tool_settings)
        action(tools_menu, "開啟 Audiveris 校對工具", self.open_audiveris)
        help_menu = self.menuBar().addMenu("說明")
        action(help_menu, "操作說明", self.show_help, "F1")
        action(help_menu, "關於", lambda: QMessageBox.about(self, "關於簡譜工房", f"簡譜工房 {__version__}\nMIT\n\n排譜：GNU LilyPond\n轉換：jianpu-ly · Silas S. Brown (Apache-2.0)\n辨識：Audiveris (AGPL-3.0)\n介面：PySide6 / Qt (LGPL-3.0)\n\nhttps://github.com/miles990/jianpu-studio"))

    def current_tool(self, name):
        return find_tool(name, str(self.settings.value(name, "")))

    def sync(self, selection: int | None = None):
        self.loading = True
        score = self.project.score
        self.title_field.setText(score.title)
        self.composer_field.setText(score.composer)
        self.key_field.setCurrentText(score.key)
        self.meter_field.setCurrentText(score.meter)
        self.tempo_field.setValue(score.tempo)
        self.lyrics.setText(score.lyrics)
        self.staff.setChecked(score.with_staff)
        self.editor_stack.setCurrentIndex(0 if self.project.mode == "visual" else 1)
        self.mode_button.setText("切換進階原始碼" if self.project.mode == "visual" else "返回視覺草稿")
        if self.source.toPlainText() != self.project.source:
            self.source.setPlainText(self.project.source)
        old_row = self.notes.currentRow() if selection is None else selection
        self.notes.clear()
        position = Fraction(0)
        for i, note in enumerate(score.notes):
            item = QListWidgetItem()
            bar = int(position // score.bar_length) + 1
            item.setData(Qt.ItemDataRole.UserRole, {"note": asdict(note), "bar": bar, "bar_start": position % score.bar_length == 0})
            item.setToolTip(f"第 {i + 1} 個音符 · 第 {bar} 小節 · {note.token()}")
            item.setSizeHint(QSize(76, 112))
            self.notes.addItem(item)
            position += note.beats
        if score.notes and old_row >= 0:
            self.notes.setCurrentRow(min(old_row, len(score.notes) - 1))
        error = score.rhythm_error()
        self.rhythm_label.setText(error or f"{len(score.notes)} 個音符 · {position / score.bar_length} 小節 · 拍數完整")
        self.undo_action.setEnabled(bool(self.history) or self.source.document().isUndoAvailable())
        self.redo_action.setEnabled(bool(self.future) or self.source.document().isRedoAvailable())
        self.setWindowTitle(f"{'● ' if self.dirty else ''}{self.path.name if self.path else score.title} — 簡譜工房")
        self.loading = False
        self.select_note(self.notes.currentRow())

    def mutate(self, fn, selection=None):
        if self.loading or self.task:
            return
        candidate = copy.deepcopy(self.project)
        try:
            fn(candidate)
            candidate.score.validate()
        except Exception as exc:
            self.error("無法套用變更", exc)
            self.sync()
            return
        if candidate.to_dict() == self.project.to_dict():
            return
        self.history.append(self.project.to_dict())
        self.history = self.history[-100:]
        self.future.clear()
        self.project = candidate
        self.changed()
        self.sync(selection)

    def changed(self):
        self.player.stop()
        self.dirty = True
        self.revision += 1
        self.preview_status.setText("內容已修改，預覽待更新。")
        self.autopreview.start()

    def capture_fields(self, *_):
        if self.loading or self.project.mode != "visual":
            return
        def update(p):
            p.score.title = self.title_field.text()
            p.score.composer = self.composer_field.text()
            p.score.key = self.key_field.currentText()
            p.score.meter = self.meter_field.currentText()
            p.score.tempo = self.tempo_field.value()
            p.score.lyrics = self.lyrics.text()
            p.score.with_staff = self.staff.isChecked()
        self.mutate(update)

    def source_changed(self):
        if self.loading:
            return
        self.project.source = self.source.toPlainText()
        self.changed()
        self.undo_action.setEnabled(True)

    def add_note(self, degree: int):
        note = Note(degree, 0 if degree == 0 else self.octave.currentData(), self.duration.currentData(),
                    self.dotted.isChecked(), "" if degree == 0 else self.accidental.currentData())
        row = self.notes.currentRow()
        index = row + 1 if row >= 0 else len(self.project.score.notes)
        self.mutate(lambda p: p.score.notes.insert(index, note), index)
        self.notes.setFocus()
        self.notes.scrollToItem(self.notes.currentItem())

    def select_note(self, row):
        if self.loading or row < 0 or row >= len(self.project.score.notes):
            return
        note = self.project.score.notes[row]
        self.degree.setCurrentIndex(note.degree)
        self.duration.setCurrentIndex(self.duration.findData(note.duration))
        self.octave.setCurrentIndex(self.octave.findData(note.octave))
        self.accidental.setCurrentIndex(self.accidental.findData(note.accidental))
        self.dotted.setChecked(note.dotted)

    def apply_note(self):
        row = self.notes.currentRow()
        if row < 0:
            self.statusBar().showMessage("請先點選要修改的音符。", 4000)
            return
        degree = self.degree.currentData()
        note = Note(degree, 0 if degree == 0 else self.octave.currentData(), self.duration.currentData(),
                    self.dotted.isChecked(), "" if degree == 0 else self.accidental.currentData())
        self.mutate(lambda p: p.score.notes.__setitem__(row, note), row)

    def delete_note(self):
        row = self.notes.currentRow()
        if row >= 0:
            self.mutate(lambda p: p.score.notes.pop(row), max(0, row - 1))

    def duplicate_note(self):
        row = self.notes.currentRow()
        if row >= 0:
            self.mutate(lambda p: p.score.notes.insert(row + 1, copy.deepcopy(p.score.notes[row])), row + 1)

    def move_note(self, delta):
        row = self.notes.currentRow()
        target = row + delta
        if row >= 0 and 0 <= target < len(self.project.score.notes):
            self.mutate(lambda p: p.score.notes.insert(target, p.score.notes.pop(row)), target)

    def fill_bar(self):
        self.mutate(lambda p: p.score.fill_last_bar())

    def undo(self):
        if self.task:
            return
        if self.project.mode == "source" and self.source.document().isUndoAvailable():
            self.source.undo()
        elif self.history:
            self.future.append(self.project.to_dict())
            self.project = Project.from_dict(self.history.pop())
            self.changed()
            self.sync()

    def redo(self):
        if self.task:
            return
        if self.project.mode == "source" and self.source.document().isRedoAvailable():
            self.source.redo()
        elif self.future:
            self.history.append(self.project.to_dict())
            self.project = Project.from_dict(self.future.pop())
            self.changed()
            self.sync()

    def switch_mode(self):
        if self.task:
            return
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
        if self.task:
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
        self.player.stop()
        self.project, self.path, self.dirty = project, path, dirty
        self.revision += 1
        self.history.clear()
        self.future.clear()
        self.result = None
        self.recognized_xml = None
        self.pdf_document.close()
        self.photo_label.clear()
        self.photo_label.setText("匯入照片後，可在這裡對照原圖。")
        self.recovery_path.unlink(missing_ok=True)
        self.sync()
        self.autopreview.start()

    def new_project(self):
        if self.guard_unsaved():
            self.reset(Project())

    def load_demo(self):
        if self.guard_unsaved():
            self.reset(demo_project())

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

    def start_task(self, title, fn, callback, show_error=True):
        if self.task:
            return
        self.autopreview.stop()
        self.task = Task(fn, self)
        self.editor.setEnabled(False)
        self.preview_button.setEnabled(False)
        self.progress.show()
        self.cancel_button.show()
        self.statusBar().showMessage(title)
        def finish(value, error):
            task = self.task
            task.wait()
            self.task = None
            task.deleteLater()
            self.editor.setEnabled(True)
            self.preview_button.setEnabled(True)
            self.progress.hide()
            self.cancel_button.hide()
            if error is not None:
                self.preview_status.setText("作業已取消。" if isinstance(error, Cancelled) else "未完成；請查看訊息並修正。")
                self.log.setPlainText(str(error) + "\n\n" + getattr(error, "details", ""))
                self.statusBar().showMessage(str(error).splitlines()[0][:180], 10000)
                if show_error and not isinstance(error, Cancelled):
                    self.error("作業未完成", error)
            else:
                callback(value)
                self.statusBar().showMessage("作業完成", 5000)
        self.task.completed.connect(finish)
        self.task.start()

    def cancel_task(self):
        if self.task:
            self.task.cancel.set()
            self.statusBar().showMessage("正在取消…")

    def render_score(self, manual=True):
        if self.task:
            return
        self.capture_fields()
        try:
            source = self.project.notation()
        except ValueError as exc:
            self.preview_status.setText(str(exc))
            if manual:
                self.error("請先完成樂譜", exc)
            return
        lilypond = self.current_tool("lilypond")
        if not lilypond:
            self.preview_status.setText("尚未找到 LilyPond。請使用完整版安裝檔，或在「工具與環境設定」指定位置。")
            if manual:
                self.tool_settings()
            return
        revision = self.revision
        output = self.job_dir()
        self.preview_status.setText("正在精細排版…")
        def complete(result):
            if revision != self.revision:
                self.autopreview.start()
                return
            self.result = result
            self.rendered_revision = revision
            self.pdf_document.close()
            self.pdf_document.load(str(result.pdf))
            self.preview_status.setText(f"排版完成 · {self.pdf_document.pageCount()} 頁 · 可匯出 PDF / MIDI")
            self.log.setPlainText(result.log)
        self.start_task("正在用 LilyPond 排版…", lambda cancel: render(source, output, lilypond, cancel), complete, manual)

    def fresh_result(self):
        self.capture_fields()
        if not self.result or self.rendered_revision != self.revision:
            self.statusBar().showMessage("請先按「更新排版」，完成後再匯出或播放。", 6000)
            return None
        return self.result

    def export(self, kind):
        try:
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
                result = self.fresh_result()
                if not result:
                    return
                source = {"pdf": result.pdf, "midi": result.midi, "ly": result.lilypond}[kind]
                if not source:
                    raise ValueError("這份樂譜未產生 MIDI。")
                suffix, description, content = source.suffix, kind.upper(), None
            name, _ = QFileDialog.getSaveFileName(self, f"匯出 {description}", self.project.score.title + suffix, f"{description} (*{suffix})")
            if name:
                path = Path(name) if Path(name).suffix else Path(name + suffix)
                if content is not None:
                    atomic_write(path, content)
                else:
                    shutil.copy2(source, path)
                self.statusBar().showMessage(f"已匯出：{path}", 6000)
        except Exception as exc:
            self.error("無法匯出", exc)

    def open_pdf(self):
        result = self.fresh_result()
        if result and not QDesktopServices.openUrl(QUrl.fromLocalFile(str(result.pdf))):
            self.error("無法開啟 PDF", "請設定 Windows 的預設 PDF 閱讀器，或先匯出 PDF。")

    def play_midi(self):
        if self.player.playing:
            self.player.stop()
            return
        result = self.fresh_result()
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
            pixmap = QPixmap(str(prepared))
            self.photo_label.setPixmap(pixmap.scaledToWidth(850, Qt.TransformationMode.SmoothTransformation))
            self.log.setPlainText(message + "\n\n" + log)
            self.preview_status.setText("照片已轉為簡譜。請對照原圖核對調號、音高、拍數與反覆記號。")
            QMessageBox.information(self, "辨識完成 · 請校對", message + "\n\n辨識可能有誤，請切換右側「照片原圖」逐小節對照。可匯出 MusicXML，在 Audiveris 等工具進一步修正。")
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
        for name, display in (("lilypond", "LilyPond 排譜引擎"), ("audiveris", "Audiveris 照片辨識")):
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
        QMessageBox.information(self, "快速上手", "1. 新建或開啟「小星星」範例。\n2. 點 1–7 加入音符，0 加入休止符。點音符後可設定時值、八度、升降與附點，再按「套用」。\n3. 右側會自動排版；拍數不足時按「補齊休止符」。\n4. Ctrl+S 儲存 .jps；匯出 PDF 可列印，按「試聽 / 停止」可用 Windows 內建 MIDI 合成器播放。\n5. 照片轉簡譜適用清楚的印刷五線譜；先旋轉／裁白邊，再辨識、對照原圖校對。\n\n快捷鍵：Ctrl+Z 復原、Ctrl+Y 重做、F5 排版、Ctrl+E 匯出 PDF。音符區可用 0–7 輸入、Delete 刪除。\n\n每 15 秒建立本機復原暫存。不會上傳樂譜或照片。\n進階原始碼支援多聲部與更多記號；照片辨識不支援手寫或數字簡譜影像。")

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

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "sidebar"):
            self.sidebar.setVisible(self.width() >= 1300)

    def closeEvent(self, event):
        if self.task:
            self.cancel_task()
            self.statusBar().showMessage("正在取消作業，完成後請再次關閉視窗。")
            event.ignore()
            return
        if not self.guard_unsaved():
            event.ignore()
            return
        self.autosave.stop()
        self.player.stop()
        self.autopreview.stop()
        self.recovery_path.unlink(missing_ok=True)
        self.pdf_document.close()
        self.temp.cleanup()
        event.accept()
