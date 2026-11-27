import os
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        from .worker import run
        return run(sys.argv[2:])

    from PySide6.QtCore import QLockFile, QTimer
    from PySide6.QtGui import QFont, QIcon
    from PySide6.QtWidgets import QApplication, QMessageBox

    from . import storage
    from .app import MainWindow
    from .engine import app_root
    from .i18n import tr
    from .platform_support import FileOpenRouter, initial_path_from_args

    app = QApplication(sys.argv)
    app.setOrganizationName("JianpuStudio")
    self_test = len(sys.argv) > 2 and sys.argv[1] == "--self-test"
    app.setApplicationName("JianpuStudioSelfTest" if self_test else "JianpuStudio")
    font = QFont()
    from .i18n import ui_font_stack
    font.setFamilies(ui_font_stack())
    font.setPixelSize(13)
    app.setFont(font)
    app.setStyle("Fusion")
    # Installed before any window so a Finder "open document" is never lost.
    file_open = FileOpenRouter(app)
    icon = Path(__file__).parent / "assets/icon.png"
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    data_dir = storage.data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(data_dir / "session.lock"))
    if not lock.tryLock(100):
        QMessageBox.information(None, tr("簡譜工房已開啟"), tr("請切回已開啟的簡譜工房，從「檔案 → 開啟」載入樂譜。"))
        return 0
    # Audiveris looks here before trying to download OCR language data.
    tessdata = app_root() / "runtime/tessdata"
    if tessdata.is_dir():
        os.environ.setdefault("TESSDATA_PREFIX", str(tessdata))
    initial_path = None if self_test else initial_path_from_args(sys.argv)
    window = MainWindow(recovery=not self_test, initial_path=initial_path)
    window.show()
    if not self_test:
        file_open.attach(window)
    if self_test:
        QTimer.singleShot(0, lambda: window.render_score(True))
        output = Path(sys.argv[2]).resolve()
        output.mkdir(parents=True, exist_ok=True)
        timer = QTimer(window)
        def capture():
            if window.result and window.pdf_document.pageCount() > 0:
                screenshot = "macos-ui.png" if sys.platform == "darwin" else "windows-ui.png"
                if not window.grab().save(str(output / screenshot)):
                    app.exit(2)
                    return
                (output / "ui-test.txt").write_text("Frozen GUI rendered demo PDF successfully.\n", encoding="utf-8")
                import json

                from .updates import installed_target
                target = installed_target(app_root())
                (output / "update-target.json").write_text(json.dumps(
                    {"directory": str(target.directory), "scope": target.scope} if target else None,
                    ensure_ascii=False), encoding="utf-8")
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
    result = app.exec()
    lock.unlock()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
