"""Where the app keeps user settings and its data folder (recovery, print cache, updates).

These two functions are the only places that choose those locations; nothing else in the
package constructs ``QSettings`` or asks ``QStandardPaths`` for a writable location (enforced by
tests/test_storage_isolation.py).

``JIANPU_SETTINGS_DIR`` and ``JIANPU_DATA_DIR`` redirect them, so tests and helper scripts never
touch the user's real store (the macOS plist, the Windows registry key, ~/.config/...).
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from PySide6.QtCore import QSettings, QStandardPaths

ORGANIZATION = "JianpuStudio"
APPLICATION = "JianpuStudio"
SETTINGS_ENV = "JIANPU_SETTINGS_DIR"
DATA_ENV = "JIANPU_DATA_DIR"
SETTINGS_FILE = "JianpuStudio.ini"


def open_settings() -> QSettings:
    folder = os.environ.get(SETTINGS_ENV)
    if folder:
        return QSettings(str(Path(folder) / SETTINGS_FILE), QSettings.Format.IniFormat)
    return QSettings(ORGANIZATION, APPLICATION)


def data_dir() -> Path:
    folder = os.environ.get(DATA_ENV)
    if folder:
        return Path(folder)
    return Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation))


def isolate_for_script(prefix: str = "jianpu-script-") -> Path | None:
    """Point both locations at a fresh temporary folder unless the caller already chose them.

    Helper scripts call this before creating any window. Returns the folder it created, or None
    when both variables were already set."""
    if os.environ.get(SETTINGS_ENV) and os.environ.get(DATA_ENV):
        return None
    root = Path(tempfile.mkdtemp(prefix=prefix))
    os.environ.setdefault(SETTINGS_ENV, str(root / "settings"))
    os.environ.setdefault(DATA_ENV, str(root / "data"))
    return root
