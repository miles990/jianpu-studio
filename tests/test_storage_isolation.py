"""Tests and helper scripts must never touch the user's real settings or data folder.

How they leaked: MainWindow opened ``QSettings("JianpuStudio", "JianpuStudio")``. That
organization/application constructor always uses the native store (on macOS
~/Library/Preferences/com.jianpustudio.JianpuStudio.plist, on Windows the registry) and ignores
the ``setDefaultFormat``/``setPath`` the old conftest relied on, so test runs added pytest temp
files to the user's recent files and counted thousands of guide launches. The data folder came
from ``QStandardPaths`` AppDataLocation, so every test window wrote its print cache and preview
MIDI to ~/Library/Application Support/pytest-qt-qapp. scripts/capture.py and
scripts/benchmark_editor.py opened both real locations too.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path

from PySide6 import QtCore
from PySide6.QtWidgets import QApplication

from jianpu_studio import storage
from jianpu_studio.app import MainWindow

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "jianpu_studio"
REAL_QSETTINGS = storage.QSettings        # imported before conftest guards PySide6.QtCore.QSettings


def session_root() -> Path:
    return Path(os.environ[storage.SETTINGS_ENV]).resolve().parent


def test_org_and_app_settings_ignore_the_ini_default_which_is_how_the_suite_leaked():
    previous = REAL_QSETTINGS.defaultFormat()
    REAL_QSETTINGS.setDefaultFormat(REAL_QSETTINGS.Format.IniFormat)
    try:
        native = REAL_QSETTINGS(storage.ORGANIZATION, storage.APPLICATION)   # read-only: nothing is written
        assert native.format() == REAL_QSETTINGS.Format.NativeFormat
    finally:
        REAL_QSETTINGS.setDefaultFormat(previous)
    isolated = storage.open_settings()
    assert isolated.format() == REAL_QSETTINGS.Format.IniFormat
    assert Path(isolated.fileName()).resolve().is_relative_to(session_root())


def test_a_test_window_keeps_settings_and_data_in_the_session_folder(qtbot):
    window = MainWindow(recovery=False)
    window.autosave.stop()
    qtbot.addWidget(window, before_close_func=lambda w: setattr(w, "dirty", False))
    QApplication.processEvents()
    assert Path(window.settings.fileName()).resolve().is_relative_to(session_root())
    assert window.data_dir.resolve().is_relative_to(session_root())
    assert window.recovery_path.resolve().is_relative_to(session_root())
    # The launch counter the real plist had counted to 2255 now lands in the session file.
    assert int(storage.open_settings().value("guide/launches", 0)) >= 1


def test_overrides_choose_the_settings_file_and_data_folder(tmp_path, monkeypatch):
    monkeypatch.setenv(storage.SETTINGS_ENV, str(tmp_path / "設定 資料夾"))
    monkeypatch.setenv(storage.DATA_ENV, str(tmp_path / "資料"))
    settings = storage.open_settings()
    settings.setValue("probe", "值")
    settings.sync()
    assert Path(settings.fileName()) == tmp_path / "設定 資料夾" / storage.SETTINGS_FILE
    assert (tmp_path / "設定 資料夾" / storage.SETTINGS_FILE).is_file()
    assert storage.data_dir() == tmp_path / "資料"


def test_scripts_isolate_unless_the_caller_chose_locations(tmp_path, monkeypatch):
    monkeypatch.delenv(storage.SETTINGS_ENV)
    monkeypatch.delenv(storage.DATA_ENV)
    root = storage.isolate_for_script()
    try:
        assert Path(os.environ[storage.SETTINGS_ENV]) == root / "settings"
        assert Path(os.environ[storage.DATA_ENV]) == root / "data"
    finally:
        root.rmdir()
    monkeypatch.setenv(storage.SETTINGS_ENV, str(tmp_path / "s"))
    monkeypatch.setenv(storage.DATA_ENV, str(tmp_path / "d"))
    assert storage.isolate_for_script() is None
    assert os.environ[storage.SETTINGS_ENV] == str(tmp_path / "s")


def test_tests_cannot_open_the_native_store_directly():
    for args in (("JianpuStudio", "JianpuStudio"), (),
                 (QtCore.QSettings.Format.NativeFormat, QtCore.QSettings.Scope.UserScope, "JianpuStudio", "JianpuStudio")):
        try:
            QtCore.QSettings(*args)
        except AssertionError:
            continue
        raise AssertionError(f"QSettings{args!r} was allowed")


def _calls(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
            yield name, node.lineno


def test_only_storage_creates_settings_or_asks_for_a_writable_location():
    offenders = []
    for path in SRC.rglob("*.py"):
        if "vendor" in path.parts or path.name == "storage.py":
            continue
        for name, line in _calls(ast.parse(path.read_text(encoding="utf-8"))):
            if name in {"QSettings", "writableLocation", "setDefaultFormat", "setPath"}:
                offenders.append(f"{path.relative_to(ROOT)}:{line} {name}")
    assert offenders == []


def test_scripts_that_open_the_app_isolate_its_store_first():
    for name in ("capture.py", "benchmark_editor.py", "render_symbol_previews.py"):
        text = (ROOT / "scripts" / name).read_text(encoding="utf-8")
        assert "isolate_for_script()" in text, name
        assert text.index("isolate_for_script()") < text.index("Application("), name
