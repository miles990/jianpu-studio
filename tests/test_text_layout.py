"""Review 2 #13: free text above notes must stay readable (no two labels overlap on the canvas).

The labels are measured where they are really painted: ``QPainter.drawText`` is recorded during
a real paint of the offscreen view, and each label's ink box comes from the painter's font.
"""

import itertools

import pytest
from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QFontMetricsF, QImage, QPainter

from jianpu_studio import score_paint
from jianpu_studio.model import Note, Part, Score
from jianpu_studio.score_view import NoteList as ScoreView

LABELS = ["中速稍慢 深情而略帶憂傷地", "漸慢並逐漸減弱 回到原速", "稍快 活潑而輕巧地", "突強後立即轉弱"]


class RecordingPainter(QPainter):
    drawn = []

    def drawText(self, *args):
        if len(args) == 2 and isinstance(args[0], QPointF) and isinstance(args[1], str):
            metrics = QFontMetricsF(self.font())
            point, text = args
            box = QRectF(point.x(), point.y() - metrics.ascent(), metrics.horizontalAdvance(text), metrics.height())
            RecordingPainter.drawn.append((text, box))
        return super().drawText(*args)


def painted_labels(view, monkeypatch, texts):
    RecordingPainter.drawn = []
    monkeypatch.setattr(score_paint, "QPainter", RecordingPainter)
    view.viewport().repaint()
    if not RecordingPainter.drawn:      # offscreen viewport may not paint synchronously
        image = QImage(view.viewport().size(), QImage.Format.Format_ARGB32)
        view.viewport().render(image)
    return [(t, box) for t, box in RecordingPainter.drawn if t in texts]


def make_view(qtbot, score, width):
    view = ScoreView()
    qtbot.addWidget(view)
    view.resize(width, 700)
    view.set_score(score, 0)
    view.show()
    view.relayout()
    return view


@pytest.mark.parametrize("width", [1200, 760, 480])
def test_adjacent_note_texts_never_overlap(qtbot, monkeypatch, width):
    score = Score(notes=[Note(i + 1, text=label) for i, label in enumerate(LABELS)] + [Note(5, duration=1)])
    view = make_view(qtbot, score, width)
    labels = painted_labels(view, monkeypatch, set(LABELS))
    assert sorted(t for t, _ in labels) == sorted(LABELS)
    for (a, box_a), (b, box_b) in itertools.combinations(labels, 2):
        assert not box_a.intersects(box_b), (a, box_a, b, box_b)
    # The labels stay above the notes, below the bar-number strip and inside the paper.
    row = view.systems[0].rows[0]
    for _, box in labels:
        assert box.bottom() < row.baseline - 20 * view.scale
        assert box.top() >= row.top + 15 * view.scale
        assert view.paper_rect.left() <= box.left() and box.right() <= view.paper_rect.right()
    # Notes stay selectable where they are drawn: a click on each note's centre hits that note.
    for r in range(len(score.notes)):
        rect = view.rects[r]
        point = rect.center() - QPointF(view.horizontalOffset(), view.verticalOffset()).toPoint()
        assert view.indexAt(point).row() == r


def test_short_labels_on_spread_notes_share_one_line_and_spacing_is_unchanged(qtbot, monkeypatch):
    plain = Score(notes=[Note(1), Note(2), Note(3), Note(4)] * 2)
    labelled = Score(notes=[Note(1, text="慢"), Note(2), Note(3, text="快"), Note(4)] + [Note(1), Note(2), Note(3), Note(4)])
    a, b = make_view(qtbot, plain, 1200), make_view(qtbot, labelled, 1200)
    # Labels never change the horizontal note spacing.
    assert [r.left() for r in a.rects] == [r.left() for r in b.rects]
    labels = painted_labels(b, monkeypatch, {"慢", "快"})
    assert len(labels) == 2 and labels[0][1].top() == labels[1][1].top()


def test_label_rows_in_two_parts_do_not_collide_with_the_other_part(qtbot, monkeypatch):
    score = Score(parts=[Part(notes=[Note(i + 1, text=label) for i, label in enumerate(LABELS)]),
                         Part(notes=[Note(5, text=label) for label in LABELS])])
    view = make_view(qtbot, score, 1000)
    labels = painted_labels(view, monkeypatch, set(LABELS))
    assert len(labels) == 8
    for (a, box_a), (b, box_b) in itertools.combinations(labels, 2):
        assert not box_a.intersects(box_b), (a, box_a, b, box_b)
    system = view.systems[0]
    upper, lower = system.rows[0], system.rows[1]
    assert all(box.bottom() < upper.baseline or box.top() > upper.bottom for _, box in labels)
    assert lower.top >= upper.bottom
