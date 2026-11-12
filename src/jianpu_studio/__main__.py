import os
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        from .worker import run
        return run(sys.argv[2:])

    from PySide6.QtCore import QLockFile, QStandardPaths
    from PySide6.QtGui import QFont, QIcon
    from PySide6.QtWidgets import QApplication, QMessageBox

    from .app import MainWindow
    from .engine import app_root

    app = QApplication(sys.argv)
    app.setOrganizationName("JianpuStudio")
    app.setApplicationName("JianpuStudio")
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
    window = MainWindow()
    window.show()
    if len(sys.argv) > 1:
        window.open_file(path=Path(sys.argv[1]))
    result = app.exec()
    lock.unlock()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
