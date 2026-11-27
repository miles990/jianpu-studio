"""First run and discoverability (wow/design.md §5): start page, guide bar and the toast."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QPropertyAnimation, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from . import icons
from .i18n import N_, tr
from .widgets import label, push, tool

GUIDE_STEPS = (N_("點一個音符"), N_("按 1–7 改音高"), N_("在右邊點一個記號加上去"), N_("按 Enter 試聽"),
               N_("匯出 PDF 或列印"))  # catalog sources; show tr(value)
GUIDE_LAUNCHES = 5
TEMPLATES = (("blank", "template-blank", N_("空白樂譜"), N_("二胡 · D 調 · 4/4 · 8 個空小節")),
             ("solo", "template-solo", N_("二胡獨奏"), N_("一個聲部，常用記號已在右側")),
             ("duet", "template-duet", N_("二胡二重奏"), N_("兩個聲部，大括號連在一起")))
EXAMPLES = (("twinkle", N_("小星星"), N_("最簡單的單旋律")),
            ("duet", N_("晨光二重奏"), N_("二部譜、反覆、Fine 與 D.C.")))


class Card(QFrame):
    clicked = Signal()

    def __init__(self, icon_name, title, subtitle):
        super().__init__()
        self.setProperty("card", True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(170, 96)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(4)
        picture = QLabel()
        picture.setPixmap(icons.pixmap(icon_name, 28, (icons._tokens or _light())["accenttext"]))
        layout.addWidget(picture)
        self.title = label(title, role="title")
        layout.addWidget(self.title)
        self.subtitle = label(subtitle, wrap=True, role="muted")
        layout.addWidget(self.subtitle)
        layout.addStretch()
        self.setAccessibleName(title)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
            return
        super().keyPressEvent(event)


def _light():
    from .theme import LIGHT
    return LIGHT


class StartPage(QScrollArea):
    """Shown in the score column when nothing was opened before (not a dialog)."""

    def __init__(self, window):
        super().__init__()
        self.window = window
        self.setObjectName("startpage_scroll")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        body = QFrame()
        body.setObjectName("startpage")
        self.setWidget(body)
        outer = QHBoxLayout(body)
        outer.setContentsMargins(32, 28, 32, 28)
        outer.setSpacing(28)
        main = QVBoxLayout()
        main.setSpacing(10)
        self.heading = label(tr("開始寫譜"), "section")
        main.addWidget(self.heading)
        self.new_heading = label(tr("新建"), role="heading")
        main.addWidget(self.new_heading)
        grid = QGridLayout()
        grid.setSpacing(12)
        self.cards = []
        for column, (key, icon_name, title, subtitle) in enumerate(TEMPLATES):
            card = Card(icon_name, tr(title), tr(subtitle))
            card.setObjectName(f"template_{key}")
            card.clicked.connect(lambda k=key: window.new_from_template(k))
            grid.addWidget(card, 0, column)
            self.cards.append(card)
        main.addLayout(grid)
        self.example_heading = label(tr("範例"), role="heading")
        main.addWidget(self.example_heading)
        grid = QGridLayout()
        grid.setSpacing(12)
        for column, (key, title, subtitle) in enumerate(EXAMPLES):
            card = Card("example", tr(title), tr(subtitle))
            card.setObjectName(f"example_{key}")
            card.clicked.connect(lambda k=key: window.open_example(k))
            grid.addWidget(card, 0, column)
            self.cards.append(card)
        grid.setColumnStretch(2, 1)
        main.addLayout(grid)
        main.addStretch()
        outer.addLayout(main, 3)
        side = QVBoxLayout()
        side.setSpacing(6)
        self.recent_heading = label(tr("最近開啟"), role="heading")
        side.addWidget(self.recent_heading)
        self.recent_host = QVBoxLayout()
        self.recent_host.setSpacing(2)
        side.addLayout(self.recent_host)
        side.addStretch()
        outer.addLayout(side, 2)
        self.recent_widgets = []

    def refresh(self, recent: list[str]):
        for widget in self.recent_widgets:
            self.recent_host.removeWidget(widget)
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()
        self.recent_widgets = []
        if not recent:
            empty = label(tr("還沒有開過樂譜"), role="hint")
            self.recent_host.addWidget(empty)
            self.recent_widgets.append(empty)
            return
        for path in recent[:8]:
            button = tool(Path(path).name, "file-recent", path, "ghost")
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            if not Path(path).is_file():
                # A moved or deleted file stays listed (it may be on a removable drive) but says why.
                from .widgets import REASON_MARK
                button.setEnabled(False)
                button.setToolTip(path + REASON_MARK + tr("找不到這個檔案（可能已移動、改名或刪除）"))
            button.clicked.connect(lambda checked=False, p=path: self.window.open_file(path=Path(p)))
            self.recent_host.addWidget(button)
            self.recent_widgets.append(button)


class GuideBar(QFrame):
    """「開始寫譜」steps above the score; ticks itself off, never blocks (design §5)."""

    def __init__(self, window):
        super().__init__()
        self.window = window
        self.setObjectName("guidebar")
        self.setFixedHeight(34)
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 0, 8, 0)
        row.setSpacing(10)
        self.title = label(tr("開始寫譜"), role="title")
        row.addWidget(self.title)
        self.steps = []
        for index, text in enumerate(GUIDE_STEPS, 1):
            step = label(f"{'①②③④⑤'[index - 1]} {tr(text)}", f"guide_step_{index}")
            row.addWidget(step)
            self.steps.append(step)
        row.addStretch()
        self.close_button = push(tr("不再顯示 ×"), self.dismiss, "ghost", "guide_close")
        row.addWidget(self.close_button)
        self.done = set()
        self.dismissed_now = False

    def mark(self, step: int):
        if step in self.done or not self.should_show():
            return
        self.done.add(step)
        self.refresh()
        self.window.guide_status.setText(tr("開始寫譜 {done}/5 ›").format(done=len(self.done)))
        if len(self.done) == len(GUIDE_STEPS):
            self.title.setText(tr("完成了！"))

    def refresh(self):
        for index, step in enumerate(self.steps, 1):
            text = tr(GUIDE_STEPS[index - 1])
            step.setText(("✓ " if index in self.done else f"{'①②③④⑤'[index - 1]} ") + text)
            step.setProperty("role", "muted" if index in self.done else "")
            step.style().unpolish(step)
            step.style().polish(step)

    def dismiss(self):
        self.window.settings.setValue("guide/hidden", True)
        self.dismissed_now = True
        self.hide()
        self.window.guide_status.hide()

    def should_show(self) -> bool:
        settings = self.window.settings
        hidden = str(settings.value("guide/hidden", "false")).lower() == "true"
        launches = int(settings.value("guide/launches", 0) or 0)
        return not hidden and launches <= GUIDE_LAUNCHES

    def retranslate(self):
        self.refresh()
        self.title.setText(tr("完成了！") if len(self.done) == len(GUIDE_STEPS) else tr("開始寫譜"))


class Toast(QFrame):
    """Bottom-centred notice over the canvas: 已把二指換成一指　復原 Ctrl+Z (fades after 4 s)."""

    def __init__(self, host: QWidget):
        super().__init__(host)
        self.host = host
        self.setObjectName("toast")
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 8, 8, 8)
        row.setSpacing(10)
        self.text = label("", "toast_text")
        row.addWidget(self.text)
        self.undo = QPushButton(tr("復原 Ctrl+Z"))
        self.undo.setObjectName("toast_undo")
        row.addWidget(self.undo)
        self.effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.effect)
        self.fade = QPropertyAnimation(self.effect, b"opacity", self)
        self.fade.setDuration(160)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(4000)
        self.timer.timeout.connect(self.fade_out)
        host.installEventFilter(self)
        self.hide()

    def show_text(self, text: str, undo=None):
        self.text.setText(text)
        self.undo.setVisible(undo is not None)
        self._undo = undo
        if not getattr(self, "_connected", False):
            self.undo.clicked.connect(self._undo_clicked)
            self._connected = True
        self.adjustSize()
        self._place()
        self.fade.stop()
        self.effect.setOpacity(1.0)
        self.show()
        self.raise_()
        self.timer.start()

    def dismiss(self):
        """Hide at once: the notice is about something the score no longer is."""
        self.timer.stop()
        self.fade.stop()
        self.hide()

    def _undo_clicked(self):
        if self._undo is not None:
            self._undo()
        self.hide()

    def _place(self):
        size = self.sizeHint()
        width = min(size.width(), self.host.width() - 24)
        self.resize(width, size.height())
        self.move((self.host.width() - width) // 2, self.host.height() - size.height() - 18)

    def fade_out(self):
        if self.underMouse():
            self.timer.start()
            return
        self.fade.stop()
        self.fade.setStartValue(1.0)
        self.fade.setEndValue(0.0)
        self.fade.finished.connect(self._hidden)
        self.fade.start()

    def _hidden(self):
        try:
            self.fade.finished.disconnect(self._hidden)
        except (RuntimeError, TypeError):
            pass
        if self.effect.opacity() < 0.05:
            self.hide()

    def eventFilter(self, watched, event):
        if watched is self.host and event.type() == QEvent.Type.Resize and self.isVisible():
            self._place()
        return False
