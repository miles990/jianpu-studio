"""Local photo preparation: EXIF orientation, crop, rotation, contrast."""

from pathlib import Path

from PIL import Image, ImageEnhance, ImageOps
from PIL.ImageQt import ImageQt
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
)

Image.MAX_IMAGE_PIXELS = 40_000_000


class PhotoDialog(QDialog):
    def __init__(self, path: Path, parent=None):
        super().__init__(parent)
        self.setWindowTitle("照片轉簡譜 · 先整理照片")
        self.resize(980, 760)
        with Image.open(path) as original:
            self.original = ImageOps.exif_transpose(original).convert("RGB")
        self.quarter_turns = 0
        layout = QVBoxLayout(self)
        hint = QLabel("適用：印刷五線譜照片／掃描圖。請讓譜線水平、保留完整小節。\n目前不辨識手寫譜或已經是數字的簡譜照片。辨識後仍需核對音符。")
        hint.setWordWrap(True)
        hint.setObjectName("notice")
        layout.addWidget(hint)
        row = QHBoxLayout()
        rotate = QPushButton("順時針旋轉 90°")
        rotate.clicked.connect(self.rotate)
        row.addWidget(rotate)
        row.addWidget(QLabel("微調角度"))
        self.angle = QDoubleSpinBox()
        self.angle.setRange(-15, 15)
        self.angle.setSingleStep(0.5)
        self.angle.valueChanged.connect(self.refresh)
        row.addWidget(self.angle)
        self.contrast = QCheckBox("增強對比")
        self.contrast.setChecked(True)
        self.contrast.toggled.connect(self.refresh)
        row.addWidget(self.contrast)
        row.addStretch()
        layout.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel("裁掉白邊（%）"))
        self.crops = []
        for name in ("左", "上", "右", "下"):
            row.addWidget(QLabel(name))
            box = QSpinBox()
            box.setRange(0, 40)
            box.valueChanged.connect(self.refresh)
            self.crops.append(box)
            row.addWidget(box)
        layout.addLayout(row)
        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.image_label)
        layout.addWidget(scroll, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("開始辨識")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setObjectName("primary")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.refresh()

    def rotate(self):
        self.quarter_turns += 1
        self.refresh()

    def processed(self) -> Image.Image:
        image = self.original.rotate(-90 * (self.quarter_turns % 4), expand=True)
        w, h = image.size
        left, top, right, bottom = [box.value() / 100 for box in self.crops]
        image = image.crop((int(w * left), int(h * top), int(w * (1 - right)), int(h * (1 - bottom))))
        if self.angle.value():
            image = image.rotate(self.angle.value(), resample=Image.Resampling.BICUBIC, expand=True, fillcolor="white")
        image = ImageOps.grayscale(image)
        if self.contrast.isChecked():
            image = ImageEnhance.Contrast(ImageOps.autocontrast(image)).enhance(1.25)
        return image

    def refresh(self):
        image = self.processed().convert("RGB")
        image.thumbnail((850, 490))
        self.image_label.setPixmap(QPixmap.fromImage(ImageQt(image)))

    def save(self, target: Path):
        self.processed().save(target, dpi=(300, 300))
