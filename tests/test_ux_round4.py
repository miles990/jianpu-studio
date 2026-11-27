"""Usability-review leftovers (wow/work/reviews-raw/eval1-fixlist.md P1 #8, #9, P2) and design gaps."""
from __future__ import annotations

import sys

import pytest
from PySide6.QtCore import QSettings, QStandardPaths
from PySide6.QtWidgets import QApplication, QMessageBox

import jianpu_studio.app as app_module
from jianpu_studio import i18n
from jianpu_studio.app import MainWindow
from jianpu_studio.model import Note, Project, Score, demo_project, save_project

SETTINGS_FILE = []


def settings():
    return QSettings(SETTINGS_FILE[0], QSettings.Format.IniFormat)


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path_factory, monkeypatch):
    """QSettings("JianpuStudio", "JianpuStudio") is the native store on macOS even with an INI
    default format, so these tests give the window its own INI file instead."""
    SETTINGS_FILE[:] = [str(tmp_path_factory.mktemp("settings") / "JianpuStudio.ini")]
    monkeypatch.setattr(app_module, "QSettings", lambda *args: settings())
    settings().setValue(i18n.SETTINGS_KEY, i18n.TRADITIONAL)
    yield


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    errors = []
    monkeypatch.setattr(MainWindow, "error", lambda self, title, error: errors.append((title, str(error))))
    widget = MainWindow(recovery=False)
    widget.errors = errors
    widget.recovery_path = tmp_path / "recovery.jps"
    widget.autosave.stop()
    qtbot.addWidget(widget, before_close_func=lambda w: setattr(w, "dirty", False))
    widget.reset(Project())
    widget.show()
    QApplication.processEvents()
    return widget


def launch(qtbot, tmp_path, monkeypatch, **kwargs):
    monkeypatch.setattr(QStandardPaths, "writableLocation", lambda location: str(tmp_path))
    launched = MainWindow(recovery=True, **kwargs)
    launched.autosave.stop()
    qtbot.addWidget(launched, before_close_func=lambda w: setattr(w, "dirty", False))
    launched.show()
    for _ in range(5):
        qtbot.wait(20)
    return launched


# 1. Play on an empty score -------------------------------------------------------------

def test_failed_play_on_empty_score_does_not_leave_the_transport_preparing(window):
    """e2/05: after the 「請先完成樂譜」 message the play button said 「準備試聽…」 forever."""
    assert not window.project.score.notes
    window.play_midi()
    assert window.errors and window.errors[0][0] == "請先完成樂譜"
    assert not window.play_after_render
    assert window.transport_label.text() != "準備試聽…"
    assert "試聽" in window.transport_label.text()


def test_render_failure_after_play_request_resets_the_transport(window, monkeypatch):
    window.reset(Project(Score(notes=[Note(1, duration=1)])))
    monkeypatch.setattr(MainWindow, "current_tool", lambda self, name: None)
    monkeypatch.setattr(MainWindow, "tool_settings", lambda self: None)
    window.play_midi()
    assert not window.play_after_render
    assert window.transport_label.text() != "準備試聽…"


# 2. Relaunch (design §5) -----------------------------------------------------------------

def test_first_launch_without_recent_files_shows_the_start_page(qtbot, tmp_path, monkeypatch):
    launched = launch(qtbot, tmp_path, monkeypatch)
    assert launched.editor_stack.currentIndex() == 1
    assert launched.path is None


def test_relaunch_reopens_the_last_file(qtbot, tmp_path, monkeypatch):
    path = tmp_path / "我的 曲子.jps"
    save_project(path, Project(Score(title="上次的曲子", notes=[Note(3, duration=1)])))
    settings().setValue("recent_files", [str(path)])
    launched = launch(qtbot, tmp_path, monkeypatch)
    qtbot.waitUntil(lambda: launched.path == path)
    assert launched.editor_stack.currentIndex() == 0
    assert launched.project.score.title == "上次的曲子"
    assert launched.project.score.title != demo_project().score.title


def test_relaunch_with_a_missing_last_file_shows_the_start_page_not_the_demo(qtbot, tmp_path, monkeypatch):
    settings().setValue("recent_files", [str(tmp_path / "已刪除.jps")])
    launched = launch(qtbot, tmp_path, monkeypatch)
    assert launched.editor_stack.currentIndex() == 1
    assert launched.path is None
    assert launched.project.score.title != demo_project().score.title     # nothing pretends to be open
    from jianpu_studio.widgets import REASON_MARK
    entry, = launched.start_page.recent_widgets
    assert not entry.isEnabled() and REASON_MARK + "找不到這個檔案" in entry.toolTip()


@pytest.mark.parametrize("answer", ["restore", "decline"])
def test_relaunch_offers_recovery_before_the_last_file(qtbot, tmp_path, monkeypatch, answer):
    path = tmp_path / "last.jps"
    save_project(path, Project(Score(title="上次存的", notes=[Note(3, duration=1)])))
    settings().setValue("recent_files", [str(path)])
    recovered = Project(Score(title="未儲存的", notes=[Note(5, duration=1)]))
    save_project(tmp_path / "recovery.jps", recovered)
    asked = []

    def question(parent, title, *args):
        asked.append(title)
        assert title == "復原未儲存樂譜", "the recovered work must never meet an unsaved-work prompt"
        return QMessageBox.StandardButton.Yes if answer == "restore" else QMessageBox.StandardButton.No
    monkeypatch.setattr(QMessageBox, "question", question)
    launched = launch(qtbot, tmp_path, monkeypatch)
    qtbot.waitUntil(lambda: bool(asked))
    if answer == "restore":
        assert launched.project.score.title == "未儲存的" and launched.dirty and launched.path is None
    else:
        qtbot.waitUntil(lambda: launched.path == path)
        assert launched.project.score.title == "上次存的"
    assert launched.editor_stack.currentIndex() == 0


# 4/5. macOS wording and dialog icons -----------------------------------------------------

def test_alt_shortcuts_use_the_option_key_on_macos(monkeypatch):
    monkeypatch.setattr(i18n.sys, "platform", "darwin")
    assert i18n.tr("常用 · Alt+1…9") == "常用 · ⌥1…9"
    assert i18n.tr("Alt+↑ / Alt+↓") == "⌥↑ / ⌥↓"
    assert i18n._portable_keys("常用 · ⌥1…9") == "常用 · Alt+1…9"
    assert i18n.convert("常用 · ⌥1…9", i18n.SIMPLIFIED, i18n.TRADITIONAL) == "常用 · ⌥1…9"
    monkeypatch.setattr(i18n.sys, "platform", "win32")
    assert i18n.tr("常用 · Alt+1…9") == "常用 · Alt+1…9"


def test_standard_buttons_have_no_windows_mnemonics_on_macos(qapp, monkeypatch):
    monkeypatch.setattr(i18n.sys, "platform", "darwin")
    i18n._apply_qt_translator()
    try:
        box = QMessageBox(QMessageBox.Icon.Question, "t", "x",
                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Save)
        texts = sorted(b.text() for b in box.buttons())
        assert texts == sorted(["是", "否", "儲存"]), texts
        # untranslated texts stay untranslated (an empty string would blank Qt's 「Quit %1」 menu item)
        from PySide6.QtCore import QCoreApplication
        assert QCoreApplication.translate("QMenuBar", "Quit %1") == "Quit %1"
        assert QCoreApplication.translate("JianpuNoSuchContext", "Hello") == "Hello"
    finally:
        monkeypatch.setattr(i18n.sys, "platform", sys.platform)
        i18n._apply_qt_translator()
    assert i18n.strip_mnemonic("儲存(&S)…") == "儲存…" and i18n.strip_mnemonic("A && B") == "A & B"


def test_message_box_icons_follow_the_theme(window):
    from PySide6.QtWidgets import QStyle

    from jianpu_studio import theme
    for appearance in ("dark", "light"):
        window.theme.set_appearance(appearance)
        icon = QApplication.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxQuestion)
        image = icon.pixmap(48, 48).toImage()
        colours = {image.pixelColor(x, y).name() for x in range(image.width()) for y in range(image.height())
                   if image.pixelColor(x, y).alpha() == 255}
        assert window.theme.tokens["accenttext"] in colours
        assert theme.contrast(window.theme.tokens["accenttext"], window.theme.tokens["window"]) >= 3.0
    window.theme.set_appearance("system")


# 3. Inspector chips follow the language ------------------------------------------------------

def test_inspector_mark_chips_follow_a_language_switch(window):
    """d1/02: the 「連線」 chip stayed Traditional in Simplified mode."""
    from PySide6.QtWidgets import QLabel
    window.reset(Project(Score(notes=[Note(1, spans_start=("line.slur",), marks=("erhu.inner",)), Note(2),
                                      Note(3, spans_end=("line.slur",)), Note(4)])))
    window.sync(selection=[0], current=0)
    def chip_texts():
        return [chip.findChild(QLabel, "chip_name").text() for chip in window.inspector.chip_widgets]
    traditional = chip_texts()
    assert "內弦" in traditional
    window.change_language(i18n.SIMPLIFIED)
    try:
        simplified = chip_texts()
        assert "内弦" in simplified and "內弦" not in simplified
        assert "连线" in simplified and "連線" not in simplified
    finally:
        window.change_language(i18n.TRADITIONAL)
    assert chip_texts() == traditional


# 6. A later success replaces a stale warning ------------------------------------------------

def test_a_later_successful_edit_clears_a_warning_toast(window):
    """c1/13: 「⚠ 需要選取連續兩個以上的音符」 stayed after the slur was applied."""
    window.reset(Project(Score(notes=[Note(1), Note(2), Note(3), Note(4)])))
    window.sync(selection=[0], current=0)
    window._apply_symbol("line.slur")
    assert window.toast.isVisible() and window.toast.text.text().startswith("⚠")
    window.sync(selection=[0, 1, 2], current=0)
    assert window._apply_symbol("line.slur")
    assert window.project.score.notes[0].spans_start == ("line.slur",)
    assert not window.toast.isVisible() or not window.toast.text.text().startswith("⚠")


def test_a_later_edit_hides_an_undo_offer_for_an_earlier_one(window):
    window.reset(Project(Score(notes=[Note(1, marks=("finger.2",)), Note(2), Note(3), Note(4)])))
    window.sync(selection=[0], current=0)
    window._apply_symbol("finger.1")          # replaces 二指: toast with 復原
    assert window.toast.isVisible() and window.toast.undo.isVisible()
    window.sync(selection=[1], current=1)
    window._apply_symbol("dyn.p")
    assert not window.toast.isVisible() or "一指" not in window.toast.text.text()


# 7. Unavailable palette cells stay readable ---------------------------------------------------

@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_unavailable_palette_cells_meet_wcag_on_the_real_tokens(scheme):
    """d1/10: names of unavailable cells were too faint in the light theme."""
    from jianpu_studio import palette, theme
    t = theme.tokens(scheme)
    background = t["panel"]                                   # the grid fills its viewport with panel
    name = theme.contrast(t[palette.DISABLED_NAME_ROLE], background)
    glyph = theme.contrast(theme.blend(t["ink"], background, palette.DISABLED_INK_ALPHA), background)
    print(f"{scheme}: unavailable name {name:.2f}:1, mark {glyph:.2f}:1")
    assert name >= 4.5
    assert glyph >= 3.0
    # still distinguishable from an available cell (text on a filled, solid-bordered cell)
    assert t[palette.DISABLED_NAME_ROLE] != t["text"]


# 8. The 繁｜简 switch is easy to find -----------------------------------------------------------

def test_language_switch_is_larger_and_its_icon_toggles(window, qtbot):
    from PySide6.QtCore import Qt
    for code, button in window.language_buttons.items():
        assert button.width() >= 36 and button.height() >= 24 and button.isVisible()
        assert i18n.LANGUAGE_NAMES[code] in button.toolTip()
    assert window.language_icon.isVisible()
    qtbot.mouseClick(window.language_icon, Qt.MouseButton.LeftButton)
    try:
        assert i18n.current_language() == i18n.SIMPLIFIED
        assert window.language_buttons[i18n.SIMPLIFIED].isChecked()
    finally:
        qtbot.mouseClick(window.language_icon, Qt.MouseButton.LeftButton)
    assert i18n.current_language() == i18n.TRADITIONAL


# 10. The side panel width is remembered ------------------------------------------------------

def drag_divider(qtbot, window, dx):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    handle = window.split.handle(2)
    start = handle.rect().center()
    QTest.mousePress(handle, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    QTest.mouseMove(handle, start + QPoint(dx // 2, 0))
    QTest.mouseMove(handle, start + QPoint(dx, 0))
    QTest.mouseRelease(handle, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start + QPoint(dx, 0))
    QApplication.processEvents()


def test_side_panel_width_is_dragged_and_remembered_across_restarts(qtbot, tmp_path, window):
    window.resize(1920, 1000)
    QApplication.processEvents()
    assert window.side_panel.width() == 328
    drag_divider(qtbot, window, -12)
    dragged = window.side_panel.width()
    assert 328 < dragged < 360
    assert int(settings().value("layout/side_panel_width")) == dragged
    drag_divider(qtbot, window, -200)
    assert window.side_panel.width() == 360                     # design: at most 360
    second = MainWindow(recovery=False)  # a restart reads the saved width
    qtbot.addWidget(second, before_close_func=lambda w: setattr(w, "dirty", False))
    second.resize(1920, 1000)
    second.show()
    QApplication.processEvents()
    assert second.side_panel.width() == 360
    second.resize(1000, 630)                                # narrow windows still use the compact 276
    QApplication.processEvents()
    assert second.side_panel.width() == 276
    second.resize(1380, 850)
    QApplication.processEvents()
    assert second.side_panel.width() == 360


# 9. 填寫 on a written note ------------------------------------------------------------------------

def test_fill_mode_digit_on_a_written_note_repitches_it_in_place(window, qtbot):
    """Review P2: in 填寫 a digit on an already written note did nothing but show a banner.
    Now it changes that note's pitch, keeps its duration (no rhythm change), and moves on."""
    from PySide6.QtCore import Qt
    window.reset(Project(Score(notes=[Note(1), Note(2, duration=2), Note(0), Note(0)])))
    window.input_mode.setCurrentIndex(window.input_mode.findData("fill"))
    window.notes.select_rows([0], 0)
    window.notes.setFocus()
    qtbot.keyClick(window.notes, Qt.Key.Key_5)
    notes = window.project.score.notes
    assert [n.degree for n in notes] == [5, 2, 0, 0]
    assert [n.duration for n in notes] == [4, 2, 4, 4]                 # nothing in the bar moved
    assert window.notes.selected_rows() == [1]
    qtbot.keyClick(window.notes, Qt.Key.Key_6)                          # keeps going: the half note
    assert [(n.degree, n.duration) for n in window.project.score.notes][:2] == [(5, 4), (6, 2)]
    assert window.notes.selected_rows() == [2]
    assert not window.measure_notice.isVisible()
    window.undo()
    assert window.project.score.notes[1].degree == 2                    # one undo step per digit


# 11. Every disabled button says why --------------------------------------------------------------

def _disabled_controls(window):
    from PySide6.QtWidgets import QAbstractButton
    found = [b for b in window.findChildren(QAbstractButton) if b.isVisible() and not b.isEnabled()]
    return found + [a for a in (window.undo_action, window.redo_action) if not a.isEnabled()]


def _assert_reasons(window, state):
    import re

    from PySide6.QtCore import QEvent

    from jianpu_studio.widgets import REASON_MARK
    controls = _disabled_controls(window)
    for control in controls:
        if hasattr(control, "isVisible"):
            QApplication.sendEvent(control, QEvent(QEvent.Type.ToolTip))       # what hovering does
        tip = control.toolTip()
        name = control.objectName() or control.text()
        assert REASON_MARK in tip, (state, name, tip)
        reason = tip.split(REASON_MARK, 1)[1]
        assert re.search(r"[\u4e00-\u9fff]", reason), (state, name, reason)          # user language
        assert "_" not in reason and not re.search(r"[a-z]+\.[a-z]+", reason), (state, name, reason)
    return controls


def test_every_disabled_button_says_why(window):
    """design §7 item 30: not only palette cells, every disabled button explains itself."""
    seen = set()
    window.reset(Project())                                                     # empty score
    seen |= {c.objectName() or c.text() for c in _assert_reasons(window, "empty score")}
    window.reset(demo_project())
    window.input_mode.setCurrentIndex(window.input_mode.findData("edit"))
    window.sync(selection=[], current=-1)
    seen |= {c.objectName() or c.text() for c in _assert_reasons(window, "edit mode, nothing selected")}
    window.sync(selection=[2], current=2)
    _assert_reasons(window, "note selected")
    window.notes.select_bars([1])
    window.sync()
    _assert_reasons(window, "bar selected")
    window.input_mode.setCurrentIndex(window.input_mode.findData("insert"))
    window.sync(selection=[], current=-1)
    _assert_reasons(window, "insert mode")
    # a blocking job (import) disables the whole editor
    window.task, window.task_blocks_edit = object(), True
    window.editor.setEnabled(False)
    try:
        controls = _assert_reasons(window, "blocking task")
        assert any("正在處理作業" in c.toolTip() for c in controls)
    finally:
        window.task, window.task_blocks_edit = None, False
        window.editor.setEnabled(True)
    # the states above really disable things: undo/redo, the tie and the note bar in 修改
    assert {"undo_button", "redo_button", "tie_button", "dot_button", "octave_up_button"} <= seen
    # enabling a control again removes the reason
    window.mutate(lambda p: p.score.notes.__setitem__(0, Note(7)))
    assert "⊘" not in window.undo_button.toolTip()


def test_disabled_reasons_follow_a_language_switch(window):
    window.reset(Project())
    assert window.undo_button.toolTip().endswith("目前沒有可以復原的修改")
    window.change_language(i18n.SIMPLIFIED)
    try:
        assert window.undo_button.toolTip().endswith("目前没有可以撤销的修改")
        assert "復原" not in window.undo_button.toolTip()
    finally:
        window.change_language(i18n.TRADITIONAL)
    assert window.undo_button.toolTip().endswith("目前沒有可以復原的修改")
