from dataclasses import replace

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QAbstractItemView

from jianpu_studio.model import Note, Score
from jianpu_studio.score_view import NoteList, ScoreModel


def test_model_detects_same_score_mutations():
    model = ScoreModel()
    score = Score(notes=[Note(1), Note(2)])
    assert model.set_score(score)
    score.notes[0] = Note(3, duration=2)
    assert model.set_score(score)
    assert model.positions == [0, 32]
    score.notes.append(Note(4))
    assert model.set_score(score)
    assert model.rowCount() == 3
    score.meter = "2/4"
    assert model.set_score(score)
    assert [bar for bar, _ in model.bars] == [0, 1]
    assert not model.set_score(score)


def test_accessibility_includes_erhu_marks():
    model = ScoreModel()
    model.set_score(Score(notes=[Note(1, finger=0, bow="downbow", dynamic="mf", slide="up", slur_start=True)]))
    description = model.data(model.index(0, 0), Qt.ItemDataRole.AccessibleTextRole)
    for label in ("空弦", "拉弓", "力度 mf", "上滑音", "連弓起點"):
        assert label in description


def test_long_score_geometry_and_hit_testing_are_scroll_aware(qtbot):
    view = NoteList()
    qtbot.addWidget(view)
    view.resize(900, 500)
    score = Score(notes=[Note(1)] * 10000)
    view.set_score(score)
    view.show()
    qtbot.waitExposed(view)
    assert view.model().rowCount() == 10000
    assert len(list(view.visible_bars())) < 30
    last = view.model().index(9999, 0)
    view.setCurrentRow(9999)
    view.scrollTo(last, QAbstractItemView.ScrollHint.PositionAtBottom)
    assert view.indexAt(view.visualRect(last).center()) == last
    assert view.verticalScrollBar().value() > 0
    assert len(list(view.visible_bars())) < 30
    view.set_zoom(150)
    assert view.indexAt(view.visualRect(last).center()) == last
    assert not view.indexAt(QPoint(-10, -10)).isValid()


def test_layout_options_reflow_and_links_continue_across_lines(qtbot):
    view = NoteList()
    qtbot.addWidget(view)
    view.resize(900, 500)
    notes = [Note(1)] * 12
    notes[0] = replace(notes[0], slur_start=True)
    notes[-1] = replace(notes[-1], slur_end=True)
    score = Score(notes=notes, bars_per_line=1)
    view.set_score(score)
    assert len(view.line_rects) == 3
    assert all(len(paths) == 1 for paths in view.line_links)
    old_height = view.content_height
    score.staff_size = 28
    assert not view.set_score(score)
    assert view.content_height > old_height
    score.bars_per_line = 3
    view.set_score(score)
    assert len(view.line_rects) == 1


def test_selection_ranges_stay_compact(qtbot):
    view = NoteList()
    qtbot.addWidget(view)
    view.set_score(Score(notes=[Note(1)] * 10000))
    view.select_rows(range(10000), current=5000)
    assert len(view.selectionModel().selection()) == 1
    assert view.currentRow() == 5000
    view.select_rows([0, 1, 8, 9, 10], current=8)
    assert len(view.selectionModel().selection()) == 2
    assert view.selected_rows() == [0, 1, 8, 9, 10]


def test_paint_only_visits_visible_note_marks(qtbot, monkeypatch):
    view = NoteList()
    qtbot.addWidget(view)
    view.resize(900, 500)
    view.set_score(Score(notes=[Note(1)] * 10000))
    painted = []
    draw_marks = view._draw_marks

    def record(*args):
        painted.append(1)
        draw_marks(*args)

    monkeypatch.setattr(view, "_draw_marks", record)
    view.show()
    qtbot.waitExposed(view)
    painted.clear()
    view.viewport().grab()
    assert 0 < len(painted) < 100
