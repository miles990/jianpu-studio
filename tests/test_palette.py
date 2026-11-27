"""0.4.0 symbol palette, selection inspector, parts and pickup (wow/design.md §2, §3.3, §3.4)."""
import re

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication

from jianpu_studio import i18n, notation_edit, storage, symbols, symbols_ui
from jianpu_studio.app import MainWindow
from jianpu_studio.model import Note, Part, Project, Score
from jianpu_studio.notation_edit import Target

T = Target


# ---------------------------------------------------------------- catalog and search (no window)
def test_palette_shows_every_non_hidden_registry_symbol_except_volta_end():
    shown = {s.id for s in symbols_ui.palette_symbols()}
    expected = {s.id for s in symbols.SYMBOLS.values() if not s.hidden} - {"volta.end"}
    assert shown == expected
    assert symbols_ui.count("all") == len(expected)
    assert symbols_ui.count("erhu") == sum(s.group in ("common", "erhu") for s in symbols_ui.palette_symbols())


def test_every_visible_symbol_has_a_usage_text_and_pinyin():
    for symbol in symbols_ui.palette_symbols():
        assert symbol.id in symbols_ui.USAGE, symbol.id
        assert len(symbols_ui.USAGE[symbol.id]) <= 32, symbol.id
    assert set(symbols_ui.PINYIN) == set(symbols.SYMBOLS)


@pytest.mark.parametrize("query", ["滑", "hua", "slide"])
def test_slide_searches_rank_the_slides_first(query):
    found = [s.id for s in symbols_ui.search(query, instrument="erhu")]
    assert found[0] == "slide.up"
    slides = ["slide.up", "slide.down", "slide.back_up", "slide.back_down", "slide.gliss"]   # 回滑音 since round 4
    if query != "slide":
        assert found[:6] == slides + ["line.slur"]
    else:
        assert found[:5] == slides


def test_simplified_input_and_syllable_matching():
    assert {s.id for s in symbols_ui.search("顿")[:3]} == {"art.staccato", "erhu.dun", "erhu.liandun"}
    assert symbols_ui.search("dunyin")[0].id == "art.staccato"
    assert "bar.double" not in {s.id for s in symbols_ui.search("hua")}     # 雙 shuang is not hua
    assert symbols_ui.search("渐强")[0].id == "hairpin.cresc"
    assert symbols_ui.segment_pinyin("huayin") == (["hua", "yin"], "")
    assert symbols_ui.search("xyzq") == []


def test_hidden_symbols_stay_hidden_but_are_reported():
    hidden = {s.id for s in symbols_ui.hidden_matches("滑")}
    assert hidden == {"slide.back", "slide.pad", "erhu.slide_vib"}
    assert not hidden & {s.id for s in symbols_ui.search("滑")}


def test_search_is_fast_enough_to_type(benchmark_free=None):
    import time
    started = time.perf_counter()
    for query in ("滑", "hua", "slide", "dunyin", "fanfu", "mf"):
        symbols_ui.search(query)
    assert (time.perf_counter() - started) / 6 < 0.05


# ---------------------------------------------------------------- availability matrix (design §2.5)
def contexts():
    score = Score(notes=[Note(1), Note(2), Note(0), Note(4), Note(5), Note(6), Note(7), Note(1)] * 2)
    return score, {
        "none": T(),
        "one": T("notes", 0, (0,)),
        "rest": T("notes", 0, (2,)),
        "run": T("notes", 0, (0, 1, 2, 3)),
        "scattered": T("notes", 0, (0, 5)),
        "bars": T("bars", 0, (4, 5, 6, 7), (1,)),
        "barline": T("barline", 0, (), (), 2),
    }


def test_every_symbol_in_every_selection_has_a_state_and_disabled_ones_say_why():
    score, targets = contexts()
    ids = re.compile(r"[a-z]+\.[a-z0-9_]+|_")
    for symbol in symbols_ui.palette_symbols(include_hidden=True):
        for name, target in targets.items():
            state = notation_edit.symbol_state(score, target, symbol.id)
            if not state.available:
                assert state.reason.strip(), (symbol.id, name)
                assert not ids.search(state.reason), (symbol.id, name, state.reason)
                assert re.search("[一-鿿]", state.reason), (symbol.id, name, state.reason)


def test_reasons_follow_the_selection_rules():
    score, t = contexts()
    assert notation_edit.symbol_state(score, t["none"], "finger.1").reason == "請先在譜上點一個音符。"
    assert "Shift" in notation_edit.symbol_state(score, t["none"], "line.slur").reason
    assert notation_edit.symbol_state(score, t["rest"], "finger.1").reason == "休止符不能加「一指」。"
    assert notation_edit.symbol_state(score, t["one"], "line.slur").reason == "需要選取連續兩個以上的音符。"
    assert notation_edit.symbol_state(score, t["scattered"], "line.slur").reason.startswith("選取不連續")
    assert notation_edit.symbol_state(score, t["bars"], "finger.1").reason.startswith("目前選的是整個小節")
    # Round 6: an ending can start from a bar line too (the bar right of it); the :‖ comes along.
    assert notation_edit.symbol_state(score, t["barline"], "volta.1").available
    assert notation_edit.symbol_state(score, t["one"], "line.tie").reason.startswith("延音線只能連到下一個相同音高的音")
    # Round 6: a structure is built one sign at a time -- D.S. before its Segno is allowed and
    # then listed as unfinished, with the next step.
    assert notation_edit.symbol_state(score, t["bars"], "nav.ds").available
    trial = score.copy()
    notation_edit.toggle_symbol(trial, t["bars"], "nav.ds")
    assert "還需要 Segno" in trial.notation_error() and trial.edit_error() is None
    assert notation_edit.symbol_state(score, t["run"], "dyn.mf").available     # rests are skipped
    assert notation_edit.symbol_state(score, t["one"], "slide.gliss").available  # to the next note


def test_palette_state_agrees_with_mutate_validation():
    """One source of truth: whatever the palette offers, the edit then accepts (and vice versa)."""
    score, targets = contexts()
    for symbol in symbols_ui.palette_symbols():
        if symbol.id == "tuplet.3" or symbol.storage == "composite":
            continue
        for target in targets.values():
            state = notation_edit.symbol_state(score, target, symbol.id)
            trial = score.copy()
            try:
                notation_edit.toggle_symbol(trial, target, symbol.id)
                trial.validate()
                # Round 6: an unfinished repeat/jump is a legal step (listed, not refused).
                ok = trial.edit_error() is None
            except ValueError:
                ok = False
            assert ok == state.available, (symbol.id, target, state)


def test_barline_and_measure_targets_follow_the_printed_side():
    score = Score(notes=[Note(1)] * 12)
    notation_edit.toggle_symbol(score, T("barline", 0, (), (), 1), "bar.repeat_start")
    assert score.notes[4].bar_start == ("bar.repeat_start",)          # right-hand bar's start
    notation_edit.toggle_symbol(score, T("bars", 0, tuple(range(8, 12)), (2,)), "bar.final")
    assert score.notes[11].bar_end == ("bar.final",)                  # the bar's right edge


def test_voltas_are_placed_where_they_print_and_stored_as_tech_md_wants():
    score = Score(notes=[Note(i % 7 + 1) for i in range(16)])
    bar = lambda b: T("bars", 0, tuple(range(4 * b, 4 * b + 4)), (b,))   # noqa: E731
    notation_edit.toggle_symbol(score, bar(0), "bar.repeat_start")
    notation_edit.toggle_symbol(score, bar(2), "bar.repeat_end")       # the user puts :‖ after bar 3
    notation_edit.toggle_symbol(score, bar(2), "volta.1")              # bar 3 is the first ending
    notation_edit.toggle_symbol(score, bar(3), "volta.2")
    assert score.notation_error() is None
    assert score.notes[7].bar_end == ("bar.repeat_end",)               # moved to the repeat body
    assert score.notes[8].bar_start == ("volta.1",) and score.notes[12].bar_start == ("volta.2",)
    assert score.notes[15].bar_end == ("volta.end",)
    notation_edit.toggle_symbol(score, bar(3), "volta.2")              # click again = remove
    notation_edit.toggle_symbol(score, bar(2), "volta.1")
    assert score.notes[11].bar_end == ("bar.repeat_end",) and score.notation_error() is None


# ---------------------------------------------------------------- the window
@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(MainWindow, "render_score", lambda *a, **k: None)
    errors = []
    monkeypatch.setattr(MainWindow, "error", lambda self, title, error: errors.append((title, str(error))))
    storage.open_settings().remove("palette")
    widget = MainWindow(recovery=False)
    widget.errors = errors
    widget.recovery_path = tmp_path / "recovery.jps"
    widget.autosave.stop()
    qtbot.addWidget(widget, before_close_func=lambda w: setattr(w, "dirty", False))
    widget.resize(1380, 850)
    widget.show()
    widget.reset(Project(Score(notes=[Note(i % 7 + 1) for i in range(16)])))
    widget.input_mode.setCurrentIndex(widget.input_mode.findData("edit"))
    QApplication.processEvents()
    return widget


def click_cell(qtbot, window, symbol_id, modifier=Qt.KeyboardModifier.NoModifier):
    grid = window.palette.grid
    entry = next(e for e in grid.entries if e.kind in ("cell", "row") and e.sid == symbol_id)
    grid.verticalScrollBar().setValue(int(entry.rect.top()) - 10)
    QApplication.processEvents()
    point = entry.rect.center().toPoint() - QPoint(0, grid.verticalScrollBar().value())
    qtbot.mouseClick(grid.viewport(), Qt.MouseButton.LeftButton, modifier, point)


def test_click_toggles_replaces_same_slot_and_undo_restores(window, qtbot):
    window.notes.select_rows([0], 0)
    click_cell(qtbot, window, "finger.2")
    assert window.project.score.notes[0].marks == ("finger.2",)
    click_cell(qtbot, window, "finger.1")                               # same slot: replaced
    assert window.project.score.notes[0].marks == ("finger.1",)
    assert window.toast.isVisible() and "二指" in window.toast.text.text() and "一指" in window.toast.text.text()
    window.undo()                                                       # one step back
    assert window.project.score.notes[0].marks == ("finger.2",)
    window.redo()
    click_cell(qtbot, window, "finger.1")                               # again = remove
    assert window.project.score.notes[0].marks == ()
    assert window.errors == []


def test_twenty_notes_is_one_undo_step_and_rests_are_skipped(window):
    notes = [Note(i % 7 + 1) for i in range(20)]
    notes[3] = notes[9] = Note(0)
    window.reset(Project(Score(notes=notes)))
    window.notes.select_rows(range(20))
    depth = len(window.history)
    window.palette_apply("art.accent")
    assert len(window.history) == depth + 1
    assert [bool(n.marks) for n in window.project.score.notes] == [n.degree != 0 for n in notes]
    assert "略過 2 個休止符" in window.toast.text.text()


def test_disabled_cell_explains_and_does_not_change_the_score(window, qtbot):
    window.notes.select_rows([], -1)
    before = window.project.snapshot()
    state = window.symbol_state("finger.1")
    assert not state.available and state.reason == "請先在譜上點一個音符。"
    click_cell(qtbot, window, "finger.1")
    assert window.project == before
    assert "請先在譜上點一個音符" in window.statusBar().currentMessage()
    window.palette.grid.hover_index = next(i for i, e in enumerate(window.palette.grid.entries)
                                           if e.kind == "cell" and e.sid == "finger.1")
    window.palette.detail.refresh()
    assert window.palette.detail.reason.isVisibleTo(window) and "請先在譜上點一個音符" in window.palette.detail.reason.text()


def test_continuous_apply_stamps_five_notes_then_escape_ends(window, qtbot):
    window.notes.select_rows([0], 0)
    click_cell(qtbot, window, "bow.down", Qt.KeyboardModifier.ShiftModifier)
    assert window.palette.continuous == "bow.down" and window.stamp_bar.isVisible()
    for row in (2, 3, 4, 5, 6):
        index = window.notes.model().index(row, 0)
        window.notes.scrollTo(index)
        qtbot.mouseClick(window.notes.viewport(), Qt.MouseButton.LeftButton, pos=window.notes.visualRect(index).center())
    assert [("bow.down" in window.project.score.notes[r].marks) for r in range(8)] == [
        True, False, True, True, True, True, True, False]
    qtbot.keyClick(window.notes, Qt.Key.Key_Escape)
    assert window.palette.continuous == "" and not window.stamp_bar.isVisible()


def test_favourites_and_recents_persist(window, qtbot):
    window.notes.select_rows([0], 0)
    window.palette_apply("erhu.vibrato")
    window.palette.toggle_favorite("finger.3")          # the 9 defaults are full: make room
    window.palette.toggle_favorite("art.accent")
    assert window.palette.recents[0] == "erhu.vibrato"
    settings = storage.open_settings()
    assert "art.accent" in settings.value("palette/favorites")
    second = MainWindow(recovery=False)
    qtbot.addWidget(second, before_close_func=lambda w: setattr(w, "dirty", False))
    second.autosave.stop()
    assert second.palette.recents[0] == "erhu.vibrato" and "art.accent" in second.palette.favorites
    # Alt+number applies favourite N from the score.
    window.notes.select_rows([1], 1)
    window.notes.setFocus()
    key = window.palette.section_ids("favorites").index("finger.2") + 1
    qtbot.keyClick(window.notes, getattr(Qt.Key, f"Key_{key}"), Qt.KeyboardModifier.AltModifier)
    assert "finger.2" in window.project.score.notes[1].marks


def test_keyboard_only_search_flow(window, qtbot):
    window.notes.select_rows([4], 4)
    window.notes.setFocus()
    qtbot.keyClick(window.notes, Qt.Key.Key_Slash)
    assert window.palette.search.hasFocus()
    qtbot.keyClicks(window.palette.search, "hua")
    assert window.palette.grid.focused_sid() == "slide.up"
    qtbot.keyClick(window.palette.search, Qt.Key.Key_Return)
    assert "slide.up" in window.project.score.notes[4].marks
    assert window.notes.hasFocus()                                       # back to the score
    qtbot.keyClick(window.notes, "6")                                    # keep typing music
    assert window.project.score.notes[4].degree == 6
    window.palette.focus_search()
    qtbot.keyClick(window.palette.search, Qt.Key.Key_Escape)             # clears the query
    assert window.palette.search.text() == ""
    qtbot.keyClick(window.palette.search, Qt.Key.Key_Escape)             # then back to the score
    assert window.notes.hasFocus()


def test_hidden_symbols_have_an_opt_in(window):
    window.palette.search.setText("滑")
    assert window.palette.hidden_row.isVisible() and "回滑音" in window.palette.hidden_label.text()
    window.palette.show_hidden_button.click()
    assert "slide.back" in window.palette.grid.results
    window.palette.show_hidden_button.click()
    assert "slide.back" not in window.palette.grid.results


def test_inspector_lists_marks_and_removes_one_click(window, qtbot):
    window.notes.select_rows([0], 0)
    window.palette_apply("finger.3")
    window.palette_apply("bow.down")
    chips = {chip.sid: chip for chip in window.inspector.chip_widgets}
    assert set(chips) == {"finger.3", "bow.down"}
    qtbot.mouseClick(chips["bow.down"].remove, Qt.MouseButton.LeftButton)
    assert window.project.score.notes[0].marks == ("finger.3",)
    window.undo()
    assert "bow.down" in window.project.score.notes[0].marks


def test_measure_selection_shows_bar_marks_and_actions(window, qtbot):
    window.notes.select_bar(1)
    window.palette_apply("bar.double")
    assert window.inspector.title.text().startswith("第 2 小節")
    assert window.inspector.actions.isVisible()
    assert [c.sid for c in window.inspector.chip_widgets] == ["bar.double"]
    qtbot.mouseClick(window.inspector.measure_buttons["duplicate"], Qt.MouseButton.LeftButton)
    assert len(window.project.score.part_bars(0)) == 5


# ---------------------------------------------------------------- parts and pickup
def duet():
    return Project(Score(parts=[Part("二胡一", [Note(i % 7 + 1) for i in range(8)]),
                                Part("二胡二", [Note(0)] * 8)]))


def test_switch_parts_by_chip_click_and_keyboard_and_edit_part_two(window, qtbot):
    window.reset(duet())
    window.input_mode.setCurrentIndex(window.input_mode.findData("edit"))
    window.notes.select_rows([5], 5)
    qtbot.keyClick(window.notes, Qt.Key.Key_Down, Qt.KeyboardModifier.AltModifier)
    assert window.part == 1 and window.notes.currentRow() == 5           # same time position
    qtbot.keyClick(window.notes, "3")
    score = window.project.score
    assert score.parts[1].notes[5].degree == 3 and score.parts[0].notes[5].degree == 6
    window.palette_apply("finger.2")
    assert window.project.score.parts[1].notes[5].marks == ("finger.2",)
    assert window.project.score.parts[0].notes[5].marks == ()
    window.notes.select_bar(1)
    window.palette_apply("bar.final")                                    # bar items live on part 0
    assert window.project.score.parts[0].notes[7].bar_end == ("bar.final",)
    window.part_buttons[0].click()
    assert window.part == 0
    # clicking a note of the other part switches and selects it
    other = window.notes.part_rects[1][2].center() - QPoint(window.notes.horizontalOffset(), window.notes.verticalOffset())
    qtbot.mouseClick(window.notes.viewport(), Qt.MouseButton.LeftButton, pos=other)
    assert window.part == 1 and window.notes.selected_rows() == [2]
    window.undo()
    window.undo()
    assert window.project.score.parts[1].notes[5].marks == ()


def test_add_part_keeps_other_marks_and_is_undoable(window):
    window.notes.select_rows([0], 0)
    window.palette_apply("finger.1")
    window.add_part()
    score = window.project.score
    assert len(score.parts) == 2 and window.part == 1
    assert sum(n.ticks for n in score.parts[1].notes) == sum(n.ticks for n in score.parts[0].notes)
    assert score.parts[0].notes[0].marks == ("finger.1",)
    window.undo()
    assert len(window.project.score.parts) == 1


def test_pickup_control_lists_only_legal_lengths(window, monkeypatch):
    items = [window.pickup_field.itemData(i) for i in range(window.pickup_field.count())]
    assert items[0] == 0 and 48 in items and 192 not in items          # 4/4: shorter than a bar
    window.reset(Project(Score(notes=[Note(5)] + [Note(1)] * 4 + [Note(1), Note(1), Note(1)])))
    # round 8: a pickup on written music asks first, even when no note has to change
    asked = []
    monkeypatch.setattr(window, "choose_rhythm_option", lambda refused: asked.append(refused) or "rebar")
    window.pickup_field.setCurrentIndex(window.pickup_field.findData(48))
    assert len(asked) == 1
    assert window.project.score.pickup == 48
    assert window.project.score.rhythm_error() is None
    window.undo()
    assert window.project.score.pickup == 0


# ---------------------------------------------------------------- theme and language
def test_theme_switch_recolours_without_hard_coded_colours(window, monkeypatch):
    from pathlib import Path

    from jianpu_studio import theme
    for path in sorted((Path(theme.__file__).parent).glob("*.py")):
        if path.name in ("theme.py", "symbols_data.py"):
            continue
        assert not re.search(r"#[0-9a-fA-F]{6}\b", path.read_text(encoding="utf-8")), path.name
    window.theme.set_appearance("dark")
    assert window.theme.scheme == "dark"
    assert window.notes.colors.paper.name() == theme.DARK["paper"]
    assert QApplication.instance().palette().window().color().name() == theme.DARK["window"]
    assert theme.DARK["panel"] in QApplication.instance().styleSheet()
    window.theme.set_appearance("light")
    assert window.notes.colors.paper.name() == theme.LIGHT["paper"]
    window.theme.set_appearance("system")


def test_language_switch_retranslates_palette_and_inspector(window):
    window.notes.select_rows([0], 0)
    window.palette.search.setText("滑")
    window.language_actions["zh_CN"].trigger()
    assert window.palette.categories.item(0).text() == "常用"
    assert window.palette.instrument_buttons["guzheng"].text() == "古筝"
    assert window.inspector.heading.text() == "选中"
    assert window.palette.search.text() == "滑"                          # user input is kept
    assert i18n.tr(symbols_ui.USAGE["slide.up"]).startswith("从低处滑进")
    window.language_actions["zh_TW"].trigger()
    assert window.palette.instrument_buttons["guzheng"].text() == "古箏"


# ---------------------------------------------------------------- small windows
def visible_leaves(window):
    from PySide6.QtWidgets import QAbstractButton, QComboBox, QLabel, QLineEdit, QWidget
    for widget in window.findChildren(QWidget):
        if (isinstance(widget, (QAbstractButton, QLabel, QLineEdit, QComboBox)) and widget.isVisible()
                and widget.window() is window and widget.width() > 0):
            yield widget


@pytest.mark.parametrize("language", ["zh_TW", "zh_CN"])
@pytest.mark.parametrize("size", [(1000, 630), (1380, 850)])
def test_no_control_is_outside_the_window_or_overlapping(window, size, language):
    window.reset(duet())
    window.notes.select_rows([1], 1)
    window.change_language(language)
    window.resize(*size)
    QApplication.processEvents()
    rect = window.rect()
    boxes = []
    for widget in visible_leaves(window):
        top_left = widget.mapTo(window, QPoint(0, 0))
        box = widget.rect().translated(top_left)
        assert rect.contains(box), (widget.objectName() or type(widget).__name__, box, size)
        boxes.append((widget, box))
    # Leaf controls inside the same bar never overlap each other.
    for bar in (window.toolbar, window.score_settings, window.note_tools):
        inside = [(w, b) for w, b in boxes if bar.isAncestorOf(w) and not any(
            w is not o and o.isAncestorOf(w) for o, _ in boxes)]
        for i, (a, box_a) in enumerate(inside):
            for b, box_b in inside[i + 1:]:
                overlap = box_a.intersected(box_b)
                assert overlap.width() <= 1 or overlap.height() <= 1, (a.objectName(), b.objectName(), size)
    assert window.notes.viewport().width() >= (700 if size[0] == 1000 else 1000)
    assert window.notes.viewport().height() >= (380 if size[0] == 1000 else 600)
    window.change_language("zh_TW")


# ---------------------------------------------------------------- previews, discoverability, packaging
def test_every_visible_symbol_has_a_lilypond_preview_shipped_as_package_data():
    from pathlib import Path

    from PySide6.QtGui import QImage

    from jianpu_studio.palette import preview_path
    for symbol in symbols_ui.palette_symbols():
        path = preview_path(symbol.id)
        assert path.is_file(), symbol.id
        image = QImage(str(path))
        assert not image.isNull() and image.hasAlphaChannel(), symbol.id
    pyproject = (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    assert '"assets/symbols/*.png"' in pyproject and '"assets/icons/*.svg"' in pyproject


def test_f1_shortcuts_are_searchable_and_non_modal(window, qtbot):
    window.show_help()
    dialog = window.shortcuts_dialog
    assert dialog.isVisible() and not dialog.isModal()
    dialog.filter.setText("Ctrl+B")
    visible = [dialog.tree.topLevelItem(i).child(j).text(1)
               for i in range(dialog.tree.topLevelItemCount()) for j in range(dialog.tree.topLevelItem(i).childCount())
               if not dialog.tree.topLevelItem(i).child(j).isHidden()]
    assert visible == ["曲尾加一小節"]
    window.notes.setFocus()
    qtbot.keyClick(window.notes, "3")                                    # writing continues meanwhile
    dialog.close()


def test_f6_cycles_score_palette_inspector_and_score_bar(window, qtbot):
    window.notes.select_rows([0], 0)
    window.palette_apply("finger.1")
    window.notes.setFocus()
    order = []
    for _ in range(4):
        window.cycle_focus(1)
        focus = QApplication.focusWidget()
        order.append("score" if focus is window.notes else "palette" if focus is window.palette.search
                     else "inspector" if window.inspector.isAncestorOf(focus) else
                     "scorebar" if window.score_settings.isAncestorOf(focus) else type(focus).__name__)
    assert order == ["palette", "inspector", "scorebar", "score"]


@pytest.mark.parametrize("longer", [1, 0])
def test_bar_symbols_on_a_bar_the_first_part_does_not_have_yet(longer):
    """Review P1.1: a part longer than part 1 (while it is written) must not crash the palette."""
    long, short = [Note(1, duration=1), Note(2), Note(3)], [Note(5, duration=1)]
    parts = [Part("一", short), Part("二", long)] if longer else [Part("一", long), Part("二", short)]
    score = Score(parts=parts)
    score.validate()
    # rows 1-2 are the second bar of the longer part (part 2 when ``longer``, else part 1)
    targets = [Target("notes", longer, (1,)), Target("notes", longer, (1, 2)), Target("bars", longer, (1, 2), (1,)),
               Target("barline", longer, (), (), 2)]
    original = score.copy()
    for target in targets:
        for symbol in symbols.SYMBOLS.values():
            state = notation_edit.symbol_state(score, target, symbol.id)     # must not raise
            if symbol.storage == "bar" and longer:
                assert not state.available and state.reason, (target, symbol.id)
            notation_edit.target_where(score, target, symbol.id)
            if symbol.storage == "bar" and longer:
                trial = score.copy()
                with pytest.raises(ValueError):
                    notation_edit.toggle_symbol(trial, target, symbol.id)
    assert score == original


def test_start_page_templates_and_example(window):
    window.show_start_page()
    assert window.editor_stack.currentIndex() == 1
    window.new_from_template("duet")
    score = window.project.score
    assert window.editor_stack.currentIndex() == 0
    assert [p.name for p in score.parts] == ["二胡一", "二胡二"] and score.key == "D"
    assert len(score.part_bars(0)) == 8 and score.rhythm_error() is None
    window.dirty = False
    window.open_example("duet")
    assert window.project.score.title == "晨光二重奏" and len(window.project.score.parts) == 2
