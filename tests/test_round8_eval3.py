"""Third evaluation (eval3) fixes in the editor window.

1. ↑ ↓ . ♯ ♭ ♮ act on the selected note in every mode; with nothing to act on they prepare the
   next note, visibly, for one note only.
3. A pickup set on written music asks before the whole piece is re-barred.
5. A Coda sign next to the automatic "⊕ Coda" is refused (and never drawn twice).
6. The refusal toast wraps instead of being cut off.
7. 「新建」 and the start page's 「空白樂譜」 give the same score.
"""
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from jianpu_studio import storage
from jianpu_studio.app import MainWindow, demo_project
from jianpu_studio.model import Note, Project, Score


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
    widget.resize(1280, 800)
    widget.show()
    QApplication.processEvents()
    yield widget
    QApplication.clipboard().clear()


def mode(window, name):
    window.input_mode.setCurrentIndex(window.input_mode.findData(name))


def octaves(window):
    return [n.octave for n in window.project.score.notes]


# ---------------------------------------------------------------- 1. modifiers act on the selection
@pytest.mark.parametrize("name", ["insert", "fill", "edit"])
def test_octave_keys_and_buttons_change_the_selected_note_in_every_mode(window, qtbot, name):
    window.reset(Project(Score(notes=[Note(1), Note(2), Note(3), Note(4)])))
    mode(window, name)
    window.notes.select_rows([1], 1)
    qtbot.keyClick(window.notes, Qt.Key.Key_Up)
    assert octaves(window) == [0, 1, 0, 0]
    qtbot.mouseClick(window.octave_down_button, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(window.octave_down_button, Qt.MouseButton.LeftButton)
    assert octaves(window) == [0, -1, 0, 0]
    assert window.next_note.octave == 0                      # nothing was left for later notes
    window.undo()
    assert octaves(window) == [0, 0, 0, 0]                   # each press is one undo step


@pytest.mark.parametrize("name", ["insert", "fill", "edit"])
def test_accidental_and_dot_change_the_selected_note_in_every_mode(window, qtbot, name):
    window.reset(Project(Score(notes=[Note(1), Note(2), Note(0, duration=2)])))
    mode(window, name)
    window.notes.select_rows([1], 1)
    qtbot.mouseClick(window.accidental_buttons["#"], Qt.MouseButton.LeftButton)
    assert [n.accidental for n in window.project.score.notes] == ["", "#", ""]
    assert window.accidental_buttons["#"].isChecked()        # the button shows the selected note
    qtbot.keyClick(window.notes, Qt.Key.Key_Period)
    notes = window.project.score.notes
    assert notes[1].dotted and notes[1].degree == 2 and sum(n.ticks for n in notes) == 4 * 48
    assert window.dot_button.isChecked()
    assert not window.next_note.dotted and not window.next_note.accidental


def test_up_after_typing_raises_the_note_just_typed_not_the_following_ones(window, qtbot):
    """The evaluator's trap: 填寫, type 5 3, press ↑, go on typing."""
    window.new_from_template("blank")
    for key in "53":
        qtbot.keyClick(window.notes, key)
    qtbot.keyClick(window.notes, Qt.Key.Key_Up)
    assert octaves(window)[:2] == [0, 1]
    qtbot.keyClick(window.notes, Qt.Key.Key_Up)              # again: the same note, two octaves
    assert octaves(window)[:2] == [0, 2]
    for key in "12":
        qtbot.keyClick(window.notes, key)
    assert [(n.degree, n.octave) for n in window.project.score.notes[:4]] == [(5, 0), (3, 2), (1, 0), (2, 0)]


def test_a_prepared_octave_is_shown_and_used_by_one_note_only(window, qtbot):
    window.new_from_template("blank")                        # the caret is on a rest, nothing typed yet
    qtbot.keyClick(window.notes, Qt.Key.Key_Up)
    assert window.next_note.octave == 1
    assert window.octave_up_button.isChecked() and not window.octave_down_button.isChecked()
    assert "高八度" in window.selection_label.text() and "一個音" in window.selection_label.text()
    for key in "65":
        qtbot.keyClick(window.notes, key)
    assert [(n.degree, n.octave) for n in window.project.score.notes[:2]] == [(6, 1), (5, 0)]
    assert window.next_note.octave == 0 and not window.octave_up_button.isChecked()
    assert "高八度" not in window.selection_label.text()


def test_a_prepared_accidental_is_used_by_one_note_only(window, qtbot):
    window.new_from_template("blank")
    qtbot.mouseClick(window.accidental_buttons["b"], Qt.MouseButton.LeftButton)
    assert window.accidental_buttons["b"].isChecked() and "♭" in window.selection_label.text()
    for key in "76":
        qtbot.keyClick(window.notes, key)
    assert [(n.degree, n.accidental) for n in window.project.score.notes[:2]] == [(7, "b"), (6, "")]
    assert not window.accidental_buttons["b"].isChecked()


def test_modifiers_on_a_selected_rest_in_insert_mode_never_touch_other_notes(window, qtbot):
    window.reset(Project(Score(notes=[Note(1), Note(0), Note(3), Note(4)])))
    mode(window, "insert")
    window.notes.select_rows([1], 1)
    qtbot.keyClick(window.notes, Qt.Key.Key_Up)              # a rest has no octave: the next note's
    assert octaves(window) == [0, 0, 0, 0] and window.next_note.octave == 1
    assert window.octave_up_button.isChecked()


# ---------------------------------------------------------------- 7. 新建 = 空白樂譜
def test_new_and_the_blank_template_give_the_same_score(window, qtbot):
    window.new_from_template("blank")
    template = (window.project.snapshot(), window.input_mode.currentData(), window.notes.selected_rows())
    score = window.project.score
    assert (score.key, score.meter, score.tempo, len(score.part_bars(0))) == ("D", "4/4", 72, 8)
    assert all(n.degree == 0 for n in score.notes) and template[1] == "fill"

    window.reset(demo_project())
    qtbot.mouseClick(window.new_button, Qt.MouseButton.LeftButton)        # toolbar 「新建」
    assert (window.project, window.input_mode.currentData(), window.notes.selected_rows()) == template
    assert not window.dirty

    window.reset(demo_project())
    window.new_project()                                                  # File → 新建 / Ctrl+N
    assert (window.project, window.input_mode.currentData(), window.notes.selected_rows()) == template
    qtbot.keyClick(window.notes, "5")                                     # typing fills the first bar
    assert window.project.score.notes[0] == Note(5) and len(window.project.score.part_bars(0)) == 8


# ---------------------------------------------------------------- 6. the refusal toast wraps
def toast_shows_all_of_its_text(window):
    """The toast lies inside the canvas, and its label is tall enough for every wrapped line."""
    from PySide6.QtCore import QRect
    toast, label = window.toast, window.toast.text
    assert toast.isVisible() and label.wordWrap()
    assert window.notes.rect().contains(toast.geometry()), (toast.geometry(), window.notes.rect())
    assert toast.rect().contains(label.geometry())
    needed = label.fontMetrics().boundingRect(QRect(0, 0, label.width(), 100000),
                                              int(Qt.TextFlag.TextWordWrap), label.text())
    return needed.height() <= label.height() and needed.width() <= label.width()


@pytest.mark.parametrize("size", [(960, 600), (1280, 800)])
def test_a_long_refusal_is_wrapped_inside_the_window_with_its_suggestion_readable(window, qtbot, size):
    window.resize(*size)
    window.reset(demo_project())
    mode(window, "insert")
    window.notes.select_rows([9], 9)                     # a note in a full bar: inserting is refused
    before = window.project.snapshot()
    qtbot.keyClick(window.notes, "7")
    assert window.project == before
    text = window.toast.text.text()
    assert text.startswith("⚠") and text.rstrip("。").endswith("改用「填寫」")       # the fix is the last clause
    assert toast_shows_all_of_its_text(window)
    if size[0] == 960:
        assert window.toast.text.height() > 1.5 * window.toast.text.fontMetrics().height()    # really wrapped


def test_a_short_notice_with_undo_stays_one_line(window):
    window.resize(960, 600)
    window.reset(demo_project())
    window.toast.show_text("已加上「一指」。", window.undo)
    QApplication.processEvents()
    assert toast_shows_all_of_its_text(window) and window.toast.undo.isVisible()
    assert window.toast.text.height() < 1.5 * window.toast.text.fontMetrics().height()
    assert window.toast.rect().contains(window.toast.undo.geometry())


# ---------------------------------------------------------------- 3. a pickup asks before re-barring
def set_pickup(window, ticks):
    window.pickup_field.setCurrentIndex(window.pickup_field.findData(ticks))
    QApplication.processEvents()


def test_a_pickup_on_written_music_asks_and_cancel_leaves_the_score_untouched(window, monkeypatch):
    """小星星 fits a quarter pickup without splitting a note; it was re-barred without a word."""
    window.reset(demo_project())
    before = window.project.snapshot()
    asked = []
    monkeypatch.setattr(window, "choose_rhythm_option", lambda refused: asked.append(refused) or None)
    set_pickup(window, 48)
    assert len(asked) == 1 and window.project == before and not window.dirty and not window.history
    assert window.pickup_field.currentData() == 0                 # the field shows the real pickup again
    text = str(asked[0])
    assert "弱起" in text and "小節線" in text
    assert "12 小節 → 13 小節" in text and "可用復原還原" in text      # what will change, and the way back
    assert "rebar" in [choice.key for choice in asked[0].choices]


def test_an_accepted_pickup_is_one_undo_step(window, monkeypatch):
    window.reset(demo_project())
    before = window.project.snapshot()
    monkeypatch.setattr(window, "choose_rhythm_option", lambda refused: "rebar")
    set_pickup(window, 48)
    score = window.project.score
    assert score.pickup == 48 and score.rhythm_error() is None and len(window.history) == 1
    assert [n.degree for n in score.notes if n.degree] == [n.degree for n in before.score.notes if n.degree]
    window.undo()
    assert window.project == before and window.pickup_field.currentData() == 0


def test_an_empty_score_takes_a_pickup_without_a_question(window, monkeypatch):
    window.new_from_template("blank")
    monkeypatch.setattr(window, "choose_rhythm_option", lambda refused: pytest.fail("asked on an empty score"))
    set_pickup(window, 48)
    assert window.project.score.pickup == 48


# ---------------------------------------------------------------- 5. one Coda sign
def ds_al_coda(window):
    """小星星 with Segno (bar 5), To Coda (bar 8) and D.S. (bar 10): the coda starts at bar 11."""
    window.reset(demo_project())
    for sid, bar in (("nav.ds", 9), ("nav.tocoda", 7), ("nav.segno", 4)):
        window.notes.select_bars([bar])
        window.palette_apply(sid)
    assert window.project.score.notation_error() is None


def test_a_coda_sign_on_the_automatic_coda_bar_is_refused_with_the_reason(window):
    ds_al_coda(window)
    window.notes.select_bars([10])
    state = window.symbol_state("nav.coda_sign")
    assert not state.available and "自動印出 Coda 記號" in state.reason and "第 11 小節" in state.reason
    before = window.project.snapshot()
    window.palette_apply("nav.coda_sign")
    assert window.project == before                                     # nothing added
    assert window.toast.isVisible() and "Coda 記號" in window.toast.text.text()
    # the same sign on another bar is still available (a second ending's coda, a rehearsal sign...)
    window.notes.select_bars([11])
    assert window.symbol_state("nav.coda_sign").available


def test_a_print_only_twin_of_a_working_sign_is_refused(window):
    ds_al_coda(window)
    for sid, bar, name in (("nav.segno_sign", 4, "Segno"), ("nav.dscoda_text", 9, "D.S."), ("nav.ds_text", 9, "D.S."),
                           ("nav.tocoda_text", 7, "To Coda")):
        window.notes.select_bars([bar])
        state = window.symbol_state(sid)
        assert not state.available and name in state.reason, sid
    window.notes.select_bars([2])                                        # a bar without the working sign
    assert window.symbol_state("nav.tocoda_text").available


# ---------------------------------------------------------------- P2: messages
def test_a_score_that_is_only_a_pickup_never_says_bar_0(window, qtbot):
    """弱起 on an empty score, one note typed: the pickup is complete; the last bar is missing."""
    window.reset(Project(Score(pickup=48, notes=[Note(5)])))
    QApplication.processEvents()
    status = window.rhythm_label.text()
    assert "第 0 小節" not in status and "弱起小節" in status and "還缺 3 拍" in status
    problem = str(window.project.score.rhythm_error())
    assert "第 0 小節" not in problem and "弱起小節後面還缺 3 拍" in problem
    window.fix_problem()                                           # 補齊休止符 still completes it
    assert window.project.score.rhythm_error() is None and len(window.project.score.part_bars(0)) == 2


def test_a_missing_file_gets_a_sentence_not_an_errno(window, tmp_path):
    missing = tmp_path / "不見了 的譜.jps"
    before = window.project.snapshot()
    window.open_file(path=missing)
    assert window.project == before
    title, text = window.errors[-1]
    assert title == "無法開啟樂譜" and "找不到這個檔案" in text and str(missing) in text
    assert "Errno" not in text and "No such file" not in text


def test_the_unsaved_question_names_the_file(window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    asked = []
    monkeypatch.setattr(QMessageBox, "question",
                        lambda parent, title, text, *a, **k: asked.append(text) or QMessageBox.StandardButton.Cancel)
    window.reset(demo_project(), tmp_path / "春江 花月夜.jps")
    window.notes.select_rows([0], 0)
    mode(window, "edit")
    window.input_pitch(3)
    assert window.dirty
    window.new_project()
    assert asked and "春江 花月夜.jps" in asked[-1]
    window.path = None                                             # never saved: the title stands in
    window.new_project()
    assert "小星星" in asked[-1]


def test_a_whole_bar_rest_is_called_a_whole_rest(window):
    window.new_from_template("blank")
    assert window.inspector.props.text() == "全休止符"
    from jianpu_studio.inspector import rest_name, spoken_note
    assert rest_name(Note(0, duration=4, dotted=True)) == "四分休止符（附點）"
    assert spoken_note(Note(4, octave=1, duration=8, accidental="#")) == "升 4，八分音符，高音區"


def test_screen_reader_names_have_no_engine_codes(window):
    notes = [Note(5), Note(6, octave=1, duration=8), Note(1, duration=8, accidental="b"), Note(0, duration=2)]
    window.reset(Project(Score(pickup=48, notes=notes)))
    model = window.notes.model()
    texts = [model.data(model.index(row, 0), Qt.ItemDataRole.AccessibleTextRole) for row in range(len(notes))]
    assert texts[0].startswith("弱起小節，第 1 個音符：5，四分音符，中音區")
    assert texts[1].startswith("第 1 小節，第 2 個音符：6，八分音符，高音區")
    assert "降 1" in texts[2] and "二分休止符" in texts[3]
    import re
    for text in texts:
        assert not re.search(r"[cqsdhwb#]\d|\d'|- -", text), text          # no c5, q6', 1' - - -


# ---------------------------------------------------------------- P2: tuning follows the key
def test_tuning_suggestions_follow_the_key_and_any_tuning_prints_in_the_header(window):
    from jianpu_studio import symbols_ui
    from jianpu_studio.export import header_words
    from jianpu_studio.symbols_ui import TEXT_PREFIX
    from jianpu_studio.text_words import header_parts, open_string_degrees
    assert symbols_ui.text_suggestions("定弦", key="Bb")[:2] == ["定弦 3–7", "37弦"]
    assert symbols_ui.text_suggestions("dingxian", key="G")[:2] == ["定弦 5–2", "52弦"]
    assert symbols_ui.text_suggestions("定弦", key="D")[:2] == ["定弦 1–5", "15弦"]
    assert open_string_degrees("F") == (6, 3) and open_string_degrees("E") is None
    assert header_words("37弦") and header_words("中速 定弦 3–7")
    assert header_parts("中速 37弦 稍自由") == (["中速", "稍自由"], ["37弦"])
    # in the window: 1=♭B, search 定弦, the first result is this key's tuning
    window.reset(Project(Score(key="Bb", notes=[Note(3), Note(5), Note(6), Note(1)])))
    window.palette.search.setText("定弦")
    assert window.palette.grid.results[0] == TEXT_PREFIX + "定弦 3–7"
    window.notes.select_rows([0], 0)
    window.palette_apply(window.palette.grid.results[0])
    QApplication.processEvents()
    meta = [block[1] for block in window.notes.header_blocks if "1 =" in block[1]][0]
    assert meta.endswith("（定弦 3–7）") and meta.index("2/4" if False else "4/4") < meta.index("♩")
    assert window.notes.part_labels[0][0] is None                 # not also above the note


def test_open_string_zero_on_another_pitch_is_applied_with_a_reminder(window):
    window.reset(Project(Score(key="Bb", notes=[Note(3), Note(5), Note(7), Note(1)])))
    mode(window, "edit")
    window.notes.select_rows([1], 1)                              # 5 is not an open string in 1=♭B (3 and 7)
    window.palette_apply("finger.0")
    assert "finger.0" in window.project.score.notes[1].marks      # applied: only a reminder
    text = window.toast.text.text()
    assert "空弦" in text and "內弦 3" in text and "外弦 7" in text and "第 1 小節" in text
    window.notes.select_rows([0], 0)                              # 3 is the inner string: no reminder
    window.palette_apply("finger.0")
    assert "finger.0" in window.project.score.notes[0].marks
    assert not (window.toast.isVisible() and "空弦" in window.toast.text.text())


# ---------------------------------------------------------------- P2: the palette
def grid_cells(window):
    grid = window.palette.grid
    grid.relayout()
    return [e for e in grid.entries if e.kind == "cell"]


def test_jump_signs_come_first_and_print_only_texts_have_their_own_group(window):
    from jianpu_studio import symbols
    window.palette.set_instrument("erhu", save=False)
    grid = window.palette.grid
    grid.relayout()
    section = [e for e in grid.entries if e.section == "navigation" and e.kind in ("subhead", "cell")]
    kinds = [e.kind for e in section]
    assert kinds[0] == "subhead" and kinds.count("subhead") == 2
    second = kinds.index("subhead", 1)
    working, printed = section[1:second], section[second + 1:]
    assert [e.sid for e in working] == ["nav.segno", "nav.tocoda", "nav.ds", "nav.dc", "nav.fine"]
    assert printed and all(symbols.SYMBOLS[e.sid].print_only for e in printed)
    assert "會跳" in section[0].text and "不跳" in section[second].text
    # the working group is laid out above the print-only one
    assert max(e.rect.bottom() for e in working) < min(e.rect.top() for e in printed)
    # a sub-head is not a cell: keyboard focus and clicks skip it
    assert all(grid.entries[i].kind in ("cell", "chip") for i in grid.items())


@pytest.mark.parametrize("language", ["zh_TW", "zh_CN"])
def test_no_two_cells_of_the_erhu_palette_share_a_name(window, language):
    from collections import Counter

    from jianpu_studio import i18n
    i18n.set_language(language)
    try:
        window.palette.set_instrument("erhu", save=False)
        names = Counter(e.text for e in grid_cells(window))
        assert [name for name, count in names.items() if count > 1] == []
    finally:
        i18n.set_language(i18n.TRADITIONAL)


def test_the_category_highlight_follows_the_scroll_to_the_last_section(window):
    window.resize(1280, 800)
    QApplication.processEvents()
    palette, grid = window.palette, window.palette.grid
    grid.relayout()
    bar = grid.verticalScrollBar()
    assert bar.maximum() > 0

    def highlighted():
        return palette.categories.currentItem().data(Qt.ItemDataRole.UserRole)
    seen = []
    for value in range(0, bar.maximum() + 1, 10):
        bar.setValue(value)
        if not seen or seen[-1] != highlighted():
            seen.append(highlighted())
    bar.setValue(bar.maximum())
    sections = [s for s in grid.sections if palette.section_ids(s) or s == "favorites"]
    assert highlighted() == sections[-1]                          # it used to stop one or two short
    assert seen == [s for s in sections if s in seen] and set(sections) - set(seen) <= {"recents"}
    # the highlighted section is on screen at every position
    for value in range(0, bar.maximum() + 1, 25):
        bar.setValue(value)
        head = grid.entries[grid.sections[highlighted()]].rect
        following = [grid.entries[i].rect.top() for s, i in grid.sections.items() if grid.entries[i].rect.top() > head.top()]
        end = min(following, default=grid.content_height)
        assert head.top() < value + grid.viewport().height() and end > value, (value, highlighted())
    # clicking a category near the end highlights it although it cannot reach the top
    target = sections[-2]
    item = next(palette.categories.item(row) for row in range(palette.categories.count())
                if palette.categories.item(row).data(Qt.ItemDataRole.UserRole) == target)
    palette._category_clicked(item)
    assert highlighted() == target


def test_a_short_window_shows_four_rows_of_cells_and_a_readable_reason(window):
    window.resize(960, 600)
    window.reset(demo_project())
    QApplication.processEvents()
    palette, grid, detail = window.palette, window.palette.grid, window.palette.detail
    assert detail.short and detail.height() < 70                  # was about 120
    grid.relayout()
    # anywhere in the grid: at least four rows of cells fit the viewport
    rows = sorted({round(e.rect.top()) for e in grid.entries if e.kind == "cell" and e.section == "technique"})
    cell_height = next(e.rect.height() for e in grid.entries if e.kind == "cell")
    assert len(rows) >= 4 and rows[3] + cell_height - rows[0] <= grid.viewport().height()
    # a symbol that cannot be used: the reason is shown whole (the usage line gives way to it)
    window.notes.select_rows([], -1)
    palette.detail_sid = ""
    grid.focus_index = next(i for i, e in enumerate(grid.entries) if e.sid == "orn.trill" and e.kind == "cell")
    grid.setFocus()
    grid.focusChanged.emit("orn.trill")
    QApplication.processEvents()
    assert detail.name.text().startswith("顫音") and detail.reason.isVisible() and not detail.usage_line.isVisible()
    label = detail.reason
    from PySide6.QtCore import QRect
    needed = label.fontMetrics().boundingRect(QRect(0, 0, label.width(), 10000), int(Qt.TextFlag.TextWordWrap), label.text())
    assert needed.height() <= label.height() + 1
    assert detail.usage_line.toolTip() and label.toolTip() == label.text()
    # a taller window keeps the full strip
    window.resize(1280, 800)
    QApplication.processEvents()
    assert not detail.short and detail.usage.isVisible() and not detail.usage_line.isVisible()
