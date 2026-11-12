"""Capture the real Qt interface after a real LilyPond render."""
import argparse
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from jianpu_studio.app import MainWindow  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument("output", type=Path)
args = parser.parse_args()
app = QApplication([])
app.setApplicationName("JianpuStudioScreenshot")
window = MainWindow(recovery=False)
window.show()
window.render_score(True)


def capture():
    if window.result and window.pdf_document.pageCount():
        args.output.parent.mkdir(parents=True, exist_ok=True)
        window.grab().save(str(args.output))
        window.dirty = False
        window.close()
        app.exit(0)


timer = QTimer()
timer.timeout.connect(capture)
timer.start(1000)
QTimer.singleShot(60000, lambda: app.exit(1))
raise SystemExit(app.exec())
