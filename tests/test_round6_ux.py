"""Round 6 review fixes: text words in search, favourites full, one-shot dot, paste over bars,
saved state after undo, recovery keeps its file, focus on the score, key labels, problems shown
for an unfinished structure (with a one-click counterpart)."""

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from jianpu_studio import symbols_ui
from jianpu_studio.app import MainWindow
from jianpu_studio.model import Note, Project, Score, key_label, save_project
from jianpu_studio.symbols_ui import MAX_FAVORITES, TEXT_PREFIX


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(MainWindow, "render_score", lambda *a, **k: None)
    errors = []
    monkeypatch.setattr(MainWindow, "error", lambda self, title, error: errors.append((title, str(error))))
    QSettings("JianpuStudio", "JianpuStudio").remove("palette")
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
    yield widget
    QApplication.clipboard().clear()


def test_search_offers_tempo_words_as_text_in_both_scripts():
    assert symbols_ui.text_suggestions("中速") == ["中速"]
    assert "渐慢" in symbols_ui.text_suggestions("渐") and "漸慢" in symbols_ui.text_suggestions("漸")
    assert symbols_ui.text_suggestions("zhongsu") == ["中速"]
    assert {"全弓", "上半弓", "下半弓", "弓尖", "弓根"} <= set(symbols_ui.text_suggestions("弓"))
    assert "15弦" in symbols_ui.text_suggestions("定弦") or "定弦 1–5" in symbols_ui.text_suggestions("定弦")
    # nothing matches: the query itself can still become text
    assert symbols_ui.text_suggestions("小快板", found=False) == ["小快板"]
    assert symbols_ui.text_suggestions('a"b', found=False) == []


def test_typing_a_tempo_word_and_enter_puts_it_on_the_note(window):
    window.notes.select_rows([0], 0)
    window.palette.search.setText("中速")
    assert window.palette.grid.results[0] == TEXT_PREFIX + "中速"
    window.palette_apply(window.palette.grid.results[0])
    assert window.project.score.notes[0].text == "中速"
    window.palette.search.setText("15弦")
    window.palette_apply(window.palette.grid.results[0])
    assert window.project.score.notes[0].text == "中速 15弦"          # added, nothing replaced
    window.undo()
    assert window.project.score.notes[0].text == "中速"


def test_full_favourites_explain_and_replace(window, monkeypatch):
    palette = window.palette
    palette.favorites = [s.id for s in symbols_ui.palette_symbols()][:MAX_FAVORITES]
    new = next(s.id for s in symbols_ui.palette_symbols() if s.id not in palette.favorites)
    asked = []
    monkeypatch.setattr(palette, "choose_replacement", lambda sid, position=None: asked.append(sid))
    palette.toggle_favorite(new)
    assert asked == [new] and new not in palette.favorites           # asked which one, not silent
    old = palette.favorites[3]
    palette.toggle_favorite(new, replace=old)
    assert palette.favorites[3] == new and old not in palette.favorites and len(palette.favorites) == MAX_FAVORITES


def test_a_dot_applies_to_the_next_note_only(window):
    window.input_mode.setCurrentIndex(window.input_mode.findData("insert"))
    window.insert_position.setCurrentIndex(window.insert_position.findData("end"))
    window.note_command("duration_4")
    window.note_command("dot")
    assert window.next_note.dotted
    window.input_pitch(5)
    window.input_pitch(6)
    notes = window.project.score.notes
    assert notes[-2].dotted and notes[-2].degree == 5 and not notes[-1].dotted and notes[-1].degree == 6


def test_pasting_whole_bars_on_selected_bars_asks_replace_or_insert(window, monkeypatch):
    window.notes.select_bars([0])
    assert window.copy_notes()
    window.notes.select_bars([2])
    answers = iter(["replace"])
    monkeypatch.setattr(window, "choose_rhythm_option", lambda refused: next(answers))
    before = len(window.project.score.notes)
    window.paste_notes()
    notes = window.project.score.notes
    assert len(notes) == before and [n.degree for n in notes[8:12]] == [n.degree for n in notes[0:4]]
    window.undo()
    window.notes.select_bars([2])
    monkeypatch.setattr(window, "choose_rhythm_option", lambda refused: "insert")
    window.paste_notes()
    assert len(window.project.score.notes) == before + 4


def test_undo_back_to_the_saved_state_is_not_modified(window, tmp_path):
    path = tmp_path / "a.jps"
    save_project(path, window.project)
    window.reset(Project.from_dict(window.project.to_dict()), path)
    assert not window.dirty
    window.input_mode.setCurrentIndex(window.input_mode.findData("edit"))
    window.notes.select_rows([0], 0)
    window.input_pitch(3)
    assert window.dirty
    window.undo()
    assert not window.dirty
    window.redo()
    assert window.dirty


def test_recovered_work_keeps_its_file(window, tmp_path, monkeypatch):
    path = tmp_path / "曲 1.jps"
    save_project(path, window.project)
    window.reset(Project.from_dict(window.project.to_dict()), path)
    window.input_mode.setCurrentIndex(window.input_mode.findData("edit"))
    window.notes.select_rows([0], 0)
    window.input_pitch(3)
    window.write_recovery()                     # the autosave after an edit, then a crash
    assert window.recovery_path.with_suffix(".path").read_text(encoding="utf-8") == str(path)
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    assert window.restore_recovery()
    assert window.path == path and window.dirty and window.project.score.notes[0].degree == 3


def test_loading_a_score_puts_the_keyboard_on_the_canvas(window):
    window.title_field.setFocus()
    window.reset(Project(Score(notes=[Note(1)] * 4)))
    QApplication.processEvents()
    assert QApplication.focusWidget() is window.notes or window.notes.hasFocus()


def test_key_labels_put_the_accidental_first(window):
    assert key_label("Bb") == "♭B" and key_label("F#") == "♯F" and key_label("D") == "D"
    window.reset(Project(Score(key="Bb", notes=[Note(1)] * 4)))
    assert window.key_field.currentText() == "♭B" and window.key_field.currentData() == "Bb"
    window.key_field.setCurrentIndex(window.key_field.findData("F#"))
    window.capture_fields()
    assert window.project.score.key == "F#"


def test_an_unfinished_jump_is_listed_with_a_one_click_counterpart(window):
    window.notes.select_bars([1])
    window.palette_apply("nav.segno")
    score = window.project.score
    assert score.edit_error() is None and "還需要 D.S." in score.notation_error()
    assert window.fill_button.isVisibleTo(window) and "D.S." in window.fill_button.text()
    assert any(text == "尚未完成" for text in window.notes.problems.values())
    window.fix_problem()
    assert window.project.score.notation_error() is None
    assert "nav.ds" in window.project.score.notes[-1].bar_end


@pytest.mark.parametrize("size", [(1380, 850), (1000, 630)])
@pytest.mark.parametrize("language", ["zh_TW", "zh_CN"])
def test_no_palette_name_is_clipped(window, size, language):
    from jianpu_studio import i18n
    from jianpu_studio.palette import LABEL_MIN_PX
    previous = i18n.current_language()
    i18n.set_language(language)
    try:
        window.resize(*size)
        window.palette.set_instrument("all", save=False)
        QApplication.processEvents()
        grid = window.palette.grid
        grid.relayout()
        cells = [e for e in grid.entries if e.kind in ("cell", "chip")]
        assert len(cells) > 100
        clipped = [e.text for e in cells if grid.label_fit(e)[0] < LABEL_MIN_PX]
        assert clipped == []
    finally:
        i18n.set_language(previous)


def unfinished_score():
    from dataclasses import replace
    notes = [Note(k % 7 + 1, duration=1) for k in range(8)]
    notes[1] = replace(notes[1], bar_start=("bar.repeat_start",))
    notes[2] = replace(notes[2], bar_start=("nav.segno",))
    notes[4] = replace(notes[4], bar_end=("nav.tocoda",))
    return Score(key="D", notes=notes)


def test_an_unfinished_structure_exports_as_a_draft_of_plain_signs():
    score = unfinished_score()
    assert score.notation_error() and score.edit_error() is None
    with pytest.raises(ValueError):
        score.to_jianpu()
    text = score.to_jianpu(draft=True)
    assert '\\bar ".|:" 2' in text and "\\jsSegnoSign 3" in text and "\\jsToCodaText" in text
    assert "R{" not in text and "Segno" not in text.replace("jsSegnoSign", "") and "ToCoda " not in text


@pytest.mark.integration
def test_draft_print_renders_without_warnings_and_plays_as_written(tmp_path):
    import threading

    from jianpu_studio import smf
    from jianpu_studio.engine import find_tool, render
    lilypond = find_tool("lilypond")
    if not lilypond:
        pytest.skip("JIANPU_LILYPOND not configured")
    from dataclasses import replace
    score = unfinished_score()
    notes = score.parts[0].notes
    notes[5] = replace(notes[5], bar_start=("volta.1",))
    notes[6] = replace(notes[6], bar_start=("volta.2",), bar_end=("volta.end",))
    result = render(Project(score).notation(draft=True), tmp_path.resolve(), lilypond.resolve(), threading.Event())
    assert [line for line in result.log.splitlines() if "warning" in line.lower()] == []
    assert len(smf.read(result.midi.read_bytes())[0]) == 8                  # no repeat, no jump


def test_print_asks_before_a_draft_and_goes_to_the_problem_on_no(window, monkeypatch):
    window.reset(Project(unfinished_score()))
    answers = []
    monkeypatch.setattr(window, "confirm_draft", lambda problem: answers.append(str(problem)) or False)
    assert window.draft_notation(True) is None and answers and "尚未完成" in answers[0]
    monkeypatch.setattr(window, "confirm_draft", lambda problem: True)
    assert "\\jsSegnoSign" in window.draft_notation(True)


def test_canvas_header_line_shows_first_note_tempo_words_like_the_print(window):
    window.reset(Project(Score(key="Bb", notes=[Note(1, text="中速 稍自由")] + [Note(2)] * 7)))
    QApplication.processEvents()
    meta = [block[1] for block in window.notes.header_blocks if "1 =" in block[1]][0]
    assert "♭B" in meta and "中速 稍自由" in meta
    assert window.notes.part_labels[0][0] is None                         # not also above the note
    window.reset(Project(Score(notes=[Note(1, text="主音")] + [Note(2)] * 7)))
    QApplication.processEvents()
    assert window.notes.part_labels[0][0] is not None                     # a label stays on its note


def test_searching_huanba_leads_to_the_position_marks():
    assert [s.id for s in symbols_ui.search("換把")][:5] == ["pos.1", "pos.2", "pos.3", "pos.4", "pos.5"]
