from dataclasses import replace

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtWidgets import QAbstractItemView

from jianpu_studio.model import TICKS_PER_QUARTER, Note, Score
from jianpu_studio.score_view import NoteList, ScoreModel


def test_model_detects_same_score_mutations():
    model = ScoreModel()
    score = Score(notes=[Note(1), Note(2)])
    assert model.set_score(score)
    score.notes[0] = Note(3, duration=2)
    assert model.set_score(score)
    assert model.positions == [0, 2*TICKS_PER_QUARTER]
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


def measure_point(view, number):
    frame = next(frame for frame, bar, _ in view.bar_rects if bar == number)
    return frame.topLeft() + QPoint(max(1, int(4*view.scale)), max(1, int(6*view.scale))) - QPoint(
        view.horizontalOffset(), view.verticalOffset())


def test_score_header_wraps_plain_metadata_and_reflows_without_note_reset(qtbot):
    view = NoteList()
    qtbot.addWidget(view)
    view.resize(420, 480)
    score = Score(title="短曲名", composer="來源", notes=[Note(1)]*4)
    view.set_score(score)
    view.show()
    qtbot.waitExposed(view)
    original_header_bottom = view.header_rect.bottom()
    score.title = "<b>長曲名與字面標記</b>"*8
    score.composer = "LongUnbrokenComposerText"*7
    score.key, score.tempo = "D", 120
    assert not view.set_score(score)
    assert view.header_rect.bottom() > original_header_bottom
    assert view.bar_rects[0][0].top() > view.header_rect.bottom()
    assert [block[1] for block in view.header_blocks] == [score.title, score.composer, "1 = D    4/4    ♩ = 120"]
    for rect, *_ in view.header_blocks:
        assert view.header_rect.contains(rect)
    assert view.measure_at(view.header_rect.center()) is None
    assert not view.indexAt(view.header_rect.center()).isValid()
    view.scrollTo(view.model().index(3, 0))
    assert view.indexAt(view.visualRect(view.model().index(3, 0)).center()).row() == 3


def test_continuous_measures_use_compact_rows_and_expand_for_erhu_marks(qtbot):
    view = NoteList()
    qtbot.addWidget(view)
    view.resize(1000, 700)
    score = Score(notes=[Note(1)]*16, bars_per_line=4)
    view.set_score(score)
    plain_height = view.line_rects[0].height()
    assert plain_height < 100
    frames = [frame for frame, _, _ in view.bar_rects]
    assert all(a.right()+1 == b.left() for a, b in zip(frames, frames[1:]))
    score.notes[0] = replace(score.notes[0], finger=0, bow="downbow", slur_start=True, dynamic="mp")
    score.notes[3] = replace(score.notes[3], slur_end=True)
    view.set_score(score)
    assert view.line_rects[0].height() > plain_height
    assert view.line_links[0]
    assert view.line_rects[0].contains(view.line_links[0][0].boundingRect().toAlignedRect())


def test_measure_number_and_whitespace_select_full_bars_with_modifiers(qtbot):
    view = NoteList()
    qtbot.addWidget(view)
    view.resize(700, 620)
    view.set_score(Score(notes=[Note(1)]*24, bars_per_line=2))
    view.show()
    qtbot.waitExposed(view)
    clicked = []
    view.measureClicked.connect(clicked.append)
    point = measure_point(view, 0)
    assert not view.indexAt(point).isValid()
    assert view.measure_at(point) == 0
    qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=point)
    assert view.selected_rows() == list(range(4))
    qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier,
                     measure_point(view, 2))
    assert view.selected_rows() == list(range(4))+list(range(8, 12))
    # Shift selection includes both complete endpoint measures, backwards too.
    qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier,
                     measure_point(view, 0))
    assert view.selected_rows() == list(range(12))
    qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier,
                     measure_point(view, 4))
    assert view.selected_rows() == list(range(8, 20))
    assert clicked == [0, 2, 0, 4]


def test_measure_context_finds_blank_area_and_preserves_existing_range(qtbot):
    view = NoteList()
    qtbot.addWidget(view)
    view.resize(900, 500)
    view.set_score(Score(notes=[Note(1)]*12))
    view.show()
    qtbot.waitExposed(view)
    view.select_rows(range(8))
    point = measure_point(view, 1)
    qtbot.mouseClick(view.viewport(), Qt.MouseButton.RightButton, pos=point)
    assert view.selected_rows() == list(range(8))
    requests = []
    view.measureContextRequested.connect(lambda number, global_pos: requests.append((number, global_pos)))
    event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, point, view.viewport().mapToGlobal(point))
    view.contextMenuEvent(event)
    assert requests == [(1, view.viewport().mapToGlobal(point))]
    assert event.isAccepted()


def test_empty_score_add_measure_and_measure_shortcuts(qtbot):
    view = NoteList()
    qtbot.addWidget(view)
    view.resize(500, 400)
    view.set_score(Score())
    view.show()
    qtbot.waitExposed(view)
    commands = []
    view.command.connect(commands.append)
    qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=view.empty_button_rect.center())
    ctrl = Qt.KeyboardModifier.ControlModifier
    for key, modifier in ((Qt.Key.Key_Insert, Qt.KeyboardModifier.NoModifier),
                          (Qt.Key.Key_Insert, ctrl), (Qt.Key.Key_B, ctrl),
                          (Qt.Key.Key_Delete, ctrl), (Qt.Key.Key_Backspace, ctrl)):
        qtbot.keyClick(view, key, modifier)
    assert commands == ["append_bar", "insert_bar_before", "insert_bars", "append_bar", "delete_bars", "delete_bars"]


@pytest.mark.parametrize("zoom", [75, 150, 200])
def test_header_and_measure_hit_testing_survive_zoom_and_scrolling(qtbot, zoom):
    view = NoteList()
    qtbot.addWidget(view)
    view.resize(460, 360)
    view.set_score(Score(notes=[Note(1)]*80))
    view.set_zoom(zoom)
    view.show()
    qtbot.waitExposed(view)
    last = view.model().index(79, 0)
    view.scrollTo(last)
    assert view.indexAt(view.visualRect(last).center()) == last
    assert view.measure_at(view.visualRect(last).center()) == 19
    assert view.measure_at(QPoint(-10, -10)) is None


def triplet_measure():
    return [Note(1, duration=8, tuplet=3, tuplet_start=True),
            Note(2, duration=8, tuplet=3), Note(3, duration=8, tuplet=3, tuplet_end=True),
            Note(4, duration=2), Note(5)]


def test_triplets_keep_exact_bar_geometry_and_accessible_marks(qtbot):
    view = NoteList()
    qtbot.addWidget(view)
    notes = triplet_measure()
    notes[0] = replace(notes[0], accidental="n", slur_start=True, finger=2, bow="downbow")
    notes[2] = replace(notes[2], slur_end=True)
    view.set_score(Score(notes=notes))
    model = view.score_model
    assert model.positions == [0, TICKS_PER_QUARTER//3, 2*TICKS_PER_QUARTER//3,
                               TICKS_PER_QUARTER, 3*TICKS_PER_QUARTER]
    assert model.total_ticks == 4*TICKS_PER_QUARTER
    assert model.bars == [(0, list(range(5)))]
    assert model.tuplets == [(0, 2, 3)]
    assert len(view.line_tuplets[0]) == 1
    bracket, label, text = view.line_tuplets[0][0]
    assert text == "3"
    assert view.line_rects[0].contains(bracket.boundingRect().toAlignedRect())
    assert view.line_rects[0].contains(label)
    assert label.bottom() < view.line_links[0][0].boundingRect().top()
    description = model.data(model.index(0, 0), Qt.ItemDataRole.AccessibleTextRole)
    assert "三連音" in description and "還原記號" in description


@pytest.mark.parametrize("direction", ["crescendo", "diminuendo"])
def test_hairpin_continues_across_systems_and_clears_note_marks(qtbot, direction):
    view = NoteList()
    qtbot.addWidget(view)
    view.resize(850, 600)
    notes = triplet_measure()*3
    notes[0] = replace(notes[0], hairpin=direction, dynamic="ppp")
    notes[-1] = replace(notes[-1], hairpin_end=True, dynamic="fff", octave=-2)
    view.set_score(Score(notes=notes, bars_per_line=1))
    assert view.score_model.hairpins == [(0, 14, direction)]
    assert len(view.line_hairpins) == 3
    openings = []
    for line, paths in enumerate(view.line_hairpins):
        assert len(paths) == 1
        path = paths[0]
        assert view.line_rects[line].contains(path.boundingRect().toAlignedRect())
        openings.append((path.elementAt(2).y-path.elementAt(0).y,
                         path.elementAt(3).y-path.elementAt(1).y))
    assert openings[0][1] == pytest.approx(openings[1][0])
    assert openings[1][1] == pytest.approx(openings[2][0])
    assert openings[0][0] == pytest.approx(0 if direction == "crescendo" else 12)
    assert openings[-1][1] == pytest.approx(12 if direction == "crescendo" else 0)
    # Dynamics have their own lane above the wedge, including lower octave dots.
    for line in (0, 2):
        assert view.line_hairpins[line][0].boundingRect().top() > (
            view.line_baselines[line]+view.line_dynamic_offsets[line]+22*view.scale)


def test_adjacent_hairpins_end_before_the_next_span_starts(qtbot):
    view = NoteList()
    qtbot.addWidget(view)
    notes = [Note(1, hairpin="crescendo"), Note(2),
             Note(3, hairpin_end=True, hairpin="diminuendo"), Note(4, hairpin_end=True)]
    view.set_score(Score(notes=notes))
    assert view.score_model.hairpins == [(0, 2, "crescendo"), (2, 3, "diminuendo")]
    assert len(view.line_hairpins[0]) == 2


def test_grace_groups_reserve_head_space_and_keep_one_note_hit_target(qtbot):
    view = NoteList()
    qtbot.addWidget(view)
    view.resize(850, 500)
    notes = [Note(1, duration=2, grace_before="#4'5", grace_after="2,,3"), Note(2, duration=2)]
    view.set_score(Score(notes=notes))
    view.show()
    qtbot.waitExposed(view)
    assert view.score_model.total_ticks == 4*TICKS_PER_QUARTER
    assert view.note_graces[0] == ((("#", "4", 1), ("", "5", 0)), (("", "2", -2), ("", "3", 0)))
    assert view.note_main_width(0) < view.rects[0].width()
    assert view.note_center(0) > view.rects[0].left()+44*view.scale
    assert view.long_beats(notes[0]) == 2
    index = view.model().index(0, 0)
    qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=view.visualRect(index).center())
    assert view.selected_rows() == [0]
    description = view.score_model.data(index, Qt.ItemDataRole.AccessibleTextRole)
    assert "前倚音 #4'5" in description and "後倚音 2,,3" in description
    view.viewport().grab()


def test_all_common_articulations_render_with_selection_and_tooltips(qtbot, monkeypatch):
    from jianpu_studio.score_view import ARTICULATION_LABELS

    view = NoteList()
    qtbot.addWidget(view)
    view.resize(1100, 800)
    view.set_score(Score(notes=[Note(i%7+1, articulation=mark) for i, mark in enumerate(ARTICULATION_LABELS)]))
    view.select_rows([0, 4, 10])
    painted = []
    original = view._draw_articulation

    def record(painter, mark, *args):
        painted.append(mark)
        original(painter, mark, *args)

    monkeypatch.setattr(view, "_draw_articulation", record)
    view.show()
    qtbot.waitExposed(view)
    view.viewport().grab()
    assert set(painted) == set(ARTICULATION_LABELS)
    assert view.selected_rows() == [0, 4, 10]
    for row, (mark, label) in enumerate(ARTICULATION_LABELS.items()):
        assert label in view.model().index(row, 0).data(Qt.ItemDataRole.ToolTipRole)


def test_long_notated_score_draws_only_visible_cached_system_marks(qtbot, monkeypatch):
    view = NoteList()
    qtbot.addWidget(view)
    view.resize(900, 500)
    notes = triplet_measure()*2000
    notes[0] = replace(notes[0], hairpin="crescendo")
    notes[-1] = replace(notes[-1], hairpin_end=True)
    view.set_score(Score(notes=notes))
    assert len(view.line_rects) > 500
    painted = []
    original = view._draw_system_marks

    def record(painter, line):
        painted.append(line)
        original(painter, line)

    monkeypatch.setattr(view, "_draw_system_marks", record)
    view.show()
    qtbot.waitExposed(view)
    last = view.model().index(9999, 0)
    view.scrollTo(last)
    painted.clear()
    view.viewport().grab()
    assert 0 < len(painted) < 10
    assert view.indexAt(view.visualRect(last).center()) == last
