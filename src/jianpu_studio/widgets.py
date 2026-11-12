from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QKeyEvent, QPainter, QPen
from PySide6.QtWidgets import QListWidget, QStyledItemDelegate, QStyleOptionViewItem

from .model import Note


class NoteDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return QSize(76, 112)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index):
        from PySide6.QtWidgets import QStyle
        data = index.data(Qt.ItemDataRole.UserRole)
        note = Note(**data["note"])
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect.adjusted(3, 3, -3, -3)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        painter.setBrush(QColor("#e1f3ed" if selected else "#ffffff"))
        painter.setPen(QPen(QColor("#197e69" if selected else "#e2e6e4"), 2 if selected else 1))
        painter.drawRoundedRect(rect, 9, 9)
        painter.setPen(QColor("#197e69" if selected else "#172f39"))
        painter.setFont(QFont("Segoe UI", 10))
        if data["bar_start"]:
            painter.drawText(rect.adjusted(8, 5, -4, -4), Qt.AlignmentFlag.AlignTop, f"{data['bar']:02d}")
        center = rect.center()
        painter.setFont(QFont("Segoe UI", 25, QFont.Weight.DemiBold))
        number = str(note.degree)
        painter.drawText(rect.adjusted(0, 15, 0, -15), Qt.AlignmentFlag.AlignCenter, number)
        painter.setFont(QFont("Segoe UI", 12))
        if note.accidental:
            painter.drawText(center.x() - 26, center.y() + 4, "♯" if note.accidental == "#" else "♭")
        if note.dotted:
            painter.drawText(center.x() + 14, center.y() + 4, "·")
        for octave in range(abs(note.octave)):
            y = center.y() - 21 - 6 * octave if note.octave > 0 else center.y() + 24 + 6 * octave
            painter.setBrush(QColor("#172f39"))
            painter.drawEllipse(center.x() - 1, y, 3, 3)
        for beam in range({1: 0, 2: 0, 4: 0, 8: 1, 16: 2, 32: 3}.get(note.duration, 0)):
            y = center.y() + 17 + 4 * beam
            painter.drawLine(center.x() - 10, y, center.x() + 10, y)
        painter.setFont(QFont("Segoe UI", 9))
        painter.setPen(QColor("#6c7c80"))
        label = "— — —" if note.duration == 1 else "—" if note.duration == 2 else f"{note.beats} 拍"
        painter.drawText(rect.adjusted(0, 0, 0, -6), Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter, label)
        painter.restore()


class NoteList(QListWidget):
    pitch = Signal(int)
    delete = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("notes")
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setMovement(QListWidget.Movement.Static)
        self.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.setSpacing(2)
        self.setItemDelegate(NoteDelegate(self))
        self.setAccessibleName("音符編輯區")
        self.setMinimumHeight(240)

    def keyPressEvent(self, event: QKeyEvent):
        if event.text() in "01234567" and len(event.text()) == 1 and event.modifiers() == Qt.KeyboardModifier.NoModifier:
            self.pitch.emit(int(event.text()))
        elif event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete.emit()
        else:
            super().keyPressEvent(event)


STYLE = """
QMainWindow, QDialog { background: #f5f7f6; color: #172f39; }
QWidget { font-family: 'Microsoft JhengHei', 'Noto Sans CJK TC', 'Segoe UI'; font-size: 13px; color: #172f39; }
QWidget#sidebar { background: #172f39; }
QWidget#sidebar QLabel { color: #dae8e7; background: transparent; }
QLabel#brand { font-size: 23px; font-weight: 700; color: white; }
QLabel#muted { color: #75858a; }
QLabel#eyebrow { color: #197e69; font-size: 11px; font-weight: 700; }
QLabel#section { font-size: 19px; font-weight: 700; }
QLabel#hero { font-size: 25px; font-weight: 700; }
QLabel#notice { background: #fff5db; color: #745619; padding: 10px; border-radius: 6px; }
QPushButton { background: #ffffff; border: 1px solid #dbe3e0; border-radius: 6px; padding: 8px 12px; }
QPushButton:hover { background: #edf5f1; border-color: #78b3a4; }
QPushButton:pressed { background: #d4eae2; }
QPushButton:disabled { color: #a1adaa; background: #edf0ee; }
QPushButton#primary { background: #197e69; color: #ffffff; border: 1px solid #197e69; font-weight: 700; }
QPushButton#primary:hover { background: #126651; }
QPushButton#pitch { min-width: 30px; min-height: 32px; font-family: 'Segoe UI'; font-size: 22px; font-weight: 600; }
QWidget#sidebar QPushButton { background: #233e48; color: white; border-color: #3b5159; text-align: left; }
QWidget#sidebar QPushButton:hover { background: #34535d; }
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit { border: 1px solid #d6dfdc; border-radius: 5px; padding: 6px; background: white; selection-background-color: #197e69; }
QLineEdit:focus, QPlainTextEdit:focus { border: 1px solid #197e69; }
QListWidget#notes { background: #f2f5f3; border: 1px solid #e0e6e2; border-radius: 8px; padding: 5px; }
QTabWidget::pane { border: 1px solid #dbe3e0; background: #ffffff; border-radius: 6px; }
QTabBar::tab { padding: 9px 17px; background: #edf1ef; border: 0; }
QTabBar::tab:selected { background: #ffffff; color: #197e69; border-bottom: 2px solid #197e69; }
QGroupBox { border: 1px solid #dbe3e0; border-radius: 6px; margin-top: 10px; padding-top: 14px; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }
QStatusBar { background: #eaf0ed; }
QSplitter::handle { background: #e1e7e4; width: 2px; }
QProgressBar { border: none; background: #e1e7e4; height: 4px; }
QProgressBar::chunk { background: #197e69; }
QMenuBar, QMenu { background: #ffffff; }
QMenu::item:selected { background: #e1f3ed; }
"""
