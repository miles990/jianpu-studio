"""The 0.4.0 workspace (wow/design.md §1): toolbar, score bar, note bar, canvas, side panel, status.

Widgets keep the 0.3.x attribute names where the behaviour is the same (``notes``,
``title_field``, ``pitch_buttons``, ``input_mode`` ...). The hidden combo boxes (degree,
duration, octave, accidental, insert position, input mode) still hold the note-entry
state; the visible buttons drive and mirror them, so keyboard and mouse share one path.
"""
from __future__ import annotations

from PySide6.QtCore import QMargins, QRectF, QSize, Qt
from PySide6.QtGui import QFont, QPainter, QPixmap
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QProgressBar,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import __version__, icons
from .i18n import N_, tr
from .inspector import SelectionInspector
from .model import DURATIONS, KEYS, METERS, key_label
from .palette import SymbolPalette
from .score_view import NoteList
from .widgets import label, push, rule, segment, tool

DURATION_KEYS = ((1, "Q"), (2, "W"), (4, "E"), (8, "R"), (16, "A"), (32, "S"))
DURATION_TIPS = {1: N_("全音符（Q）"), 2: N_("二分音符（W）"), 4: N_("四分音符（E）"), 8: N_("八分音符（R）"),
                 16: N_("十六分音符（A）"), 32: N_("三十二分音符（S）")}  # catalog sources; show tr(value)
SIDE_WIDTH, SIDE_NARROW = 328, 276


class KeyButton(QToolButton):
    """A note-bar key with a small keyboard letter in its corner (design §1.2)."""

    def __init__(self, corner=""):
        super().__init__()
        self.corner = corner

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.corner:
            return
        painter = QPainter(self)
        font = QFont(self.font())
        font.setPixelSize(9)
        painter.setFont(font)
        painter.setPen(self.palette().placeholderText().color())
        painter.drawText(QRectF(0, 0, self.width() - 3, self.height() - 1),
                         Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom, self.corner)
        painter.end()


def app_pixmap(size: int) -> QPixmap:
    from pathlib import Path
    path = Path(__file__).parent / "assets" / "icon.png"
    pixmap = QPixmap(str(path))
    if pixmap.isNull():
        return QPixmap()
    ratio = 2.0
    scaled = pixmap.scaled(QSize(size, size) * ratio, Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
    scaled.setDevicePixelRatio(ratio)
    return scaled


def build_workspace(self, label_factory=None, button_factory=None):
    central = QWidget()
    central.setObjectName("central")
    self.setCentralWidget(central)
    root = QVBoxLayout(central)
    root.setContentsMargins(0, 0, 0, 0)
    root.setSpacing(0)
    _toolbar(self, root)
    _scorebar(self, root)
    self.split = QSplitter(Qt.Orientation.Horizontal)
    self.split.setObjectName("main_split")
    self.split.setChildrenCollapsible(False)
    self.split.setHandleWidth(5)       # a draggable divider; the panel draws the 1 px line itself
    _score_column(self)
    _side_panel(self)
    _preview_panel(self)
    self.split.addWidget(self.editor_container)
    self.split.addWidget(self.preview_panel)
    self.split.addWidget(self.side_panel)
    self.split.setStretchFactor(0, 1)
    self.split.setStretchFactor(1, 1)
    self.split.setStretchFactor(2, 0)
    root.addWidget(self.split, 1)
    self.progress = QProgressBar()
    self.progress.setRange(0, 0)
    self.progress.setTextVisible(False)
    self.progress.setMaximumHeight(3)
    self.progress.hide()
    root.addWidget(self.progress)
    _status(self)
    self.change_workspace(0)


# ---------------------------------------------------------------- toolbar
def _toolbar(self, root):
    bar = QFrame()
    bar.setObjectName("toolbar")
    bar.setFixedHeight(48)
    row = QHBoxLayout(bar)
    row.setContentsMargins(12, 6, 12, 6)
    row.setSpacing(6)
    self.toolbar = bar
    self.brand = QLabel()
    self.brand.setObjectName("brand_icon")
    self.brand.setPixmap(app_pixmap(24))
    self.brand.setFixedSize(26, 26)
    row.addWidget(self.brand)
    self.file_label = label(tr("未命名樂曲"), "file_name")
    self.file_label.setMaximumWidth(220)
    row.addWidget(self.file_label)
    self.dirty_label = label(tr("● 已修改"), "dirty_mark")
    row.addWidget(self.dirty_label)
    row.addSpacing(8)
    self.new_button = tool(tr("新建"), "doc-new", tr("新建（Ctrl+N）"), "ghost", self.new_project, name="new_button")
    self.open_button = tool(tr("開啟"), "folder-open", tr("開啟（Ctrl+O）"), "ghost", self.open_file, name="open_button")
    self.save_button = tool(tr("儲存"), "save", tr("儲存（Ctrl+S）"), "ghost", self.save, name="save_button")
    self.file_buttons = (self.new_button, self.open_button, self.save_button)
    for button in self.file_buttons:
        row.addWidget(button)
    row.addWidget(rule(True))
    self.undo_button = tool("", "undo", tr("復原（Ctrl+Z）"), "ghost", self.undo, name="undo_button")
    self.redo_button = tool("", "redo", tr("重做（Ctrl+Shift+Z）"), "ghost", self.redo, name="redo_button")
    for button in (self.undo_button, self.redo_button):
        button.setFixedSize(32, 32)
        row.addWidget(button)
    row.addStretch(1)
    transport = QFrame()
    transport.setObjectName("transport")
    transport.setFixedHeight(36)
    t = QHBoxLayout(transport)
    t.setContentsMargins(4, 2, 6, 2)
    t.setSpacing(8)
    self.play_button = tool("", "play", tr("Enter 試聽 / 停止（音符區內）"), "primary", self.play_midi,
                            name="play_button", icon_role="onaccent", size=14)
    self.play_button.setFixedSize(28, 28)
    self.play_button.setStyleSheet("QToolButton { border-radius: 14px; padding: 0px; }")
    t.addWidget(self.play_button)
    self.transport_label = label("", "transport_label")
    t.addWidget(self.transport_label)
    self.enter_cap = label("Enter", "keycap", role="hint")
    t.addWidget(self.enter_cap)
    self.transport = transport
    row.addWidget(transport)
    row.addStretch(1)
    self.preview_button_top = tool(tr("列印預覽"), "print", tr("列印預覽（F5）"), "", self.show_print_preview,
                                   name="print_preview_button")
    row.addWidget(self.preview_button_top)
    self.export_button = QToolButton()
    self.export_button.setObjectName("export_button")
    self.export_button.setText(tr("匯出"))
    self.export_button.setProperty("kind", "primary")
    icons.attach(self.export_button, "export", "onaccent", 16)
    self.export_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    self.export_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    self.export_button.setToolTip(tr("匯出 PDF（Ctrl+E）、MIDI，或開啟 PDF 列印"))
    menu = QMenu(self)
    menu.addAction(icons.icon("file-pdf"), tr("PDF 樂譜…"), lambda: self.export("pdf"))
    menu.addAction(icons.icon("file-midi"), tr("MIDI 音樂…"), lambda: self.export("midi"))
    menu.addSeparator()
    menu.addAction(icons.icon("print"), tr("列印預覽…"), self.show_print_preview)
    menu.addAction(tr("開啟 PDF / 列印"), self.open_pdf)
    self.export_button.setMenu(menu)
    row.addWidget(self.export_button)
    root.addWidget(bar)


# ---------------------------------------------------------------- score settings row
def _scorebar(self, root):
    bar = QFrame()
    bar.setObjectName("scorebar")
    bar.setFixedHeight(44)
    self.score_settings = bar
    row = QHBoxLayout(bar)
    row.setContentsMargins(12, 4, 12, 4)
    row.setSpacing(6)
    self.title_field = QLineEdit()
    self.title_field.setObjectName("title_field")
    self.title_field.setAccessibleName(tr("曲名"))
    self.title_field.setPlaceholderText(tr("未命名樂曲"))
    self.title_field.setMinimumWidth(90)
    self.title_field.setMaximumWidth(220)
    row.addWidget(self.title_field, 2)
    self.composer_label = label(tr("來源"), role="muted")
    row.addWidget(self.composer_label)
    self.composer_field = QLineEdit()
    self.composer_field.setAccessibleName(tr("作曲者 / 來源"))
    self.composer_field.setPlaceholderText(tr("作曲者 / 來源"))
    self.composer_field.setMinimumWidth(60)
    self.composer_field.setMaximumWidth(170)
    row.addWidget(self.composer_field, 1)
    self.composer_rule = rule(True)
    row.addWidget(self.composer_rule)
    self.key_field = QComboBox()
    for key in KEYS:
        self.key_field.addItem(key_label(key), key)
    self.key_field.setAccessibleName(tr("調號"))
    self.meter_field = QComboBox()
    self.meter_field.addItems(METERS)
    self.meter_field.setAccessibleName(tr("拍號"))
    self.tempo_field = QSpinBox()
    self.tempo_field.setRange(20, 300)
    self.tempo_field.setPrefix("♩ = ")
    self.tempo_field.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
    self.tempo_field.setAccessibleName(tr("速度"))
    self.tempo_field.setToolTip(tr("速度（每分鐘拍數）：滾輪或 ↑↓ 調整"))
    self.tempo_field.setMinimumWidth(70)
    self.pickup_field = QComboBox()
    self.pickup_field.setObjectName("pickup_field")
    self.pickup_field.setAccessibleName(tr("弱起"))
    self.pickup_field.setToolTip(tr("弱起小節的長度；只列出可以用的長度"))
    self.setting_labels = []
    for text, widget in ((tr("調 1 ="), self.key_field), (tr("拍號"), self.meter_field),
                         (tr("速度"), self.tempo_field), (tr("弱起"), self.pickup_field)):
        caption = label(text, role="muted")
        self.setting_labels.append(caption)
        row.addWidget(caption)
        row.addWidget(widget)
    row.addStretch(1)
    self.parts_label = label(tr("聲部"), role="muted")
    row.addWidget(self.parts_label)
    self.part_host = QWidget()
    self.part_row = QHBoxLayout(self.part_host)
    self.part_row.setContentsMargins(0, 0, 0, 0)
    self.part_row.setSpacing(4)
    self.part_buttons = []
    self.part_group = QButtonGroup(self)
    row.addWidget(self.part_host)
    self.add_part_button = tool("", "part-add", tr("新增聲部（與第一聲部等長的休止符）"), "ghost",
                                self.add_part, name="add_part_button")
    self.add_part_button.setFixedSize(30, 30)
    row.addWidget(self.add_part_button)
    self.layout_rule = rule(True)
    row.addWidget(self.layout_rule)
    self.layout_button = tool(tr("紙張與排版"), "page-setup", tr("紙張、譜字大小、每行小節數與技法字印繁／簡"),
                              "", self.layout_settings, name="layout_button")
    row.addWidget(self.layout_button)
    self.more_button = tool("", "more", tr("曲目資訊、歌詞與排版"), "ghost", name="score_more_button")
    self.more_button.setFixedSize(30, 30)
    self.more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    self.more_menu = QMenu(self)
    self.more_menu.addAction(tr("曲目資訊與歌詞…"), self.show_score_info)
    self.more_menu.addAction(tr("紙張與排版…"), self.layout_settings)
    self.more_button.setMenu(self.more_menu)
    row.addWidget(self.more_button)
    root.addWidget(bar)
    # Lyrics and staff live in the score-information popup (part-specific lyrics).
    self.info_popup = QFrame(self, Qt.WindowType.Popup)
    self.info_popup.setObjectName("info_popup")
    info = QVBoxLayout(self.info_popup)
    info.setContentsMargins(12, 12, 12, 12)
    info.setSpacing(6)
    self.info_heading = label(tr("曲目資訊與歌詞"), role="title")
    info.addWidget(self.info_heading)
    self.info_composer_label = label(tr("來源"), role="muted")
    info.addWidget(self.info_composer_label)
    self.info_composer = QLineEdit()
    self.info_composer.setAccessibleName(tr("作曲者 / 來源"))
    self.info_composer.setPlaceholderText(tr("作曲者 / 來源"))
    self.info_composer.editingFinished.connect(lambda: (self.composer_field.setText(self.info_composer.text()),
                                                         self.capture_fields()))
    info.addWidget(self.info_composer)
    self.lyrics_label = label(tr("歌詞（目前聲部）"), role="muted")
    info.addWidget(self.lyrics_label)
    self.lyrics = QLineEdit()
    self.lyrics.setPlaceholderText(tr("歌詞（選填；純器樂可留白）"))
    self.lyrics.setMinimumWidth(320)
    self.lyrics.editingFinished.connect(self.capture_fields)
    info.addWidget(self.lyrics)
    self.staff = QCheckBox(tr("五線譜對照"))
    self.staff.toggled.connect(self.capture_fields)
    info.addWidget(self.staff)
    self.title_field.editingFinished.connect(self.capture_fields)
    self.composer_field.editingFinished.connect(self.capture_fields)
    self.key_field.currentTextChanged.connect(self.capture_fields)
    self.meter_field.currentTextChanged.connect(self.capture_fields)
    self.tempo_field.valueChanged.connect(self.capture_fields)
    self.pickup_field.currentIndexChanged.connect(self.change_pickup)


# ---------------------------------------------------------------- note bar + canvas
def _score_column(self):
    self.editor = QWidget()
    self.editor.setObjectName("editor")
    column = QVBoxLayout(self.editor)
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(0)
    _notebar(self, column)
    from .startpage import GuideBar, StartPage
    self.guide = GuideBar(self)
    column.addWidget(self.guide)
    self.editor_stack = QStackedWidget()
    canvas_page = QWidget()
    canvas_layout = QVBoxLayout(canvas_page)
    canvas_layout.setContentsMargins(0, 0, 0, 0)
    canvas_layout.setSpacing(0)
    self.measure_notice = label("", "notice", True)
    self.measure_notice.hide()
    canvas_layout.addWidget(self.measure_notice)
    self.notes = NoteList()
    self.notes.setMinimumHeight(200)
    self.notes.pitch.connect(self.input_pitch)
    self.notes.delete.connect(self.delete_note)
    self.notes.currentRowChanged.connect(self.select_note)
    self.notes.selectionEdited.connect(self.update_selection)
    self.notes.command.connect(self.note_command)
    self.notes.partClicked.connect(self.part_clicked)
    self.notes.stampClicked.connect(self.stamp_clicked)
    self.notes.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    self.notes.customContextMenuRequested.connect(self.note_menu)
    canvas_layout.addWidget(self.notes, 1)
    self.editor_stack.addWidget(canvas_page)
    self.start_page = StartPage(self)
    self.editor_stack.addWidget(self.start_page)
    column.addWidget(self.editor_stack, 1)
    from .startpage import Toast
    self.toast = Toast(self.notes)
    self.stamp_bar = label("", "notice", True)
    self.stamp_bar.hide()
    canvas_layout.insertWidget(0, self.stamp_bar)
    self.editor_container = QWidget()
    container = QVBoxLayout(self.editor_container)
    container.setContentsMargins(0, 0, 0, 0)
    container.addWidget(self.editor)
    self.editor_container.setMinimumWidth(560)


def _notebar(self, column):
    bar = QFrame()
    bar.setObjectName("notebar")
    bar.setFixedHeight(46)
    self.note_tools = bar
    row = QHBoxLayout(bar)
    row.setContentsMargins(10, 6, 10, 6)
    row.setSpacing(3)
    # hidden state holders (0.3.x names)
    self.input_mode = QComboBox(bar)
    self.input_mode.addItem(tr("新增音符"), "insert")
    self.input_mode.addItem(tr("修改音符"), "edit")
    self.input_mode.addItem(tr("填寫小節"), "fill")
    self.input_mode.hide()
    self.degree = QComboBox(bar)
    for value in range(8):
        self.degree.addItem(tr("休止 0") if value == 0 else tr("音高 {degree}").format(degree=value), value)
    self.degree.setCurrentIndex(1)
    self.duration = QComboBox(bar)
    for value, text in DURATIONS.items():
        self.duration.addItem(tr(text), value)
    self.duration.setCurrentIndex(2)
    self.octave = QComboBox(bar)
    for value, text in ((-2, tr("低兩八度")), (-1, tr("低八度")), (0, tr("中音")), (1, tr("高八度")), (2, tr("高兩八度"))):
        self.octave.addItem(text, value)
    self.octave.setCurrentIndex(2)
    self.accidental = QComboBox(bar)
    for text, value in ((tr("無升降"), ""), (tr("升 ♯"), "#"), (tr("降 ♭"), "b"), (tr("還原 ♮"), "n")):
        self.accidental.addItem(text, value)
    for field, widget in (("degree", self.degree), ("duration", self.duration), ("octave", self.octave),
                          ("accidental", self.accidental)):
        widget.hide()
        widget.setPlaceholderText(tr("未選取"))
        widget.setAccessibleName({"degree": tr("音高"), "duration": tr("時值"), "octave": tr("八度"),
                                  "accidental": tr("升降")}[field])
        widget.currentIndexChanged.connect(lambda _, f=field, w=widget: self.edit_property(f, w.currentData()))
    self.insert_position = QComboBox(bar)
    for text, value in ((tr("插在選取後"), "after"), (tr("插在選取前"), "before"), (tr("加到曲尾"), "end")):
        self.insert_position.addItem(text, value)
    self.insert_position.hide()
    self.insert_position.currentIndexChanged.connect(self.update_selection)
    self.dotted = QCheckBox(tr("附點"), bar)
    self.dotted.hide()
    self.dotted.checkStateChanged.connect(
        lambda state: self.edit_property("dotted", state == Qt.CheckState.Checked)
        if state != Qt.CheckState.PartiallyChecked else None)
    # mode segmented control
    self.input_mode_group = QButtonGroup(self)
    self.input_mode_group.setExclusive(True)
    self.input_mode_buttons = {}
    mode_buttons = []
    for index, (name, text, mode, hint, icon_name) in enumerate((
            ("insert_mode_button", tr("新增"), "insert", tr("新增音符：數字鍵加入新音符（空白鍵切換）"), "mode-insert"),
            ("edit_mode_button", tr("修改"), "edit", tr("修改音符：數字鍵改所選音符，保留其他屬性"), "mode-edit"),
            ("fill_mode_button", tr("填寫"), "fill", tr("填寫小節：以音符取代所選休止符，小節總拍數不變"), "mode-fill"))):
        control = tool(text, tip=hint, name=name, checkable=True)
        control.clicked.connect(lambda checked=False, i=index: (self.input_mode.setCurrentIndex(i), self.notes.setFocus()))
        self.input_mode_group.addButton(control, index)
        self.input_mode_buttons[mode] = control
        setattr(self, name, control)
        mode_buttons.append(control)
        row.addWidget(control)
    segment(mode_buttons)
    self.insert_mode_button.setChecked(True)
    self.input_mode.currentIndexChanged.connect(lambda index: self.input_mode_group.button(index).setChecked(True))
    self.input_mode.currentIndexChanged.connect(self.update_selection)
    row.addSpacing(4)
    self.pitch_buttons = []
    for degree in (1, 2, 3, 4, 5, 6, 7, 0):
        control = KeyButton()
        control.setText(str(degree))
        control.setProperty("kind", "key")
        control.setObjectName("pitch")
        control.setFixedSize(30, 30)
        control.setToolTip(tr("數字鍵 {degree} · {kind}").format(degree=degree, kind=tr("休止符") if degree == 0 else tr("音符")))
        control.clicked.connect(lambda checked=False, d=degree: self.input_pitch(d))
        self.pitch_buttons.append(control)
        row.addWidget(control)
    row.addSpacing(4)
    self.duration_buttons = {}
    self.duration_group = QButtonGroup(self)
    for value, key in DURATION_KEYS:
        control = KeyButton(key)
        control.setProperty("kind", "dur")
        control.setCheckable(True)
        icons.attach(control, f"dur-{value}", "text", 22)
        control.setToolTip(tr(DURATION_TIPS[value]))
        control.setAccessibleName(tr(DURATION_TIPS[value]))
        control.setFixedSize(32, 30)
        control.clicked.connect(lambda checked=False, v=value: self.duration_clicked(v))
        self.duration_group.addButton(control)
        self.duration_buttons[value] = control
        row.addWidget(control)
    self.dot_button = tool("", "aug-dot", tr("附點（.）"), name="dot_button", checkable=True)
    self.dot_button.setFixedSize(30, 30)
    self.dot_button.clicked.connect(lambda: self.note_command("dot"))
    row.addWidget(self.dot_button)
    row.addSpacing(4)
    self.octave_up_button = tool("", "octave-up", tr("高八度（↑）"), name="octave_up_button")
    self.octave_down_button = tool("", "octave-down", tr("低八度（↓）"), name="octave_down_button")
    for control, command in ((self.octave_up_button, "raise"), (self.octave_down_button, "lower")):
        control.setFixedSize(30, 30)
        control.clicked.connect(lambda checked=False, c=command: self.note_command(c))
        row.addWidget(control)
    row.addSpacing(4)
    self.extra_host = QWidget()
    extra = QHBoxLayout(self.extra_host)
    extra.setContentsMargins(0, 0, 0, 0)
    extra.setSpacing(4)
    self.accidental_buttons = {}
    for value, icon_name, tip in (("#", "sharp", N_("升記號 ♯")), ("b", "flat", N_("降記號 ♭")), ("n", "natural", N_("還原記號 ♮"))):
        control = tool("", icon_name, tr(tip), name=f"accidental_{icon_name}", checkable=True)
        control.setFixedSize(30, 30)
        control.clicked.connect(lambda checked=False, v=value: self.accidental_clicked(v))
        self.accidental_buttons[value] = control
        extra.addWidget(control)
    extra.addSpacing(4)
    self.tie_button = tool("", "tie", tr("延音線：連到下一個同音高的音"), name="tie_button")
    self.tie_button.setFixedSize(30, 30)
    self.tie_button.clicked.connect(lambda: self.palette_apply("line.tie", "click"))
    extra.addWidget(self.tie_button)
    self.triplet_button = QToolButton()
    self.triplet_button.setObjectName("triplet_button")
    icons.attach(self.triplet_button, "triplet", "text", 18)
    self.triplet_button.setToolTip(tr("將所選一音平均拆成三連音；箭頭可將整組合併。"))
    self.triplet_button.setAccessibleName(tr("三連音"))
    self.triplet_button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
    self.triplet_button.setFixedSize(42, 30)
    self.triplet_button.clicked.connect(lambda: self.triplet_action())
    triplets = QMenu(self)
    triplets.addAction(tr("拆成三連音"), lambda: self.triplet_action())
    triplets.addAction(tr("合併三連音"), lambda: self.triplet_action(True))
    self.triplet_button.setMenu(triplets)
    extra.addWidget(self.triplet_button)
    row.addWidget(self.extra_host)
    row.addStretch(1)
    self.add_bar_button = QToolButton()
    self.add_bar_button.setObjectName("add_bar_button")
    self.add_bar_button.setText(tr("小節"))
    icons.attach(self.add_bar_button, "bars", "text", 16)
    self.add_bar_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    self.add_bar_button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
    self.add_bar_button.setToolTip(tr("在所選小節後新增；箭頭可選前插、曲尾、重複、清空或刪除。"))
    self.add_bar_button.clicked.connect(lambda: self.insert_bars("after"))
    self.bar_menu = QMenu(self)
    self.bar_menu.aboutToShow.connect(lambda: (self.bar_menu.clear(), self.add_measure_menu(self.bar_menu)))
    self.add_bar_button.setMenu(self.bar_menu)
    row.addWidget(self.add_bar_button)
    self.note_more_button = tool(tr("更多"), "more", tr("升降、延音線、三連音與小節操作"), name="note_more_button")
    self.note_more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    self.note_more_menu = QMenu(self)
    self.note_more_menu.aboutToShow.connect(self.fill_note_more_menu)
    self.note_more_button.setMenu(self.note_more_menu)
    self.note_more_button.hide()
    row.addWidget(self.note_more_button)
    self.bar_jump = QSpinBox(bar)
    self.bar_jump.setRange(1, 10000)
    self.bar_jump.hide()
    self.bar_jump.editingFinished.connect(self.jump_bar)
    self.selection_buttons = []
    self.workspace_view = QComboBox(self)
    self.workspace_view.addItem(tr("編輯樂譜"), "score")
    self.workspace_view.hide()
    self.workspace_view.currentIndexChanged.connect(self.change_workspace)
    column.addWidget(bar)


# ---------------------------------------------------------------- side panel
def _side_panel(self):
    panel = QFrame()
    panel.setObjectName("sidepanel")
    panel.setMinimumWidth(SIDE_NARROW)
    panel.setMaximumWidth(360)
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    self.inspector = SelectionInspector(self.theme.colors)
    self.inspector.removeRequested.connect(self.remove_mark)
    self.inspector.measureAction.connect(self.inspector_measure_action)
    self.inspector.graceRequested.connect(lambda: self.notation_settings(2))
    layout.addWidget(self.inspector)
    layout.addWidget(rule())
    self.palette = SymbolPalette(self.settings, self.theme.colors, self.symbol_state)
    self.palette.where = self.symbol_where
    self.palette.applyRequested.connect(self.palette_apply)
    self.palette.backToScore.connect(lambda: self.notes.setFocus(Qt.FocusReason.ShortcutFocusReason))
    self.palette.hoverChanged.connect(self.palette_hover)
    self.palette.continuousChanged.connect(self.set_continuous)
    layout.addWidget(self.palette, 1)
    self.side_panel = panel
    # Measure actions live in the inspector when bars are selected (the note bar's 「小節 ▾」
    # holds the same commands and collapses into 「更多」 in small windows).
    self.measure_tools = self.inspector.actions
    self.measure_controls = [self.inspector.measure_buttons[k] for k in ("before", "after", "duplicate", "clear", "delete")]


# ---------------------------------------------------------------- print preview
def _preview_panel(self):
    self.preview_panel = QWidget()
    self.preview_panel.setObjectName("preview_panel")
    pv = QVBoxLayout(self.preview_panel)
    pv.setContentsMargins(12, 8, 12, 0)
    pv.setSpacing(6)
    row = QHBoxLayout()
    self.preview_heading = label(tr("列印預覽"), "section")
    row.addWidget(self.preview_heading)
    row.addStretch()
    self.auto_check = QCheckBox(tr("自動"))
    self.auto_check.setChecked(True)
    self.auto_check.toggled.connect(self.toggle_auto)
    row.addWidget(self.auto_check)
    self.preview_button = push(tr("更新"), lambda: self.render_score(True), "primary")
    self.preview_button.setObjectName("primary")
    row.addWidget(self.preview_button)
    row.addWidget(push(tr("返回樂譜"), lambda: self.change_workspace(0)))
    pv.addLayout(row)
    self.preview_status = label(tr("需要列印或匯出 PDF 時才排版。"), "muted", True)
    pv.addWidget(self.preview_status)
    self.preview_tabs = QTabWidget()
    self.preview_tabs.tabBar().hide()
    pdf_page = QWidget()
    pdf_layout = QVBoxLayout(pdf_page)
    pdf_layout.setContentsMargins(4, 6, 4, 0)
    row = QHBoxLayout()
    for text, fn in (("−", lambda: self.zoom(.8)), (tr("符合寬度"), self.fit_pdf), ("＋", lambda: self.zoom(1.25))):
        row.addWidget(push(text, fn))
    row.addStretch()
    row.addWidget(push(tr("紙張設定"), self.layout_settings))
    row.addWidget(push(tr("匯出 PDF"), lambda: self.export("pdf")))
    row.addWidget(push(tr("開啟列印"), self.open_pdf))
    pdf_layout.addLayout(row)
    self.pdf_document = QPdfDocument(self)
    self.pdf_view = QPdfView()
    self.pdf_view.setDocument(self.pdf_document)
    self.pdf_view.setPageMode(QPdfView.PageMode.MultiPage)
    self.pdf_view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
    self.pdf_view.setDocumentMargins(QMargins(10, 10, 10, 10))
    pdf_layout.addWidget(self.pdf_view, 1)
    self.preview_tabs.addTab(pdf_page, "PDF")
    photo_page = QWidget()
    photo_layout = QVBoxLayout(photo_page)
    photo_layout.setContentsMargins(4, 6, 4, 0)
    row = QHBoxLayout()
    for text, fn in (("−", lambda: self.zoom_photo(.8)), (tr("符合寬度"), lambda: self.zoom_photo(0)), ("＋", lambda: self.zoom_photo(1.25))):
        row.addWidget(push(text, fn))
    photo_layout.addLayout(row)
    self.photo_label = QLabel(tr("匯入照片後，在此放大對照原圖。"))
    self.photo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    self.photo_scroll = QScrollArea()
    self.photo_scroll.setWidgetResizable(False)
    self.photo_scroll.setWidget(self.photo_label)
    photo_layout.addWidget(self.photo_scroll, 1)
    self.preview_tabs.addTab(photo_page, tr("原圖校對"))
    self.preview_tabs.setTabVisible(1, False)
    log_page = QWidget()
    log_layout = QVBoxLayout(log_page)
    self.log = QPlainTextEdit()
    self.log.setReadOnly(True)
    log_layout.addWidget(self.log)
    log_layout.addWidget(push(tr("複製完整紀錄"), self.copy_log))
    self.preview_tabs.addTab(log_page, tr("工作紀錄"))
    pv.addWidget(self.preview_tabs, 1)


# ---------------------------------------------------------------- status bar
def _status(self):
    status = self.statusBar()
    status.setSizeGripEnabled(False)
    status.setFixedHeight(30)
    host = QWidget()
    host.setObjectName("status_host")
    row = QHBoxLayout(host)
    row.setContentsMargins(8, 0, 8, 0)
    row.setSpacing(8)
    self.rhythm_label = label("", "rhythm_label")
    self.rhythm_label.setProperty("role", "muted")
    self.rhythm_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
    self.rhythm_label.setMaximumWidth(420)
    row.addWidget(self.rhythm_label)
    self.problem_button = push(tr("下一個問題 ›"), self.goto_problem, "ghost", "problem_button")
    self.problem_button.setToolTip(tr("依序跳到每個節奏或記號問題"))
    row.addWidget(self.problem_button)
    self.fill_button = push(tr("補齊休止符"), self.fill_bar, "ghost", "fill_button")
    self.fill_button.setToolTip(tr("把最後一小節補滿休止符"))
    row.addWidget(self.fill_button)
    self.counts_label = label("", "counts_label", role="muted")
    row.addWidget(self.counts_label)
    self.guide_status = push(tr("開始寫譜 {done}/5 ›").format(done=0), self.show_guide_popup, "ghost", "guide_status")
    self.guide_status.setToolTip(tr("開始寫譜引導"))
    self.guide_status.hide()
    row.addWidget(self.guide_status)
    row.addStretch(1)
    from .widgets import ElidedLabel
    self.selection_label = ElidedLabel()
    self.selection_label.setObjectName("selection")
    self.selection_label.setProperty("role", "muted")
    self.selection_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    self.selection_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    row.addWidget(self.selection_label, 3)
    row.addStretch(1)
    self.cancel_button = push(tr("取消作業"), self.cancel_task)
    self.cancel_button.hide()
    row.addWidget(self.cancel_button)
    self.language_buttons = {}
    lang = []
    from . import i18n
    # 繁｜简 is a one-click switch (design §1.2, W-2); review: too small to notice in the
    # corner. A language icon (it also toggles) and larger, bolder keys make it findable.
    # Tooltips stay bilingual on purpose: they must be readable before switching.
    other = {i18n.TRADITIONAL: i18n.SIMPLIFIED, i18n.SIMPLIFIED: i18n.TRADITIONAL}
    self.language_icon = tool("", "language", "", "ghost", name="language_icon", icon_role="text2", size=16)
    self.language_icon.setToolTip(i18n.LANGUAGE_MENU_TITLE + " · " + " ⇄ ".join(i18n.LANGUAGE_NAMES.values()))
    self.language_icon.setAccessibleName(i18n.LANGUAGE_MENU_TITLE)
    self.language_icon.setFixedSize(24, 24)
    self.language_icon.clicked.connect(lambda: self.change_language(other[i18n.current_language()]))
    row.addWidget(self.language_icon)
    for code in i18n.LANGUAGES:
        button = tool(i18n.LANGUAGE_SHORT[code], checkable=True, name=f"language_{code}")
        button.setToolTip(i18n.LANGUAGE_MENU_TITLE + " · " + i18n.LANGUAGE_NAMES[code])
        button.setAccessibleName(i18n.LANGUAGE_NAMES[code])
        button.setProperty("lang", True)
        button.setFixedSize(36, 24)
        button.clicked.connect(lambda checked=False, c=code: self.change_language(c))
        self.language_buttons[code] = button
        lang.append(button)
        row.addWidget(button)
    segment(lang)
    self.theme_button = tool("", "theme-auto", tr("外觀：自動（跟隨系統）"), "ghost", self.cycle_theme, name="theme_button")
    self.theme_button.setFixedSize(28, 24)
    row.addWidget(self.theme_button)
    self.zoom_out_button = tool("", "zoom-out", tr("縮小（Ctrl+-）"), "ghost", lambda: self.step_zoom(-1), name="zoom_out")
    self.zoom_in_button = tool("", "zoom-in", tr("放大（Ctrl+=）"), "ghost", lambda: self.step_zoom(1), name="zoom_in")
    self.score_zoom = QComboBox()
    self.score_zoom.setObjectName("score_zoom")
    self.score_zoom.setAccessibleName(tr("譜面縮放"))
    for zoom in (75, 100, 125, 150, 200):
        self.score_zoom.addItem(f"{zoom}%", zoom)
    self.score_zoom.setCurrentIndex(1)
    self.score_zoom.currentIndexChanged.connect(lambda: self.notes.set_zoom(self.score_zoom.currentData()))
    for widget in (self.zoom_out_button, self.score_zoom, self.zoom_in_button):
        if isinstance(widget, QToolButton):
            widget.setFixedSize(26, 24)
        row.addWidget(widget)
    self.version_label = label(__version__, "current_version", role="hint")
    self.version_label.setObjectName("current_version")
    self.version_label.setAccessibleName(tr("目前版本 {version}").format(version=__version__))
    self.version_label.setToolTip(tr("說明 → 檢查更新，可安裝新的正式版本。"))
    row.addWidget(self.version_label)
    status.addPermanentWidget(host, 1)
    status.messageChanged.connect(self.status_message_changed)
    self.status_host = host
    status.showMessage(tr("就緒 · 離線編輯"))

