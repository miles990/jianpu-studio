"""Interface language: catalog completeness, detection, and a live switch with nothing lost."""
import ast
import re
import string
from pathlib import Path

import pytest
from PySide6.QtCore import QLocale, QSettings
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QComboBox,
    QGroupBox,
    QLabel,
    QMenu,
    QTabWidget,
    QWidget,
)

from jianpu_studio import i18n
from jianpu_studio.app import MainWindow
from jianpu_studio.i18n_zh_cn import CATALOG
from jianpu_studio.model import Note, Project, Score

SRC = Path(__file__).resolve().parents[1] / "src" / "jianpu_studio"
CJK = re.compile("[一-鿿]")
# Files whose user-visible Chinese must all go through tr()/N_(). Extend this list
# as the remaining modules (engine, updates, ...) are converted.
OWNED = ["app.py", "editing.py", "measures.py", "model.py", "musicxml.py", "notation_actions.py",
         "photo.py", "score_view.py", "widgets.py", "workspace.py",
         # 0.4.0 workspace modules
         "palette.py", "inspector.py", "startpage.py", "shortcuts.py", "symbols_ui.py", "notation_edit.py",
         "glyphs.py", "score_layout.py", "score_paint.py", "theme.py", "icons.py", "playmap.py"]
# Chinese literals that are not interface text: (file, exact literal).
NOT_UI = {
    ("score_view.py", "○一二三四"),                   # finger numerals drawn on the staff, same in both scripts
    ("editing.py", r"第\s*(\d+)\s*[個个]音符"),       # parses "note N" out of a validation message
    ("glyphs.py", "結"), ("glyphs.py", "结"),          # 「結」 of a final volta, printed in the score's script
}


def source_files():
    return [p for p in sorted(SRC.rglob("*.py")) if "vendor" not in p.parts]


def is_translation_call(node):
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id in ("tr", "N_") and node.args)


def translated_literals():
    found = {}
    for path in source_files():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if is_translation_call(node):
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    found.setdefault(arg.value, []).append(f"{path.name}:{node.lineno}")
                else:  # tr(table[key]) is fine (the table holds N_ literals); building text is not
                    assert not isinstance(arg, (ast.JoinedStr, ast.BinOp)), (
                        f"{path.name}:{node.lineno}: tr() must take a literal or a looked-up source, not built text")
    return found


def placeholders(text):
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


def test_every_translated_literal_has_a_simplified_entry_and_none_is_unused():
    used = translated_literals()
    assert used, "no tr() calls found"
    assert sorted(set(used) - set(CATALOG)) == []
    assert sorted(set(CATALOG) - set(used)) == []


def test_catalog_entries_are_well_formed_and_reversible():
    for source, value in CATALOG.items():
        assert value.strip() and value == value.strip() or source != source.strip(), source
        assert placeholders(source) == placeholders(value), source
        assert not re.search(r"\{[^a-z_}]|\{\}", source), source
    values = list(CATALOG.values())
    duplicated = sorted({v for v in values if values.count(v) > 1})
    assert duplicated == [], "two sources share one translation, so switching back would be ambiguous"
    foreign = [(k, v) for k, v in CATALOG.items() if v != k and v in CATALOG]
    assert foreign == [], "a translation equals a different source string"


def test_simplified_values_contain_no_traditional_only_characters():
    opencc = pytest.importorskip("opencc")
    to_simplified = opencc.OpenCC("t2s")
    allowed = set()  # characters that legitimately appear unchanged in both scripts
    leftovers = {}
    for source, value in CATALOG.items():
        bad = {c for c in value if CJK.match(c) and to_simplified.convert(c) != c and c not in allowed}
        if bad:
            leftovers[source] = "".join(sorted(bad))
    assert leftovers == {}


def test_every_translation_is_a_real_translation_unless_the_text_is_script_neutral():
    """Where Simplified equals Traditional the text must truly be script-neutral."""
    opencc = pytest.importorskip("opencc")
    to_simplified = opencc.OpenCC("t2s")
    for source, value in CATALOG.items():
        if source == value:
            assert to_simplified.convert(source) == source, f"{source!r} still Traditional"


def chinese_literals_outside_translation(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    wrapped, docstrings = set(), set()
    for node in ast.walk(tree):
        if is_translation_call(node):
            wrapped.update(id(n) for n in ast.walk(node.args[0]))
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
    return [(node.lineno, node.value) for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and CJK.search(node.value)
            and id(node) not in wrapped and id(node) not in docstrings
            and (path.name, node.value) not in NOT_UI]


@pytest.mark.parametrize("name", OWNED)
def test_no_interface_text_escapes_translation(name):
    assert chinese_literals_outside_translation(SRC / name) == []


def test_the_scan_really_flags_an_unwrapped_literal(tmp_path):
    sample = tmp_path / "sample.py"
    sample.write_text('"""文件說明"""\nfrom x import tr\na = tr("好")\nb = "漏掉"\nc = f"也漏掉{a}"\n', encoding="utf-8")
    assert [text for _, text in chinese_literals_outside_translation(sample)] == ["漏掉", "也漏掉"]


@pytest.mark.parametrize("locale, expected", [
    ("zh_Hant_TW", "zh_TW"),
    ("zh_Hans_CN", "zh_CN"),
    ("zh_HK", "zh_TW"),
    ("zh_SG", "zh_CN"),
    ("zh_CN", "zh_CN"),
    ("zh_TW", "zh_TW"),
    ("zh-Hans-CN", "zh_CN"),
    ("en_US", "zh_TW"),
    ("en_SG", "zh_TW"),
    ("ja_JP", "zh_TW"),
])
def test_detect_language(locale, expected):
    assert i18n.detect_language(locale) == expected
    assert i18n.detect_language(QLocale(locale.replace("-", "_"))) == expected


def test_tr_returns_source_in_traditional_and_catalog_in_simplified():
    assert i18n.tr("儲存") == "儲存"
    i18n.set_language("zh_CN")
    assert i18n.tr("儲存") == "保存"
    assert i18n.tr("不在目錄裡的字串") == "不在目錄裡的字串"
    assert i18n.tr("已儲存：{path}").format(path="/a b/曲.jps") == "已保存：/a b/曲.jps"


def test_set_language_notifies_once_and_rejects_unknown_codes():
    seen = []

    class Listener:
        def callback(self, new, old):
            seen.append((new, old))

    listener = Listener()
    i18n.on_change(listener.callback)
    assert i18n.set_language("zh_TW") is False
    assert i18n.set_language("zh_CN") is True
    assert i18n.set_language("zh_CN") is False
    assert seen == [("zh_CN", "zh_TW")]
    with pytest.raises(ValueError):
        i18n.set_language("en_US")
    i18n.off_change(listener.callback)
    i18n.set_language("zh_TW")
    assert seen == [("zh_CN", "zh_TW")]


def test_convert_goes_both_ways_including_templates_and_nested_messages():
    assert i18n.convert("已儲存：{x}".replace("{x}", "/a/b.jps"), "zh_CN") == "已保存：/a/b.jps"
    assert i18n.convert("已保存：/a/b.jps", "zh_TW") == "已儲存：/a/b.jps"
    nested = "請先修正連線或反覆記號，再編輯小節：第 3 個音符的連弓線缺少起點。"
    expected = "请先修正连线或反复记号，再编辑小节：第 3 个音符的连弓线缺少起点。"
    assert i18n.convert(nested, "zh_CN") == expected
    assert i18n.convert(expected, "zh_TW") == nested
    assert i18n.convert("My own title 小星星", "zh_CN") == "My own title 小星星"


def test_convert_round_trips_every_catalog_entry():
    for source, value in CATALOG.items():
        if "{" in source:
            filled_source = source.format(**{name: "X" for name in placeholders(source)})
            filled_value = value.format(**{name: "X" for name in placeholders(value)})
        else:
            filled_source, filled_value = source, value
        assert i18n.convert(filled_source, "zh_CN") == i18n.platform_keys(filled_value), source
        assert i18n.convert(filled_value, "zh_TW") == i18n.platform_keys(filled_source), source


def test_model_messages_follow_the_language_at_the_time_they_are_raised():
    score = Score(notes=[Note(1, duration=1), Note(1, duration=1)])
    score.tempo = 500
    with pytest.raises(ValueError) as traditional:
        score.validate()
    i18n.set_language("zh_CN")
    with pytest.raises(ValueError) as simplified:
        score.validate()
    assert str(traditional.value) == "速度須介於每分鐘 20 到 300 拍。"
    assert str(simplified.value) == "速度须介于每分钟 20 到 300 拍。"
    from jianpu_studio.model import DURATIONS
    assert [i18n.tr(text) for text in DURATIONS.values()][1] == "二分 · 2 拍"
    assert Score().title == "未命名乐曲"
    i18n.set_language("zh_TW")
    assert Score().title == "未命名樂曲"


# --- the running window -----------------------------------------------------

@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(MainWindow, "render_score", lambda *a, **k: None)
    monkeypatch.setattr(MainWindow, "error", lambda self, title, error: pytest.fail(f"{title}: {error}"))
    widget = MainWindow(recovery=False)
    widget.recovery_path = tmp_path / "recovery.jps"
    widget.autosave.stop()
    qtbot.addWidget(widget, before_close_func=lambda w: setattr(w, "dirty", False))
    widget.resize(1380, 850)
    widget.show()
    QApplication.processEvents()
    return widget


def menu_action(window, menu_title, text):
    menu = next(m for m in window.menuBar().findChildren(QMenu) if m.title() == menu_title)
    return next(a for a in menu.actions() if a.text() == text)


def visible_texts(window):
    """Every interface string currently on screen, with where it came from."""
    found = []
    def add(where, text):
        if text:
            found.append((where, text))
    for widget in [window, *window.findChildren(QWidget)]:
        name = type(widget).__name__ + ":" + (widget.objectName() or "-")
        add(name + " tooltip", widget.toolTip())
        add(name + " accessibleName", widget.accessibleName())
        add(name + " accessibleDescription", widget.accessibleDescription())
        if isinstance(widget, (QLabel, QAbstractButton)):
            add(name, widget.text())
        elif isinstance(widget, QGroupBox):
            add(name, widget.title())
        elif isinstance(widget, QComboBox):
            for index in range(widget.count()):
                add(name + f"[{index}]", widget.itemText(index))
            add(name + " placeholder", widget.placeholderText())
        elif isinstance(widget, QTabWidget):
            for index in range(widget.count()):
                add(name + f" tab{index}", widget.tabText(index))
        if hasattr(widget, "placeholderText") and not isinstance(widget, QComboBox):
            add(name + " placeholder", widget.placeholderText())
    for menu in window.findChildren(QMenu):
        add("menu title", menu.title())
    for action in window.findChildren(QAction):
        add("action", action.text())
        add("action tooltip", action.toolTip())
    add("window title", window.windowTitle())
    add("status", window.statusBar().currentMessage())
    return found


def untranslated(window):
    """Texts on screen that are still the Traditional source of a different Simplified string."""
    traditional_only = {k for k, v in CATALOG.items() if v != k}
    return [(where, text) for where, text in visible_texts(window)
            if any(piece in traditional_only for piece in (text, *text.split("\n")))]


def widget_census(window):
    return (len(window.findChildren(QWidget)), len(window.findChildren(QAction)), len(window.findChildren(QMenu)))


def test_switching_to_simplified_converts_every_visible_text(window):
    assert untranslated(window) != []  # sanity: the helper does see Traditional strings in zh_TW
    window.language_actions["zh_CN"].trigger()
    QApplication.processEvents()
    assert i18n.current_language() == "zh_CN"
    assert window.menuBar().actions()[0].text() == "文件"
    assert menu_action(window, "文件", "保存").text() == "保存"
    assert menu_action(window, "编辑", "撤销").text() == "撤销"
    assert window.save_button.text() == "保存"
    # 0.4.0 layout: export is one primary button; the group boxes became a toolbar,
    # a score bar and a note bar whose controls carry their own texts.
    assert window.export_button.text() == "导出"
    assert window.play_button.toolTip() == "Enter 试听 / 停止（在音符区内）"
    assert window.insert_mode_button.text() == "添加"
    assert window.inspector.heading.text() == "选中"
    assert window.palette.search.placeholderText().startswith("搜索记号")
    assert window.title_field.placeholderText() == "未命名乐曲"
    assert window.lyrics.placeholderText() == "歌词（选填；纯器乐可留空）"
    assert window.duration.itemText(1) == "二分 · 2 拍"
    assert window.octave.itemText(0) == "低两个八度"
    assert window.accidental.itemText(0) == "无升降号"
    assert window.rhythm_label.text().endswith("拍数完整")
    assert window.palette.categories.item(0).text() == "常用"
    assert window.statusBar().currentMessage() == "就绪 · 离线编辑"
    assert window.notes.accessibleName() == "简谱编辑谱面"
    assert window.version_label.accessibleName().startswith("当前版本")
    assert "简谱工房" in window.windowTitle()
    assert untranslated(window) == []


def test_switch_loses_nothing_and_adds_nothing(window, qtbot):
    notes = [Note(d, duration=4) for d in (1, 2, 3, 4, 5, 6, 7, 1)]
    window.reset(Project(Score(title="自訂曲名 停止", lyrics="新建 儲存", notes=notes)))
    qtbot.keyClick(window.notes, "3")      # one undoable edit on top of the loaded score
    window.notes.select_rows([1, 2], 2)
    window.score_zoom.setCurrentIndex(3)
    window.dirty = True
    window.sync(selection=[1, 2], current=2)
    project, depth = window.project, len(window.history)
    rows, current, zoom = window.notes.selected_rows(), window.notes.currentRow(), window.score_zoom.currentData()
    window.future.append(window.snapshot())
    window.future_depth = len(window.future)
    path = Path("/tmp/中文 路徑/曲子.jps")
    window.path = path
    census = widget_census(window)
    assert depth >= 1
    for _ in range(5):
        for code in ("zh_CN", "zh_TW"):
            window.language_actions[code].trigger()
            QApplication.processEvents()
            assert widget_census(window) == census
            assert window.project is project and window.project.score.notes == project.score.notes
            assert len(window.history) == depth and len(window.future) == window.future_depth
            assert window.dirty and window.path == path
            assert window.notes.selected_rows() == rows and window.notes.currentRow() == current
            assert window.score_zoom.currentData() == zoom
    assert window.title_field.text() == "自訂曲名 停止"      # score content is never converted
    assert window.lyrics.text() == "新建 儲存"
    assert window.project.score.title == "自訂曲名 停止"
    assert window.windowTitle().startswith("● ")
    window.language_actions["zh_CN"].trigger()
    assert untranslated(window) == []
    window.language_actions["zh_TW"].trigger()
    window.undo()                                           # history still works after switching
    assert len(window.history) == depth - 1


def test_dynamic_texts_and_open_selection_follow_the_switch(window):
    window.reset(Project(Score(notes=[Note(1, duration=1), Note(2, duration=1), Note(3, duration=1)])))
    window.notes.select_rows([0], 0)
    window.input_mode.setCurrentIndex(window.input_mode.findData("edit"))
    window.preview_status.setText("樂譜已修改，等待更新排版。")
    window.statusBar().showMessage("請先選取要標記的音符。", 60000)
    window.language_actions["zh_CN"].trigger()
    assert window.preview_status.text() == "乐谱已修改，等待更新排版。"
    assert window.statusBar().currentMessage() == "请先选中要标记的音符。"
    assert window.selection_label.text() == "请先选中要标记的音符。"   # 0.4.0: messages show mid status bar
    window.statusBar().clearMessage()
    assert window.selection_label.text().startswith("修改所选 1 个音符")
    assert window.inspector.title.text() == "第 1 小节第 1 音"        # 0.3.x: measure_selection label
    assert window.degree.currentData() == 1           # combo selection is kept, only its text changes
    assert window.notes.model().data(window.notes.model().index(0), 0).startswith("第 1 小节，第 1 个音符")
    window.language_actions["zh_TW"].trigger()
    assert window.selection_label.text().startswith("修改所選 1 個音符")
    assert window.preview_status.text() == "樂譜已修改，等待更新排版。"
    assert window.inspector.title.text() == "第 1 小節第 1 音"


def test_dialogs_opened_after_the_switch_use_the_new_language(window, monkeypatch):
    window.language_actions["zh_CN"].trigger()
    from PySide6.QtWidgets import QDialog
    titles = []
    monkeypatch.setattr(QDialog, "exec", lambda self: titles.append(self.windowTitle()) or 0)
    window.layout_settings()
    window.insert_bars_dialog()
    window.reset(Project(Score(notes=[Note(1)])))
    window.notes.select_rows([0], 0)
    window.notation_settings(1)
    assert titles == ["纸张与排版", "添加小节", "基本记号 / 二胡"]


def test_language_is_remembered_and_applied_to_a_new_window(window, qtbot, tmp_path, monkeypatch):
    window.language_actions["zh_CN"].trigger()
    assert QSettings("JianpuStudio", "JianpuStudio").value(i18n.SETTINGS_KEY) == "zh_CN"
    window.dirty = False
    window.close()
    i18n.set_language("zh_TW")                      # a fresh process starts in the source language
    second = MainWindow(recovery=False)
    qtbot.addWidget(second, before_close_func=lambda w: setattr(w, "dirty", False))
    second.autosave.stop()
    assert i18n.current_language() == "zh_CN"
    assert second.language_actions["zh_CN"].isChecked() and not second.language_actions["zh_TW"].isChecked()
    assert second.save_button.text() == "保存"
    assert untranslated(second) == []


def test_first_launch_follows_the_system_language_without_saving_it(qtbot, monkeypatch):
    QSettings("JianpuStudio", "JianpuStudio").remove(i18n.SETTINGS_KEY)
    monkeypatch.setattr(i18n, "detect_language", lambda *a: "zh_CN")
    first = MainWindow(recovery=False)
    qtbot.addWidget(first, before_close_func=lambda w: setattr(w, "dirty", False))
    first.autosave.stop()
    assert i18n.current_language() == "zh_CN" and first.save_button.text() == "保存"
    assert QSettings("JianpuStudio", "JianpuStudio").value(i18n.SETTINGS_KEY, "") == ""


def test_language_menu_names_each_language_in_its_own_script_with_a_checkmark(window):
    assert window.language_menu.title() == "語言 / 语言"
    labels = [a.text() for a in window.language_menu.actions()]
    assert labels == ["繁體中文", "简体中文"]
    assert [a.isChecked() for a in window.language_menu.actions()] == [True, False]
    window.language_actions["zh_CN"].trigger()
    assert [a.isChecked() for a in window.language_menu.actions()] == [False, True]
    assert [a.text() for a in window.language_menu.actions()] == labels     # never translated
    assert window.language_menu.title() == "語言 / 语言"


@pytest.mark.parametrize("platform, tw, cn", [
    ("win32", "Microsoft JhengHei UI", "Microsoft YaHei UI"),     # 0.4.0: narrower UI faces first
    ("darwin", "PingFang TC", "PingFang SC"),
    ("linux", "Noto Sans CJK TC", "Noto Sans CJK SC"),
])
def test_fonts_are_per_platform_and_per_language(monkeypatch, platform, tw, cn):
    from jianpu_studio.widgets import stylesheet
    monkeypatch.setattr("sys.platform", platform)
    assert (i18n.ui_font_stack("zh_TW")[0], i18n.ui_font_stack("zh_CN")[0]) == (tw, cn)
    assert stylesheet("zh_TW").count(f"'{tw}'") >= 1 and cn not in stylesheet("zh_TW")
    assert f"font-family: '{cn}'" in stylesheet("zh_CN")
    sans, serif = i18n.latin_sans_family(), i18n.latin_serif_family()
    assert (platform == "linux") == (sans == "DejaVu Sans" and serif == "DejaVu Serif")


@pytest.mark.parametrize("platform, mod", [("darwin", "⌘"), ("win32", "Ctrl"), ("linux", "Ctrl")])
def test_shortcut_hints_name_the_platform_modifier(monkeypatch, platform, mod):
    monkeypatch.setattr("sys.platform", platform)
    assert i18n.modifier_name() == mod
    hint = i18n.tr("Ctrl+Z 復原；Ctrl+Y 重做；Ctrl+S 儲存；F5 列印預覽。")
    assert hint.startswith(mod + "Z" if mod == "⌘" else "Ctrl+Z")
    assert i18n.tr("Shift 連選、Ctrl 加選、Ctrl+A 全選、M 選小節。") == (
        f"Shift 連選、{mod} 加選、{mod}A 全選、M 選小節。" if mod == "⌘" else "Shift 連選、Ctrl 加選、Ctrl+A 全選、M 選小節。")
    shown = i18n.tr("剪下　Ctrl+X")
    i18n.set_language("zh_CN")
    assert i18n.convert(shown, "zh_CN", "zh_TW") == i18n.tr("剪下　Ctrl+X")
    assert i18n.convert(i18n.tr("剪下　Ctrl+X"), "zh_TW", "zh_CN") == shown


def test_wording_is_true_on_every_platform():
    texts = " ".join(CATALOG) + (SRC / "app.py").read_text(encoding="utf-8")
    assert "Windows 的預設" not in texts and "Windows 內建音色" not in texts and "Alt+F4" not in texts


def test_quit_about_and_preferences_have_application_menu_roles(window):
    roles = {a.text(): a.menuRole() for a in window.findChildren(QAction)}
    assert roles["離開"] == QAction.MenuRole.QuitRole
    assert roles["關於"] == QAction.MenuRole.AboutRole
    assert roles["工具與環境設定"] == QAction.MenuRole.PreferencesRole
    assert window.language_menu.menuAction().isVisible() and window.language_menu.isEnabled()
    assert all(a.menuRole() == QAction.MenuRole.TextHeuristicRole for a in window.language_menu.actions())
    assert window.language_menu.menuAction() in window.menuBar().actions()
