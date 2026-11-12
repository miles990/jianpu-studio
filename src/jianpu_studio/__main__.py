import os
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        from .worker import run
        return run(sys.argv[2:])

    from PySide6.QtCore import QLockFile, QStandardPaths, QTimer
    from PySide6.QtGui import QFont, QIcon
    from PySide6.QtWidgets import QApplication, QMessageBox

    from .app import MainWindow
    from .engine import app_root

    app = QApplication(sys.argv)
    app.setOrganizationName("JianpuStudio")
    self_test = len(sys.argv) > 2 and sys.argv[1] == "--self-test"
    app.setApplicationName("JianpuStudioSelfTest" if self_test else "JianpuStudio")
    app.setFont(QFont("Microsoft JhengHei" if os.name == "nt" else "Noto Sans CJK TC", 10))
    icon = Path(__file__).parent / "assets/icon.png"
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    data_dir = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation))
    data_dir.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(data_dir / "session.lock"))
    if not lock.tryLock(100):
        QMessageBox.information(None, "簡譜工房已開啟", "請切回已開啟的簡譜工房，從「檔案 → 開啟」載入樂譜。")
        return 0
    # Audiveris looks here before trying to download OCR language data.
    tessdata = app_root() / "runtime/tessdata"
    if tessdata.is_dir():
        os.environ.setdefault("TESSDATA_PREFIX", str(tessdata))
    window = MainWindow(recovery=not self_test)
    window.show()
    if self_test:
        output = Path(sys.argv[2]).resolve()
        output.mkdir(parents=True, exist_ok=True)
        timer = QTimer(window)
        def capture():
            if window.result and window.pdf_document.pageCount() > 0:
                if not window.grab().save(str(output / "windows-ui.png")):
                    app.exit(2)
                    return
                (output / "ui-test.txt").write_text("Frozen GUI rendered demo PDF successfully.\n", encoding="utf-8")
                window.dirty = False
                window.close()
                app.exit(0)
        def timeout():
            (output / "ui-error.txt").write_text(window.preview_status.text() + "\n" + window.log.toPlainText(), encoding="utf-8")
            if window.task:
                window.task.cancel.set()
                window.task.wait(20000)
            app.exit(1)
        timer.timeout.connect(capture)
        timer.start(1000)
        QTimer.singleShot(90000, timeout)
    elif len(sys.argv) > 1:
        window.open_file(path=Path(sys.argv[1]))
    result = app.exec()
    lock.unlock()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
