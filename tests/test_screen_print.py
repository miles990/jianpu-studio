"""Group B/C (round 4): the canvas and the print make the same engraving decisions.

Accidentals, underline groups, long rests and jump texts come from one function
(``engraving.py``) that the canvas paints and the print path mirrors; the real-PDF tests
check the print side with LilyPond itself.
"""
import pytest
from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtPdf import QPdfDocument

from jianpu_studio import engraving
from jianpu_studio.engine import find_tool, render
from jianpu_studio.model import Note, Part, Project, Score
from jianpu_studio.notation_rules import bar_items
from jianpu_studio.score_view import NoteList as ScoreView


@pytest.fixture
def lilypond():
    tool = find_tool("lilypond")
    if not tool:
        pytest.skip("JIANPU_LILYPOND not configured")
    return tool.resolve()


def pdf_text(path):
    data = QByteArray(path.read_bytes())
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    document = QPdfDocument(None)
    document.load(buffer)
    text = " ".join(document.getAllText(page).text() for page in range(document.pageCount()))
    document.close()
    return text


# ---------------------------------------------------------------- accidentals
def test_accidentals_follow_lilypond_rules_per_bar_and_octave():
    """Verified against a real render (work/round4/r/acc.png): #4 ♮4 #4 4 | 4 #4' 4 ♮4' | ♭7~7 ♮7."""
    notes = [Note(4, accidental="#"), Note(4), Note(4, accidental="#"), Note(4, accidental="#"),
             Note(4), Note(4, octave=1, accidental="#"), Note(4), Note(4, octave=1),
             Note(7, accidental="b", duration=2, tie=True), Note(7, accidental="b"), Note(7)]
    score = Score(notes=notes)
    assert engraving.shown_accidentals(score, 0) == ["#", "n", "#", "", "", "#", "", "n", "b", "", "n"]


def test_a_redundant_stored_natural_is_not_shown_because_the_print_cannot_show_it():
    score = Score(notes=[Note(4, accidental="n"), Note(5), Note(6), Note(7)])
    assert Note(4, accidental="n").token() == "c4"            # upstream has no cautionary natural
    assert engraving.shown_accidentals(score, 0) == ["", "", "", ""]


def test_canvas_paints_the_shared_accidentals(qtbot):
    view = ScoreView()
    qtbot.addWidget(view)
    view.resize(1200, 600)
    score = Score(notes=[Note(4, accidental="#"), Note(4), Note(5), Note(6)])
    view.set_score(score, 0)
    view.relayout()
    assert view.part_accidentals[0] == ["#", "n", "", ""]


# ---------------------------------------------------------------- underlines, rests, layout
def test_dotted_eighth_and_sixteenth_share_one_underline_group():
    score = Score(notes=[Note(1, duration=8, dotted=True), Note(2, duration=16), Note(3, duration=8), Note(4, duration=8),
                         Note(5, duration=2)])
    assert engraving.beam_runs(score, 0, 0, 5) == [(0, [0, 1]), (0, [2, 3]), (1, [1])]


def test_long_rests_are_written_one_zero_per_beat_on_screen_and_in_print(qtbot):
    assert Note(0, duration=1).token() == "0 0 0 0"
    assert Note(0, duration=2, dotted=True).token() == "0 0 0"
    assert Note(5, duration=1).token() == "5 - - -"
    view = ScoreView()
    qtbot.addWidget(view)
    view.resize(1200, 600)
    view.set_score(Score(notes=[Note(0, duration=1)]), 0)
    view.relayout()
    xs = [view.note_center(0)] + [view.beat_x(0, 0, beat) for beat in (1, 2, 3)]
    assert xs == sorted(xs) and len(set(round(x) for x in xs)) == 4


def test_notes_that_start_together_line_up_across_parts(qtbot):
    view = ScoreView()
    qtbot.addWidget(view)
    view.resize(1400, 700)
    upper = [Note(5), Note(3, duration=2), Note(2)]
    lower = [Note(7, duration=2), Note(5, duration=2)]
    view.set_score(Score(parts=[Part("一", upper), Part("二", lower)]), 0)
    view.relayout()
    assert abs(view.part_centers[0][0] - view.part_centers[1][0]) < 1
    # the lower part's dash on beat 2 sits where the upper part's half note starts
    assert abs(view.beat_x(1, 0, 1) - view.part_centers[0][1]) < 1
    assert abs(view.part_centers[0][2] - view.beat_x(1, 1, 1)) < 1


def test_a_short_last_line_keeps_its_natural_width(qtbot):
    view = ScoreView()
    qtbot.addWidget(view)
    view.resize(1200, 700)
    view.set_score(Score(notes=[Note(1)] * 4 * 9), 0)
    view.relayout()
    assert len(view.systems) >= 2
    full, last = view.systems[0].rect.width(), view.systems[-1].rect.width()
    assert last < full * 0.75


# ---------------------------------------------------------------- jumps and codas
def ds_al_coda():
    bar = lambda d, **k: [Note(d, **k)] + [Note(d) for _ in range(3)]       # noqa: E731
    notes = (bar(1) + [Note(2, bar_start=("nav.segno",)), Note(2), Note(2), Note(2)]
             + [Note(3), Note(3), Note(3), Note(3, bar_end=("nav.tocoda",))]
             + [Note(4), Note(4), Note(4), Note(4, bar_end=("nav.ds",))]
             + [Note(5, marks=("dyn.mf",)), Note(5, marks=("dyn.sfz",)), Note(5), Note(5)] + [Note(6, duration=1)])
    return Score(title="DS", key="D", notes=notes)


def dc_al_fine():
    notes = ([Note(1)] * 3 + [Note(1, bar_end=("nav.fine",))] + [Note(2)] * 3 + [Note(2, bar_end=("nav.dc",))])
    return Score(title="DC", key="D", notes=notes)


def test_jump_text_rule_is_shared():
    assert engraving.jump_text(bar_items(ds_al_coda())) == "D.S. al Coda"
    assert engraving.jump_text(bar_items(dc_al_fine())) == "D.C. al Fine"
    assert engraving.coda_start(bar_items(ds_al_coda())) == 4
    assert engraving.end_barline(bar_items(dc_al_fine()), 0, False) == "final"


@pytest.mark.parametrize("make, expected", [(ds_al_coda, "D.S. al Coda"), (dc_al_fine, "D.C. al Fine")])
def test_real_pdf_reads_the_same_jump_text_as_the_canvas(tmp_path, lilypond, make, expected):
    score = make()
    assert engraving.jump_text(bar_items(score)) == expected
    result = render(Project(score).notation(), tmp_path.resolve(), lilypond)
    text = " ".join(pdf_text(result.pdf).split())
    assert expected in text and "e poi" not in text
    if score.title == "DS":
        assert "To Coda" in text and "Coda" in text.split("D.S. al Coda")[-1] + text


def test_print_has_bar_numbers_only_at_line_starts_and_a_ragged_last_line(tmp_path, lilypond):
    result = render(Project(ds_al_coda()).notation(), tmp_path.resolve(), lilypond)
    ly = (tmp_path / "score.ly").read_text(encoding="utf-8")
    assert "begin-of-line-visible" in ly and "center-visible" not in ly
    assert "ragged-last = ##t" in ly and "extra-spacing-width" in ly
    assert result.pdf.is_file()


# ---------------------------------------------------------------- E: text above notes, lyrics
def test_note_text_is_stored_exported_safely_and_drawn(qtbot, tmp_path):
    from jianpu_studio.model import load_project, save_project
    note = Note(1, text="中速 深情地")
    assert note.notation_token() == 'c1 ^"中速 深情地"'
    assert Note(0, text='50% "x" {y}').notation_token() == 'c0 ^"50﹪ ”x” ﹛y﹜"'
    with pytest.raises(ValueError, match="一行"):
        Note(1, text="a\nb").validate()
    score = Score(notes=[note, Note(0, text="引子"), Note(3), Note(4)])
    save_project(tmp_path / "t.jps", Project(score))
    assert load_project(tmp_path / "t.jps").score.notes[:2] == score.notes[:2]
    view = ScoreView()
    qtbot.addWidget(view)
    view.resize(1200, 600)
    view.set_score(score, 0)
    view.relayout()
    row = view.systems[0].rows[0]
    assert row.text_y and row.text_y < row.baseline - 30


def test_lyrics_skip_rests_ties_and_slur_melismas_like_the_print():
    notes = [Note(1, duration=8), Note(2, duration=8), Note(3, spans_start=("line.slur",)), Note(5, spans_end=("line.slur",)),
             Note(6, tie=True), Note(6), Note(0), Note(5), Note(1)]
    # verified against a real render (work/round4/r/lyr.png)
    assert engraving.lyric_map(notes, "一閃一閃亮晶") == {0: "一", 1: "閃", 2: "一", 4: "閃", 7: "亮", 8: "晶"}


def test_editor_adds_text_above_a_note(qtbot, monkeypatch, tmp_path):
    from jianpu_studio.app import MainWindow
    monkeypatch.setattr(MainWindow, "render_score", lambda *a, **k: None)
    window = MainWindow(recovery=False)
    window.recovery_path = tmp_path / "r.jps"
    window.autosave.stop()
    qtbot.addWidget(window, before_close_func=lambda w: setattr(w, "dirty", False))
    window.reset(Project(Score(notes=[Note(1), Note(2), Note(3), Note(4)])))
    window.notes.select_rows([1], 1)
    monkeypatch.setattr(window, "ask_note_text", lambda current: "漸慢")
    assert window.edit_note_text()
    assert window.project.score.notes[1].text == "漸慢"
    window.undo()
    assert window.project.score.notes[1].text == ""
