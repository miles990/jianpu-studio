from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path

from PySide6.QtCore import (
    QBuffer,
    QIODevice,
    QPoint,
    Qt,
    QThread,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QKeySequence, QPixmap, QShortcut
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

from . import __version__, i18n, icons, migrate, storage, theme
from .editing import EditActions
from .engine import Cancelled, find_tool, recognize, render
from .i18n import tr
from .model import (
    Note,
    Project,
    atomic_write,
    demo_project,
    load_project,
    save_project,
)
from .musicxml import read_xml, visual_import
from .photo import PhotoDialog
from .playback import MidiPlayer
from .workspace import build_workspace

PANEL_KEY = "layout/side_panel_width"
PANEL_NARROW, PANEL_DEFAULT, PANEL_MAX = 276, 328, 360    # design §1.2–1.3


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


def symbols_text_marks(score):
    """Technique marks printed as Chinese text (their script follows the score, not the UI)."""
    from . import symbols
    for part in score.parts:
        for note in part.notes:
            for mark in note.marks:
                symbol = symbols.SYMBOLS[mark]
                if symbol.canvas_text("tc") != symbol.canvas_text("sc"):
                    yield mark


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
    def __init__(self, recovery: bool = True, initial_path: Path | None = None):
        super().__init__()
        self.setMinimumSize(960, 600)
        self.resize(1380, 850)
        self.settings = storage.open_settings()
        i18n.init_language(self.settings)
        i18n.on_change(self.retranslate)
        self.theme = theme.ThemeManager(self.settings, self)
        self.theme.apply(i18n.current_language())
        self.theme.changed.connect(self.theme_changed)
        self.problem_list = []
        self.play_map = None
        self.play_timer = QTimer(self)
        self.play_timer.setInterval(50)
        self.play_timer.timeout.connect(self.playback_tick)
        self.panel_collapsed = False
        self.shortcuts_dialog = None
        # A real launch starts blank behind the start page (design §5); tests and the
        # self-test keep the demo.
        self.project = Project() if recovery else demo_project()
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
        self.data_dir = storage.data_dir()
        self.recovery_path = self.data_dir / "recovery.jps"
        self.recovery_protected = False
        self.autopreview = QTimer(self)
        self.autopreview.setSingleShot(True)
        self.autopreview.setInterval(1200)
        self.autopreview.timeout.connect(lambda: self.render_score(False))
        self.build_ui()
        self.build_menus()
        self.build_shortcuts()
        from .widgets import DisabledReasons
        self.disabled_reasons = DisabledReasons(self.disabled_reason, self)
        self.disabled_reasons.watch_tree(self)
        for action in (self.undo_action, self.redo_action):
            self.disabled_reasons.watch(action)
        self.theme_changed()
        self.sync()
        self.input_mode.setCurrentIndex(self.input_mode.findData("edit"))
        self.language_buttons[i18n.current_language()].setChecked(True)
        launches = int(self.settings.value("guide/launches", 0) or 0)
        self.settings.setValue("guide/launches", launches + 1)
        self.guide.setVisible(self.guide.should_show())
        self.start_page.refresh(self.recent_files())
        # A real launch (design §5) shows the start page until something is opened:
        # recovered work, the file the system asked for, or the last score if it still
        # exists. It never shows the demo as if it were the user's own score.
        last_file = self.last_file() if recovery and initial_path is None else None
        if recovery:
            self.editor_stack.setCurrentIndex(1)
        self.autosave = QTimer(self)
        self.autosave.setInterval(15000)
        self.autosave.timeout.connect(self.write_recovery)
        self.autosave.start()
        if initial_path is not None or last_file is not None or (recovery and self.recovery_path.is_file()):
            QTimer.singleShot(0, lambda: self.finish_startup(recovery, initial_path, last_file))
        else:
            QTimer.singleShot(250, lambda: self.render_score(False))

    def last_file(self) -> Path | None:
        """The most recently opened score, if it is still there."""
        files = self.recent_files()
        if not files:
            return None
        path = Path(files[0])
        return path if path.is_file() else None

    def finish_startup(self, recovery, initial_path, last_file=None):
        # One callback: a separate open-file timer could run inside a recovery
        # dialog's nested event loop and delete the only unsaved copy.
        restored = False
        if recovery and self.recovery_path.is_file():
            restored = self.restore_recovery()
        if initial_path is not None:
            self.open_file(path=initial_path)
        elif last_file is not None and not restored and last_file.is_file():
            # Restored work is newer than the last saved file: keep it on screen.
            self.open_file(path=last_file)

    def changed(self):
        """Every score change: a notice about the previous state (a warning that an earlier
        attempt failed, or an undo offer for an earlier edit) is stale now (review c1/13)."""
        super().changed()
        self.toast.dismiss()

    def build_ui(self):
        try:
            self.panel_width = int(self.settings.value(PANEL_KEY, PANEL_DEFAULT))
        except (TypeError, ValueError):
            self.panel_width = PANEL_DEFAULT
        build_workspace(self)
        self.split.splitterMoved.connect(self.panel_dragged)
        self.split.installEventFilter(self)
        self.apply_responsive()

    def fit_split(self):
        """Give the side panel its width and the score the rest. Also run when the splitter
        itself is resized: QSplitter scales sizes set before its own resize proportionally."""
        if self.print_preview_active:
            return
        narrow = self.width() <= 1180
        panel = PANEL_NARROW if narrow else max(PANEL_DEFAULT, min(PANEL_MAX, self.panel_width))
        total = self.split.width()
        if self.split.sizes()[2:] != [panel] or sum(self.split.sizes()) + 2 * self.split.handleWidth() < total - 2:
            self.split.setSizes([max(1, total - panel - self.split.handleWidth()), 0, panel])

    def eventFilter(self, watched, event):
        if watched is getattr(self, "split", None) and event.type() == event.Type.Resize:
            self.fit_split()
        return super().eventFilter(watched, event)

    def panel_dragged(self, *_):
        """The user dragged the divider: keep the side panel width (wide windows only)."""
        if self.width() <= 1180 or not self.side_panel.isVisible():
            return
        self.panel_width = max(PANEL_DEFAULT, min(PANEL_MAX, self.side_panel.width()))
        self.settings.setValue(PANEL_KEY, self.panel_width)

    def build_menus(self):
        def action(menu, text, fn, shortcut=None, icon_name=""):
            a = QAction(text, self)
            a.triggered.connect(fn)
            if shortcut:
                a.setShortcut(shortcut)
            if icon_name:
                a.setIcon(icons.icon(icon_name))
            menu.addAction(a)
            return a
        file_menu = self.menuBar().addMenu(tr("檔案"))
        action(file_menu, tr("新建"), self.new_project, QKeySequence.StandardKey.New, "doc-new")
        action(file_menu, tr("開始頁"), self.show_start_page, None, "template-blank")
        action(file_menu, tr("開啟…"), self.open_file, QKeySequence.StandardKey.Open, "folder-open")
        self.recent_menu = file_menu.addMenu(tr("最近開啟"))
        self.recent_menu.aboutToShow.connect(self.fill_recent_menu)
        action(file_menu, tr("開啟「小星星」範例"), self.load_demo, None, "example")
        action(file_menu, tr("開啟「晨光二重奏」範例"), lambda: self.open_example("duet"), None, "example")
        action(file_menu, tr("儲存"), self.save, QKeySequence.StandardKey.Save, "save")
        action(file_menu, tr("另存新檔…"), self.save_as, QKeySequence.StandardKey.SaveAs)
        file_menu.addSeparator()
        action(file_menu, tr("匯入 MusicXML…"), self.import_musicxml)
        action(file_menu, tr("匯出 PDF…"), lambda: self.export("pdf"), "Ctrl+E", "file-pdf")
        action(file_menu, tr("匯出 MIDI…"), lambda: self.export("midi"), None, "file-midi")
        action(file_menu, tr("列印預覽"), self.show_print_preview, "F5", "print")
        file_menu.addSeparator()
        action(file_menu, tr("離開"), self.close, QKeySequence.StandardKey.Quit).setMenuRole(QAction.MenuRole.QuitRole)
        edit = self.menuBar().addMenu(tr("編輯"))
        self.undo_action = action(edit, tr("復原"), self.undo, QKeySequence.StandardKey.Undo, "undo")
        self.redo_action = action(edit, tr("重做"), self.redo, QKeySequence.StandardKey.Redo, "redo")
        edit.addSeparator()
        for text, command in ((tr("剪下"), "cut"), (tr("複製"), "copy"), (tr("貼上"), "paste"),
                              (tr("重複"), "duplicate"), (tr("刪除音符"), "delete")):
            action(edit, text, lambda checked=False, c=command: self.note_command(c))
        edit.addSeparator()
        position_menu = edit.addMenu(tr("新增音符的位置"))
        self.position_group = QActionGroup(self)
        for index, text in enumerate((tr("插在選取後"), tr("插在選取前"), tr("加到曲尾"))):
            item = QAction(text, self)
            item.setCheckable(True)
            item.setChecked(index == 0)
            item.triggered.connect(lambda checked=False, i=index: self.insert_position.setCurrentIndex(i))
            self.position_group.addAction(item)
            position_menu.addAction(item)
        measures = self.menuBar().addMenu(tr("小節"))
        measures.aboutToShow.connect(lambda: (measures.clear(), self.add_measure_menu(measures)))
        notation = self.notation_menu()
        notation.setTitle(tr("記號"))
        notation.insertSeparator(notation.actions()[0])
        search = QAction(tr("搜尋記號…"), self)
        search.setShortcut(QKeySequence.StandardKey.Find)
        search.triggered.connect(lambda: self.palette.focus_search())
        notation.insertAction(notation.actions()[0], search)
        continuous = QAction(tr("結束連續套用"), self)
        continuous.triggered.connect(lambda: self.set_continuous(""))
        notation.addAction(continuous)
        self.menuBar().addMenu(notation)
        view = self.menuBar().addMenu(tr("檢視"))
        action(view, tr("編輯樂譜"), lambda: self.change_workspace(0))
        self.panel_action = action(view, tr("記號面板"), self.toggle_panel, "F4", "panel-right")
        self.panel_action.setCheckable(True)
        self.panel_action.setChecked(True)
        appearance = view.addMenu(tr("外觀"))
        self.appearance_group = QActionGroup(self)
        self.appearance_actions = {}
        for key, text in (("system", tr("跟隨系統")), ("light", tr("淺色")), ("dark", tr("深色"))):
            item = QAction(text, self)
            item.setCheckable(True)
            item.triggered.connect(lambda checked=False, k=key: self.theme.set_appearance(k))
            self.appearance_group.addAction(item)
            appearance.addAction(item)
            self.appearance_actions[key] = item
        appearance.addSeparator()
        self.white_paper_action = QAction(tr("深色時譜紙仍用白色"), self)
        self.white_paper_action.setCheckable(True)
        self.white_paper_action.setChecked(self.theme.white_paper)
        self.white_paper_action.triggered.connect(self.theme.set_white_paper)
        appearance.addAction(self.white_paper_action)
        zoom = view.addMenu(tr("縮放"))
        action(zoom, tr("放大"), lambda: self.step_zoom(1), "Ctrl+=")
        action(zoom, tr("縮小"), lambda: self.step_zoom(-1), "Ctrl+-")
        action(zoom, tr("實際大小（100%）"), lambda: self.score_zoom.setCurrentIndex(1), "Ctrl+0")
        self.hidden_action = action(view, tr("顯示待老師確認的記號"), lambda checked: self.palette.set_show_hidden(checked))
        self.hidden_action.setCheckable(True)
        self.palette.showHiddenChanged.connect(self.hidden_action.setChecked)
        tools_menu = self.menuBar().addMenu(tr("工具"))
        action(tools_menu, tr("工作紀錄"), self.show_work_log)
        action(tools_menu, tr("工具與環境設定"), self.tool_settings).setMenuRole(QAction.MenuRole.PreferencesRole)
        self.language_menu = self.menuBar().addMenu(i18n.LANGUAGE_MENU_TITLE)
        self.language_group = QActionGroup(self)
        self.language_actions = {}
        for code in i18n.LANGUAGES:
            item = QAction(i18n.LANGUAGE_NAMES[code], self)
            item.setCheckable(True)
            item.setData(code)
            item.triggered.connect(lambda checked=False, c=code: self.change_language(c))
            self.language_group.addAction(item)
            self.language_menu.addAction(item)
            self.language_actions[code] = item
        self.language_actions[i18n.current_language()].setChecked(True)
        help_menu = self.menuBar().addMenu(tr("說明"))
        action(help_menu, tr("操作說明"), self.show_help, "F1", "keyboard")
        action(help_menu, tr("開始寫譜引導"), self.show_guide, None, "lightbulb")
        action(help_menu, tr("檢查更新…"), self.check_updates, None, "update")
        action(help_menu, tr("關於"), self.show_about).setMenuRole(QAction.MenuRole.AboutRole)

    def build_shortcuts(self):
        """Window-wide keys that must work wherever the focus is."""
        def key(sequence, fn):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(fn)
            return shortcut
        key("F6", lambda: self.cycle_focus(1))
        key("Shift+F6", lambda: self.cycle_focus(-1))

    def cycle_focus(self, step):
        """F6: score → palette → inspector → score bar (design §2.7)."""
        stops = [self.notes, self.palette.search, self.inspector, self.title_field]
        focus = QApplication.focusWidget()
        index = next((i for i, w in enumerate(stops) if w is focus or (focus is not None and w.isAncestorOf(focus))), -1)
        target = stops[(index + step) % len(stops)]
        if target is self.inspector:
            chips = [chip.remove for chip in self.inspector.chip_widgets]
            buttons = [b for b in self.inspector.measure_buttons.values() if b.isVisible()]
            target = (chips or buttons or [self.inspector.grace_button if self.inspector.grace_button.isVisible()
                                           else self.title_field])[0]
        target.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def toggle_panel(self):
        """F4 / 檢視 → 記號面板: collapse the side panel to give the score the width."""
        self.panel_collapsed = not self.panel_collapsed
        self.side_panel.setVisible(not self.panel_collapsed and not self.print_preview_active)
        self.panel_action.setChecked(not self.panel_collapsed)

    def disabled_reason(self, control) -> str:
        """Why ``control`` cannot be used right now, in the user's words (design §7 item 30)."""
        from .model import MAX_PARTS
        if self.task is not None and self.task_blocks_edit and not control.isEnabled():
            return tr("正在處理作業；完成或按「取消作業」後才能使用")
        if control in (self.undo_action, self.undo_button):
            return tr("目前沒有可以復原的修改")
        if control in (self.redo_action, self.redo_button):
            return tr("目前沒有可以重做的修改")
        if control is self.tie_button:
            return tr("請先選取要連到下一個同音高音的音符")
        if control is self.add_part_button:
            return tr("最多 {count} 個聲部").format(count=MAX_PARTS)
        if control is self.palette.detail.favorite:
            return tr("請先在面板中點一個記號")
        if control is self.preview_button:
            return tr("正在排版，完成後可再更新")
        note_bar = [*self.pitch_buttons, *self.duration_buttons.values(), *self.accidental_buttons.values(),
                    self.dot_button, self.octave_up_button, self.octave_down_button]
        if any(control is b for b in note_bar):
            return tr("修改模式：請先在譜上點選要修改的音符")
        if control in self.inspector.measure_buttons.values():
            return tr("請先點小節號選取小節")
        reason = control.property("disabled_reason")
        return tr(reason) if reason else ""

    def show_guide(self):
        self.settings.setValue("guide/hidden", False)
        self.settings.setValue("guide/launches", 0)
        self.guide.dismissed_now = False
        self.guide.done.clear()
        self.guide.refresh()
        self.apply_responsive()

    def show_guide_popup(self):
        """Short windows: the status-bar 「開始寫譜 2/5 ›」 lists the steps."""
        from PySide6.QtWidgets import QMenu

        from .startpage import GUIDE_STEPS
        menu = QMenu(self)
        for index, text in enumerate(GUIDE_STEPS, 1):
            menu.addAction(("✓ " if index in self.guide.done else f"{index}. ") + tr(text)).setEnabled(False)
        menu.addSeparator()
        menu.addAction(tr("不再顯示 ×"), self.guide.dismiss)
        menu.exec(self.guide_status.mapToGlobal(self.guide_status.rect().topLeft()) - QPoint(0, menu.sizeHint().height()))

    def show_start_page(self):
        self.start_page.refresh(self.recent_files())
        self.editor_stack.setCurrentIndex(1)

    def recent_files(self):
        raw = self.settings.value("recent_files", [])
        values = [raw] if isinstance(raw, str) else list(raw or [])
        return [v for v in values if v]

    def remember_recent(self, path):
        files = [str(path)] + [p for p in self.recent_files() if p != str(path)]
        self.settings.setValue("recent_files", files[:10])

    def fill_recent_menu(self):
        self.recent_menu.clear()
        files = self.recent_files()
        if not files:
            self.recent_menu.addAction(tr("還沒有開過樂譜")).setEnabled(False)
        for path in files:
            self.recent_menu.addAction(Path(path).name, lambda p=path: self.open_file(path=Path(p))).setToolTip(path)

    def theme_changed(self):
        colors = self.theme.colors
        self.notes.set_colors(colors)
        self.palette.set_colors(colors)
        self.inspector.colors = colors
        appearance = self.theme.appearance
        if hasattr(self, "appearance_actions"):
            self.appearance_actions[appearance].setChecked(True)
        icons.attach(self.theme_button, {"light": "sun", "dark": "moon", "system": "theme-auto"}[appearance], "text2", 16)
        self.theme_button.setToolTip({"light": tr("外觀：淺色（點一下換深色）"), "dark": tr("外觀：深色（點一下換成跟隨系統）"),
                                      "system": tr("外觀：跟隨系統（點一下換淺色）")}[appearance])
        self.refresh_inspector()

    def cycle_theme(self):
        self.theme.cycle()

    def step_zoom(self, step):
        index = max(0, min(self.score_zoom.count() - 1, self.score_zoom.currentIndex() + step))
        self.score_zoom.setCurrentIndex(index)

    def status_message_changed(self, message):
        """Temporary status messages show in the middle of the status bar, then the hint returns."""
        if message:
            self.selection_label.setText(message)
            self.selection_label.setToolTip(message)
        else:
            self.selection_label.setToolTip("")
            self.update_selection()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.apply_responsive()

    def apply_responsive(self):
        """Collapse order of design §1.3 (each step was screenshot-checked)."""
        if not hasattr(self, "side_panel"):
            return
        width, height = self.width(), self.height()
        self.parts_label.setVisible(width > 1300 and len(self.project.score.parts) > 1)
        self.composer_label.setVisible(width > 1300)
        self.composer_field.setVisible(width > 1300)
        self.composer_rule.setVisible(width > 1300)
        icon_only = width <= 1240
        for button in self.file_buttons:
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly if icon_only
                                      else Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.preview_button_top.setText(tr("預覽") if icon_only else tr("列印預覽"))
        narrow = width <= 1180
        # ≤ 1180 the panel is the compact 276; wider, the user may drag it between 328 and
        # 360 (design §1.3) and the choice is remembered across restarts.
        low, high = (PANEL_NARROW, PANEL_NARROW) if narrow else (PANEL_DEFAULT, PANEL_MAX)
        self.side_panel.setMinimumWidth(low)
        self.side_panel.setMaximumWidth(high)
        self.fit_split()
        self.palette.grid.compact = narrow
        self.palette.grid.relayout()
        self.palette.detail.set_compact(narrow or height <= 700)
        tight = width <= 1120
        self.extra_host.setVisible(not tight)
        self.add_bar_button.setVisible(not tight)
        self.note_more_button.setVisible(tight)
        self.layout_button.setVisible(not tight)
        self.layout_rule.setVisible(not tight)
        smallest = width <= 1040
        self.inspector.set_compact(smallest)
        for button in self.pitch_buttons:
            button.setFixedSize(26 if smallest else 30, 30)
        for button in self.duration_buttons.values():
            button.setFixedSize(28 if smallest else 32, 30)
        for button in (self.dot_button, self.octave_up_button, self.octave_down_button):
            button.setFixedSize(28 if smallest else 30, 30)
        self.note_tools.layout().setSpacing(2 if smallest else 3)
        for button in self.input_mode_buttons.values():
            button.setMinimumWidth(40 if smallest else 48)
        self.palette.set_narrow(narrow)
        # Short windows: the guide shrinks into the status bar (design §1.3).
        guide_wanted = self.guide.should_show() and not self.guide.dismissed_now
        compact_guide = height <= 700 or width <= 1100
        self.guide.setVisible(guide_wanted and not compact_guide)
        self.guide_status.setVisible(guide_wanted and compact_guide)
        self.guide_status.setText(tr("開始寫譜 {done}/5 ›").format(done=len(self.guide.done)))
        self.counts_label.setVisible(width > 1100)
        self.update_transport()

    def show_about(self):
        QMessageBox.about(self, tr("關於簡譜工房"), tr(
            "簡譜工房 {version}\nMIT\n\n排譜：GNU LilyPond\n轉換：jianpu-ly · Silas S. Brown (Apache-2.0)\n"
            "辨識：Audiveris (AGPL-3.0)\n介面：PySide6 / Qt (LGPL-3.0)\n\nhttps://github.com/miles990/jianpu-studio"
        ).format(version=__version__))

    def change_language(self, language):
        """Switch the interface immediately; the project, history and selection stay as they are."""
        if language not in i18n.LANGUAGES:
            return
        self.settings.setValue(i18n.SETTINGS_KEY, language)
        i18n.set_language(language)
        self.language_actions[language].setChecked(True)
        self.language_buttons[language].setChecked(True)

    def retranslate(self, new, old):
        """Convert every visible text in place (no widget is rebuilt or reconnected)."""
        self.theme.apply(new)
        self.language_actions[new].setChecked(True)
        self.language_buttons[new].setChecked(True)
        for code, button in self.language_buttons.items():
            button.setChecked(code == new)
        message = self.statusBar().currentMessage()
        i18n.translate_tree(self, new, old)
        self.disabled_reasons.refresh_all()
        log = self.log.toPlainText()
        converted = i18n.convert(log, new, old)
        if converted != log:
            self.log.setPlainText(converted)
        self.notes.refresh_language()
        self.palette.retranslate()
        self.guide.retranslate()
        if self.shortcuts_dialog is not None:
            self.shortcuts_dialog.retranslate()
        self.sync()
        self.apply_responsive()
        self.retranslate_choice()
        if message:
            self.statusBar().showMessage(i18n.convert(message, new, old), 5000)
        if self.project.score.print_variant != ("sc" if new == i18n.SIMPLIFIED else "tc") and any(
                symbols_text_marks(self.project.score)):
            self.toast.show_text(tr("這份樂譜印出的技法字仍是{script}（在「紙張與排版」可改）").format(
                script=tr("繁體") if self.project.score.print_variant == "tc" else tr("簡體")))

    def current_tool(self, name):
        return find_tool(name, str(self.settings.value(name, "")))

    @staticmethod
    def prepare_visual_project(project):
        """Accept only an authoritative editable score, never a stale draft."""
        project.score.validate()
        if project.mode == "visual":
            return project
        try:
            exported = project.score.to_jianpu()
            unchanged = (project.source.strip() == exported.strip()
                         or migrate.same_music_text(project.source, exported))
        except ValueError:
            unchanged = False
        if unchanged:
            visual = project.snapshot()
            visual.mode = "visual"
            return visual
        raise ValueError(tr("這份舊專案含有目前無法轉成可編輯音符的內容。"
                            "本版只提供視覺編輯，未載入或變更原檔；可保留原檔並使用 0.3.0 處理。"))

    def clear_recovery(self):
        if not self.recovery_protected:
            self.recovery_path.unlink(missing_ok=True)
            self.recovery_path.with_suffix(".path").unlink(missing_ok=True)

    def guard_unsaved(self) -> bool:
        if self.task and self.task_blocks_edit:
            self.statusBar().showMessage(tr("請先等待作業完成，或按「取消作業」。"), 5000)
            return False
        self.capture_fields()
        if not self.dirty:
            return True
        name = self.path.name if self.path else (self.project.score.title or tr("未命名樂曲"))
        answer = QMessageBox.question(
            self, tr("尚未儲存"), tr("「{name}」有還沒儲存的修改，要先儲存嗎？").format(name=name),
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save)
        if answer == QMessageBox.StandardButton.Save:
            return self.save()
        return answer == QMessageBox.StandardButton.Discard

    def reset(self, project: Project, path: Path | None = None, dirty=False, *, keep_recovery=False):
        """Show ``project``. The recovery copy is discarded unless ``keep_recovery``: restored
        work keeps it on disk until it is saved or explicitly discarded."""
        project = self.prepare_visual_project(project)
        if self.task and not self.task_blocks_edit:
            self.task.cancel.set()
        self.autopreview.stop()
        self.render_pending = self.render_pending_manual = self.render_pending_pdf = False
        self.render_cancelled = False
        self.play_after_render = False
        self.output_request = None
        self.stop_playback()
        self.set_continuous("")
        self.project, self.path, self.dirty = project, path, dirty
        # What is on disk: undoing back to it clears 「已修改」.
        self.saved_project = None if dirty else project.snapshot()
        self.editor_stack.setCurrentIndex(0)
        self.notes.reset_selection()
        self.notes.set_score(project.score, 0)
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
        self.photo_label.setText(tr("匯入照片後，可在這裡對照原圖。"))
        self.measure_notice.clear()
        self.measure_notice.hide()
        if self.print_preview_active:
            self.change_workspace(0)
        if not keep_recovery:
            self.clear_recovery()
        self.sync(selection=[], current=-1)
        if self.print_preview_active and self.auto_check.isChecked():
            self.autopreview.start()
        # Keys go to the score, not the title field (a stray key must not rename the piece).
        self.notes.setFocus(Qt.FocusReason.OtherFocusReason)
        QTimer.singleShot(0, self._focus_score_if_unfocused)

    def _focus_score_if_unfocused(self):
        focus = QApplication.focusWidget()
        if self.editor_stack.currentIndex() == 0 and (focus is None or focus is self.title_field):
            self.notes.setFocus(Qt.FocusReason.OtherFocusReason)

    def new_project(self):
        """「新建」 is the start page's 「空白樂譜」: 8 empty bars, 1=D, 4/4, ready to fill in."""
        self.new_from_template("blank")

    def load_demo(self):
        if self.guard_unsaved():
            self.reset(demo_project())
            self.input_mode.setCurrentIndex(self.input_mode.findData("edit"))

    def open_file(self, checked=False, path: Path | None = None):
        if not self.guard_unsaved():
            return
        if path is None:
            name, _ = QFileDialog.getOpenFileName(self, tr("開啟樂譜"), "", tr("樂譜專案 (*.jps)"))
            if not name:
                return
            path = Path(name)
        try:
            if path.suffix.lower() != ".jps":
                raise ValueError(tr("請開啟 .jps 樂譜專案；MusicXML 請用「檔案 → 匯入 MusicXML」。本版不提供文字樂譜編輯。"))
            self.reset(load_project(path), path)
            self.input_mode.setCurrentIndex(self.input_mode.findData("edit"))
            if path.parent.name != "examples":
                self.remember_recent(path)
        except FileNotFoundError:
            self.error(tr("無法開啟樂譜"), tr("找不到這個檔案，可能已經被移動、改名或刪除：\n{path}").format(path=path))
        except PermissionError:
            self.error(tr("無法開啟樂譜"), tr("沒有權限讀取這個檔案：\n{path}").format(path=path))
        except OSError as exc:
            self.error(tr("無法開啟樂譜"), tr("讀不到這個檔案（{reason}）：\n{path}").format(
                reason=exc.strerror or type(exc).__name__, path=path))
        except Exception as exc:
            self.error(tr("無法開啟樂譜"), exc)

    def save(self, checked=False) -> bool:
        self.capture_fields()
        if self.path is None:
            return self.save_as()
        try:
            save_project(self.path, self.project)
            self.remember_recent(self.path)
            self.dirty = False
            self.saved_project = self.project.snapshot()
            self.clear_recovery()
            self.sync()
            self.statusBar().showMessage(tr("已儲存：{path}").format(path=self.path), 6000)
            return True
        except Exception as exc:
            self.error(tr("儲存失敗，原檔未被覆蓋"), exc)
            return False

    def save_as(self, checked=False) -> bool:
        name, _ = QFileDialog.getSaveFileName(self, tr("儲存專案"), str(self.path or Path(self.project.score.title + ".jps")), tr("簡譜工房專案 (*.jps)"))
        if not name:
            return False
        old_path = self.path
        self.path = Path(name) if Path(name).suffix else Path(name + ".jps")
        if self.save():
            return True
        self.path = old_path
        return False

    def write_recovery(self):
        if self.dirty and not self.recovery_protected:
            try:
                save_project(self.recovery_path, self.project)
                # Which file the work belongs to, so a recovered score saves back to it.
                atomic_write(self.recovery_path.with_suffix(".path"), str(self.path) if self.path else "")
            except (OSError, ValueError):
                # The previous recovery copy is left untouched (atomic, validated before writing).
                self.statusBar().showMessage(tr("自動復原暫存失敗，請手動儲存專案。"), 5000)

    def restore_recovery(self) -> bool:
        """Offer the recovery copy; True when the recovered score is now shown."""
        try:
            recovered = load_project(self.recovery_path)
        except Exception:
            self.statusBar().showMessage(tr("找到無法讀取的復原檔，已保留原檔並暫停自動暫存；請手動儲存新樂譜。"), 8000)
            self.recovery_protected = True
            return False
        try:
            recovered = self.prepare_visual_project(recovered)
        except ValueError as exc:
            try:
                data = self.recovery_path.read_bytes()
                backup = self.recovery_path.with_name("recovery-legacy-" + hashlib.sha256(data).hexdigest() + ".jps")
                atomic_write(backup, data)
                self.clear_recovery()
                self.error(tr("舊版復原暫存已保留"), tr("{detail}\n\n完整暫存保留於：{backup}").format(detail=exc, backup=backup))
            except OSError:
                # Never overwrite or delete the only copy if backup failed.
                self.recovery_protected = True
                self.error(tr("舊版復原暫存已保留"), tr("{detail}\n\n無法建立備份，原暫存保留於：{path}\n本次自動復原暫存暫停，請手動儲存新樂譜。").format(detail=exc, path=self.recovery_path))
            return False
        if QMessageBox.question(self, tr("復原未儲存樂譜"), tr("上次有未儲存的樂譜，現在復原嗎？")) == QMessageBox.StandardButton.Yes:
            # The file stays until the restored work is saved or discarded: a second crash
            # before the next autosave must not lose the only copy.
            path = None
            try:
                saved = self.recovery_path.with_suffix(".path").read_text(encoding="utf-8").strip()
                path = Path(saved) if saved else None
            except OSError:
                pass
            self.reset(recovered, path, dirty=True, keep_recovery=True)
            if path is not None:
                self.statusBar().showMessage(tr("已復原「{name}」未儲存的修改；按儲存會寫回原檔。").format(name=path.name), 10000)
            return True
        self.clear_recovery()
        self.autopreview.start()
        return False

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
                self.score_settings.setEnabled(False)
            return
        self.autopreview.stop()
        task = Task(fn, self)
        task.revision = revision
        self.task = task
        self.task_blocks_edit = blocks_edit
        self.editor.setEnabled(not blocks_edit)
        self.score_settings.setEnabled(not blocks_edit)
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
            self.score_settings.setEnabled(True)
            self.preview_button.setEnabled(True)
            self.progress.hide()
            self.cancel_button.hide()
            stale = revision is not None and revision != self.revision
            if error is not None and not stale and not self.close_requested:
                self.drop_pending_output()
                self.preview_status.setText(tr("作業已取消。") if isinstance(error, Cancelled) else tr("未完成；請查看訊息並修正。"))
                self.log.setPlainText(str(error) + "\n\n" + getattr(error, "details", ""))
                self.statusBar().showMessage((str(error) or tr("作業未完成")).splitlines()[0][:180], 10000)
                if show_error and not isinstance(error, Cancelled):
                    self.error(tr("作業未完成"), error)
            elif error is None and not self.close_requested:
                callback(value)
                if not stale:
                    self.statusBar().showMessage(tr("作業完成"), 5000)
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
        self.update_transport()
        if self.task:
            self.task.cancel.set()
            self.statusBar().showMessage(tr("正在取消…"))

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
                    self.statusBar().showMessage(tr("已排入最新版本；目前排版完成後會更新。"), 4000)
            return
        try:
            source = self.project.notation()
        except ValueError as exc:
            # An unfinished repeat/jump can still print as a draft (asked first).
            source = self.draft_notation(manual) if self.unfinished_structure() else None
            if source is None:
                self.drop_pending_output()
                self.preview_status.setText(str(exc))
                if manual and not self.unfinished_structure():
                    self.error(tr("請先完成樂譜"), exc)
                return
        lilypond = self.current_tool("lilypond")
        if not lilypond:
            self.drop_pending_output()
            self.preview_status.setText(tr("尚未找到 LilyPond。請使用完整版安裝檔，或在「工具與環境設定」指定位置。"))
            if manual:
                self.tool_settings()
            return
        revision = self.revision
        paper_size, staff_size = self.project.score.paper_size, self.project.score.staff_size
        output = self.job_dir()
        self.preview_status.setText(tr("正在準備列印排版…") if create_pdf else tr("正在準備試聽 / MIDI…"))
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
            self.preview_status.setText(tr("排版完成 · {pages} 頁 · 可列印 / 匯出 PDF").format(pages=self.pdf_document.pageCount()) if create_pdf else tr("MIDI 已準備完成。"))
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
        self.start_task(tr("正在準備列印 PDF；可繼續編輯…") if create_pdf else tr("正在準備 MIDI 試聽資料…"), job, complete, manual, blocks_edit=False, revision=revision)
        if self.task:
            self.task.creates_pdf = create_pdf

    def unfinished_structure(self):
        """The first repeat/jump problem when it is the only thing keeping the score from print."""
        score = self.project.score
        if self.project.mode != "visual" or score.rhythm_error() or score.edit_error():
            return None
        from .notation_rules import structure_problems
        problems = structure_problems(score)
        return problems[0] if problems else None

    def draft_notation(self, manual):
        """A draft print of an unfinished repeat/jump: asked once per version when the user asked
        for output; the signs print as they stand and nothing repeats or jumps."""
        if self.unfinished_structure() is None or manual and not self.draft_confirmed():
            return None
        self.statusBar().showMessage(tr("草稿：反覆／跳躍還沒完成，記號照樣印出，但不反覆、不跳躍（試聽與 MIDI 也是）。"), 10000)
        try:
            return self.project.notation(draft=True)
        except ValueError:
            return None

    def draft_confirmed(self) -> bool:
        """True unless this version is an unfinished structure the user has not accepted as a
        draft; asks now. Every output (export, open PDF) passes here, so an automatic preview's
        draft render can never be handed out unconfirmed."""
        problem = self.unfinished_structure()
        if problem is None or getattr(self, "_draft_ok", None) == self.revision:
            return True
        if not self.confirm_draft(problem):
            self.goto_place(0, problem.row, problem.bar)
            self.statusBar().showMessage(str(problem), 10000)
            return False
        self._draft_ok = self.revision
        return True

    def confirm_draft(self, problem) -> bool:
        """Tests replace this method."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(tr("反覆／跳躍還沒完成"))
        box.setText(tr("{problem}\n\n要先印成草稿嗎？記號會照樣印出，但樂曲不會反覆或跳躍（試聽與 MIDI 也一樣）。").format(
            problem=problem))
        draft = box.addButton(tr("印成草稿"), QMessageBox.ButtonRole.AcceptRole)
        box.addButton(tr("先去完成"), QMessageBox.ButtonRole.RejectRole)
        box.exec()
        return box.clickedButton() is draft

    def drop_pending_output(self):
        """A failed or impossible render: nothing will play or export, so the transport
        must not keep saying 「準備試聽…」."""
        self.play_after_render = False
        self.output_request = None
        self.update_transport()

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
            raise ValueError(tr("無法開啟產生的 PDF 預覽，請查看排版紀錄並重新排版。"))

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
            self.statusBar().showMessage(tr("請使用「匯出 / 列印」或試聽；需要時會自動準備輸出。"), 6000)
            return None
        return self.result

    def export(self, kind):
        try:
            self.capture_fields()
            if kind == "jly":
                self.capture_fields()
                content, suffix, description = self.project.notation(), ".jly", tr("簡譜文字")
                source = None
            elif kind == "musicxml":
                if not self.recognized_xml:
                    raise ValueError(tr("目前沒有照片辨識產生的 MusicXML。請先匯入照片。"))
                source, content = self.recognized_xml, None
                suffix, description = source.suffix, tr("MusicXML 辨識結果")
            else:
                if not self.draft_confirmed():
                    self.drop_pending_output()
                    return
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
                    raise ValueError(tr("這份樂譜未產生 MIDI。"))
                suffix, description, content = source.suffix, kind.upper(), None
            # Snapshot before the native dialog runs its nested event loop.
            # A completed render may remove the previous result directory.
            payload = content if content is not None else source.read_bytes()
            export_revision = self.revision
            name, _ = QFileDialog.getSaveFileName(self, tr("匯出 {description}").format(description=description), self.project.score.title + suffix, f"{description} (*{suffix})")
            if name:
                path = Path(name) if Path(name).suffix else Path(name + suffix)
                if self.revision != export_revision:
                    self.statusBar().showMessage(tr("樂譜在選擇檔名期間已改變，請重新匯出最新內容。"), 8000)
                    return
                atomic_write(path, payload)
                self.statusBar().showMessage(tr("已匯出：{path}").format(path=path), 6000)
        except Exception as exc:
            self.error(tr("無法匯出"), exc)

    def open_pdf(self):
        self.capture_fields()
        if not self.draft_confirmed():
            self.drop_pending_output()
            return
        result = self.result
        if not result or self.rendered_revision != self.revision or not result.pdf.is_file():
            self.output_request = ("open", "pdf")
            self.render_score(True)
            return
        from .printing import prepare_print_copy
        try:
            copy = prepare_print_copy(result.pdf, self.data_dir / "print-cache")
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(copy))):
                self.error(tr("無法開啟 PDF"), tr("請設定系統預設的 PDF 閱讀器，或先匯出 PDF。"))
        except OSError as exc:
            self.error(tr("無法開啟 PDF"), exc)

    def play_midi(self):
        if self.player.playing:
            self.stop_playback()
            return
        if self.play_after_render:
            self.play_after_render = False
            self.statusBar().showMessage(tr("已取消排版完成後的試聽。"), 4000)
            self.update_transport()
            return
        self.capture_fields()
        midi, order, note = self.preview_file()
        if midi is None and note:
            self.statusBar().showMessage(note, 8000)
        if midi is None and (not self.result or self.rendered_revision != self.revision):
            self.play_after_render = True
            self.update_transport()
            self.render_score(True, create_pdf=False)
            return
        result = self.result
        if midi is not None or (result and result.midi):
            from .playmap import PlayMap
            try:
                self.play_map = PlayMap(self.project.score, self.part, order)
            except Exception:
                self.play_map = None   # position display is optional: never block playback
            if not note and order is not None and self.jump_with_repeats():
                note = tr("試聽：D.C./D.S. 回頭那一遍會再奏一次中間的反覆（和匯出的 MIDI 相同；印出的譜不受影響）。")
                self.statusBar().showMessage(note, 8000)
            elif note:
                self.statusBar().showMessage(note, 8000)
                self.toast.show_text(note)
            start = 0.0
            bars = self.selected_measures()
            if bars and self.play_map is not None:
                start = self.play_map.first_time_of_bar(bars[0]) or 0.0
            path = midi if midi is not None else result.midi
            try:
                self.player.play(path, start) if start else self.player.play(path)
            except RuntimeError as exc:
                self.error(tr("無法播放 MIDI"), exc)
                return
            self.guide.mark(4)
            self.play_timer.start()
            self.update_transport()

    def preview_file(self):
        """(path, bar order, notice) of the preview MIDI written straight from the score, so
        playback never waits for LilyPond; (None, None, "") when the score cannot be played
        that way (then the engine's MIDI is used). An unfinished repeat or jump plays the
        bars as written, and the notice says so."""
        from .preview_midi import playing_order, preview_midi
        score = self.project.score
        try:
            score.validate()
            if not any(p.notes for p in score.parts) or score.edit_error() is not None:
                return None, None, ""
            order, structured = playing_order(score)
            data = preview_midi(score, order)
            folder = self.data_dir / "preview"
            folder.mkdir(parents=True, exist_ok=True)
            # A new name each time: Windows MCI may still hold the previous file open.
            self._preview_serial = getattr(self, "_preview_serial", 0) + 1
            for old in folder.glob("preview-*.mid"):
                try:
                    old.unlink()
                except OSError:
                    pass
            path = folder / f"preview-{os.getpid()}-{self._preview_serial}.mid"
            path.write_bytes(data)
        except OSError:
            return None, None, ""
        except Exception as exc:     # noqa: BLE001 -- a preview bug must never block playback
            # The engine's MIDI plays instead; say why (PreviewError names the note).
            return None, None, tr("無法直接產生試聽（{reason}），改用排版引擎的 MIDI。").format(reason=exc)
        notice = "" if structured else tr("反覆或跳躍記號還沒完成，這次試聽依書寫順序播放（不反覆、不跳躍）。")
        return path, order, notice

    def jump_with_repeats(self) -> bool:
        from .notation_rules import bar_items
        items = bar_items(self.project.score)
        ends = {i for _, e in items for i in e}
        return bool(ends & {"nav.dc", "nav.ds"}) and bool(ends & {"bar.repeat_end", "bar.repeat_both"})

    def stop_playback(self):
        self.player.stop()
        self.play_timer.stop()
        if self.notes.play_note is not None:
            self.notes.play_note = None
            self.notes.viewport().update()
        self.update_transport()

    def playback_tick(self):
        if not self.player.playing:
            self.stop_playback()
            return
        position = self.player.position()
        if self.play_map is not None and self.play_map.part != self.part:
            # Switching parts while playing: the same music, but this part's notes and rows.
            from .playmap import PlayMap
            try:
                # Keep the bar order actually playing (an unfinished structure plays as
                # written; a later edit must not re-route the highlight mid-performance).
                self.play_map = PlayMap(self.project.score, self.part, self.play_map.order)
            except Exception:
                self.play_map = None
        onset = self.play_map.at(position) if self.play_map is not None else None
        if onset is not None and onset.row < len(self.project.score.parts[self.part].notes):
            note = (self.part, onset.row)
            if note != self.notes.play_note:
                self.notes.play_note = note
                line = self.notes.note_line(*note)
                if line is not None:
                    self.notes.scroll_to_system(line)
                self.notes.viewport().update()
        self.update_transport(position, onset)

    def update_transport(self, position=None, onset=None):
        """Idle: 試聽 · 從第 7 小節; playing: 0:24 / 0:52 · 第 6 小節 (design §1.2)."""
        if not hasattr(self, "transport_label"):
            return
        playing = self.player.playing
        icons.attach(self.play_button, "stop" if playing else "play", "onaccent", 14)
        self.play_button.setToolTip(tr("停止（Enter）") if playing else tr("Enter 試聽 / 停止（音符區內）"))
        score = self.project.score
        compact = self.width() <= 1240
        if playing:
            pos = position if position is not None else self.player.position()
            total = self.play_map.total_seconds if self.play_map is not None else 0
            pos = min(pos, total) if total else pos      # the synth's release tail never shows 0:53 / 0:52
            text = f"{int(pos) // 60}:{int(pos) % 60:02d}"
            if total:
                text += f" / {int(total) // 60}:{int(total) % 60:02d}"
            if onset is not None:
                from .notation_edit import bar_number
                text += " · " + tr("第 {bar} 小節").format(bar=bar_number(score, onset.bar))
        elif self.play_after_render:
            text = tr("準備試聽…")
        else:
            bars = self.selected_measures() if hasattr(self, "notes") else []
            if bars:
                from .notation_edit import bar_number
                where = tr("從第 {bar} 小節").format(bar=bar_number(score, bars[0]))
            else:
                where = tr("從頭")
            text = where if compact else tr("試聽 · {where}").format(where=where)
        self.transport_label.setText(text)

    def fit_pdf(self):
        self.pdf_view.setZoomMode(QPdfView.ZoomMode.FitToWidth)

    def zoom(self, factor):
        zoom = self.pdf_view.zoomFactor() * factor
        self.pdf_view.setZoomMode(QPdfView.ZoomMode.Custom)
        self.pdf_view.setZoomFactor(max(0.2, min(4.0, zoom)))

    def import_musicxml(self):
        if not self.guard_unsaved():
            return
        name, _ = QFileDialog.getOpenFileName(self, tr("匯入 MusicXML"), "", "MusicXML (*.musicxml *.xml *.mxl)")
        if not name:
            return
        output = self.job_dir()
        self.start_task(tr("正在匯入 MusicXML…"), lambda cancel: self.convert_xml(Path(name), output, cancel), self.import_complete)

    @staticmethod
    def convert_xml(path, output, cancel):
        xml = read_xml(path)
        project, message = visual_import(xml)
        if project is None:
            raise ValueError(tr("這份 MusicXML 含有目前尚不能完整圖形化的聲部、歌詞或記號。"
                                "為避免遺失音樂內容，本次未匯入，原檔及目前樂譜不變。"))
        return project, message

    def import_complete(self, result):
        project, message = result
        self.reset(project, dirty=True)
        self.log.setPlainText(message)
        self.preview_status.setText(message)
        QMessageBox.information(self, tr("匯入完成 · 請校對"), message)

    def import_photo(self):
        if not self.guard_unsaved():
            return
        audiveris = self.current_tool("audiveris")
        if not audiveris:
            self.error(tr("尚未找到照片辨識工具"), tr("完整版安裝檔已附 Audiveris 與 Java。若使用原始碼啟動，請在工具設定指定 Audiveris 執行檔。"))
            self.tool_settings()
            return
        name, _ = QFileDialog.getOpenFileName(self, tr("選擇五線譜照片"), "", tr("樂譜照片 (*.png *.jpg *.jpeg *.tif *.tiff *.bmp)"))
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
            self.error(tr("無法讀取照片"), exc)
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
            self.preview_status.setText(tr("照片已轉為簡譜。請對照原圖核對調號、音高、拍數與反覆記號。"))
            QMessageBox.information(self, tr("辨識完成 · 請校對"), tr("{message}\n\n辨識可能有誤，請使用「原圖對照」逐小節校對。可匯出 MusicXML，在 Audiveris 等工具進一步修正。").format(message=message))
        self.start_task(tr("正在辨識五線譜照片…通常需要 1–5 分鐘，可隨時取消。"), job, complete)

    def tool_settings(self):
        if self.task:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(tr("工具與環境設定"))
        dialog.resize(720, 280)
        layout = QVBoxLayout(dialog)
        layout.addWidget(label(tr("完整版會自動找到內附工具。通常不需要更改；清空欄位可恢復自動偵測。"), wrap=True))
        form = QFormLayout()
        fields = {}
        for name, display in (("lilypond", tr("LilyPond 排譜引擎")),):
            row = QHBoxLayout()
            field = QLineEdit(str(self.settings.value(name, "")))
            found = self.current_tool(name)
            field.setPlaceholderText(str(found) if found else tr("尚未偵測到"))
            fields[name] = field
            row.addWidget(field)
            def browse(_, target=field):
                path, _ = QFileDialog.getOpenFileName(dialog, tr("選取執行檔"), "", tr("執行檔 (*.exe);;所有檔案 (*)") if sys.platform == "win32" else tr("所有檔案 (*)"))
                if path:
                    target.setText(path)
            row.addWidget(button(tr("瀏覽…"), browse))
            form.addRow(display, row)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText(tr("儲存"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("取消"))
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
            self.error(tr("無法開啟 Audiveris"), exc)

    def show_help(self):
        """F1: the searchable shortcut list, non-modal so it can stay open while writing."""
        from .shortcuts import ShortcutsDialog
        if self.shortcuts_dialog is None:
            self.shortcuts_dialog = ShortcutsDialog(self)
        self.shortcuts_dialog.show()
        self.shortcuts_dialog.raise_()
        self.shortcuts_dialog.activateWindow()

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
            copy_button = box.addButton(tr("複製完整紀錄"), QMessageBox.ButtonRole.ActionRole)
            copy_button.clicked.connect(lambda: QApplication.clipboard().setText(message + "\n\n" + details))
        box.exec()

    def closeEvent(self, event):
        if self.task:
            self.close_requested = True
            self.cancel_task()
            self.statusBar().showMessage(tr("正在取消作業，完成後關閉視窗…"))
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
        i18n.off_change(self.retranslate)
        self.autosave.stop()
        self.stop_playback()
        self.autopreview.stop()
        self.clear_recovery()
        self.clear_pdf_preview()
        self.temp.cleanup()
        event.accept()
