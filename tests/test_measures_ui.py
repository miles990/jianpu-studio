"""User-facing measure operations and the continuous reading/editing workspace."""

import pytest
from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QPainter, QPdfWriter
from PySide6.QtWidgets import QApplication, QComboBox, QDialogButtonBox, QFileDialog, QScrollArea, QSpinBox

from jianpu_studio import app as app_module
from jianpu_studio.app import MainWindow
from jianpu_studio.engine import RenderResult
from jianpu_studio.model import Note, Project, Score


@pytest.fixture
def measure_window(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(MainWindow, "render_score", lambda *args, **kwargs: None)
    monkeypatch.setattr(MainWindow, "error", lambda self, title, error: pytest.fail(f"{title}: {error}"))
    window = MainWindow(recovery=False)
    window.recovery_path = tmp_path / "recovery.jps"
    window.autosave.stop()

    def before_close(widget):
        widget.dirty = False
        QApplication.clipboard().clear()

    qtbot.addWidget(window, before_close_func=before_close)
    window.reset(Project())
    window.show()
    window.activateWindow()
    window.notes.setFocus()
    QApplication.processEvents()
    return window


def load_score(window, notes, meter="4/4"):
    window.reset(Project(Score(meter=meter, notes=list(notes))))
    QApplication.processEvents()


def complete_bars(score):
    """Assert audible bar lengths independently of the editor's geometry."""
    bars, current, beats = [], [], 0
    for note in score.notes:
        current.append(note)
        beats += note.beats
        assert beats <= score.bar_length
        if beats == score.bar_length:
            bars.append(current)
            current, beats = [], 0
    assert not current, "operation left an incomplete measure"
    return bars


def click_bar(qtbot, window, number, modifier=Qt.KeyboardModifier.NoModifier):
    view = window.notes
    frame, _, _ = next(entry for entry in view.bar_rects if entry[1] == number)
    view.verticalScrollBar().setValue(max(0, frame.top() - 4))
    view.horizontalScrollBar().setValue(max(0, frame.left() - 4))
    QApplication.processEvents()
    point = frame.topLeft() + QPoint(max(1, int(4 * view.scale)), max(1, int(6 * view.scale)))
    point -= QPoint(view.horizontalOffset(), view.verticalOffset())
    assert view.viewport().rect().contains(point)
    qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, modifier, point)


def assert_undo_redo(window, before, selected_before, current_before):
    after = window.project.snapshot()
    selected_after, current_after = window.notes.selected_rows(), window.notes.currentRow()
    window.undo()
    assert window.project == before
    assert window.notes.selected_rows() == selected_before
    assert window.notes.currentRow() == current_before
    window.redo()
    assert window.project == after
    assert window.notes.selected_rows() == selected_after
    assert window.notes.currentRow() == current_after


@pytest.mark.parametrize("position, inserted_at", [("before", 1), ("after", 2), ("end", 3)])
def test_add_multiple_bars_at_requested_position(measure_window, qtbot, position, inserted_at):
    window = measure_window
    original = [[Note(degree)] * 3 for degree in (1, 3, 5)]
    load_score(window, sum(original, []), meter="3/4")
    click_bar(qtbot, window, 1)
    before = window.project.snapshot()
    selected, current = window.notes.selected_rows(), window.notes.currentRow()
    if position == "end":
        def accept_dialog():
            dialog = QApplication.activeModalWidget()
            try:
                dialog.findChild(QSpinBox, "measureCount").setValue(2)
                location = dialog.findChild(QComboBox, "measurePosition")
                location.setCurrentIndex(location.findData("end"))
                buttons = dialog.findChild(QDialogButtonBox)
                qtbot.mouseClick(buttons.button(QDialogButtonBox.StandardButton.Ok), Qt.MouseButton.LeftButton)
            except Exception:
                dialog.reject()
                raise
        QTimer.singleShot(0, accept_dialog)
        window.insert_bars_dialog()
    else:
        window.insert_bars(position=position, count=2)
    bars = complete_bars(window.project.score)
    assert len(bars) == 5
    assert bars[:inserted_at] == original[:inserted_at]
    assert bars[inserted_at + 2:] == original[inserted_at:]
    assert all(note.degree == 0 for bar in bars[inserted_at:inserted_at + 2] for note in bar)
    assert window.input_mode.currentData() == "fill"
    assert window.notes.selected_rows() == [sum(map(len, bars[:inserted_at]))]
    assert_undo_redo(window, before, selected, current)


@pytest.mark.parametrize("initial", [[], [Note(5)]], ids=["empty-score", "incomplete-tail"])
def test_append_bars_handles_empty_score_and_incomplete_tail(measure_window, qtbot, initial):
    window = measure_window
    load_score(window, initial)
    if initial:
        window.insert_bars(position="end", count=2)
    else:
        point = window.notes.empty_button_rect.center()
        point -= QPoint(window.notes.horizontalOffset(), window.notes.verticalOffset())
        qtbot.mouseClick(window.notes.viewport(), Qt.MouseButton.LeftButton, pos=point)
        qtbot.keyClick(window.notes, Qt.Key.Key_B, Qt.KeyboardModifier.ControlModifier)
        click_bar(qtbot, window, 0)
        window.input_mode.setCurrentIndex(window.input_mode.findData("edit"))
        before, history_count = window.project.snapshot(), len(window.history)
        window.measure_action("clear")
        assert window.input_mode.currentData() == "fill"
        qtbot.keyClicks(window.notes, "q0")
        assert window.project == before
        assert len(window.history) == history_count
        assert window.notes.currentRow() == 1, "keeping the same rest should still advance input"
    bars = complete_bars(window.project.score)
    assert len(bars) == 2 + bool(initial)
    assert all(note.degree == 0 for bar in bars[-2:] for note in bar)
    if initial:
        assert bars[0][0] == initial[0]
        assert all(note.degree == 0 for note in bars[0][1:])
    assert window.input_mode.currentData() == "fill"


@pytest.mark.parametrize("action", ["clear", "delete"])
def test_clear_keeps_time_while_delete_removes_time(measure_window, qtbot, action):
    window = measure_window
    original = [[Note(degree, duration=8)] * 6 for degree in (1, 3, 5)]
    load_score(window, sum(original, []), meter="6/8")
    click_bar(qtbot, window, 1)
    before = window.project.snapshot()
    selected, current = window.notes.selected_rows(), window.notes.currentRow()
    window.measure_action(action)
    bars = complete_bars(window.project.score)
    if action == "clear":
        assert len(bars) == 3
        assert all(note.degree == 0 for note in bars[1])
        assert sum(note.beats for bar in bars[:2] for note in bar) == 6
        assert window.input_mode.currentData() == "fill"
    else:
        assert len(bars) == 2
        assert sum(note.beats for note in bars[0]) == 3
    assert bars[0] == original[0]
    assert bars[-1] == original[-1]
    assert_undo_redo(window, before, selected, current)


def test_duplicate_measure_range_preserves_rhythm_and_erhu_marks(measure_window, qtbot):
    window = measure_window
    first = [Note(1, finger=0, bow="downbow", slur_start=True),
             Note(2, octave=1, duration=8, dynamic="mf"),
             Note(3, duration=8, slur_end=True), Note(5, duration=2)]
    second, third = [Note(6, duration=1, bow="upbow")], [Note(3, duration=2), Note(2, duration=2)]
    load_score(window, first + second + third)
    click_bar(qtbot, window, 0)
    click_bar(qtbot, window, 1, Qt.KeyboardModifier.ShiftModifier)
    assert window.notes.selected_rows() == list(range(len(first + second)))
    before = window.project.snapshot()
    selected, current = window.notes.selected_rows(), window.notes.currentRow()
    window.measure_action("duplicate")
    assert complete_bars(window.project.score) == [first, second, first, second, third]
    assert window.notes.selected_rows() == list(range(len(first + second), 2 * len(first + second)))
    assert_undo_redo(window, before, selected, current)


def test_fill_consumes_only_selected_measure_rests(measure_window, qtbot):
    window = measure_window
    first, last = [Note(6)] * 4, [Note(3, finger=2)] * 4
    load_score(window, first + last)
    click_bar(qtbot, window, 0)
    window.insert_bars(position="after", count=1)
    before = window.project.snapshot()
    qtbot.keyClicks(window.notes, "r")
    assert window.project == before, "choosing the next duration must not resize the existing rest"
    qtbot.keyClicks(window.notes, "12")
    bars = complete_bars(window.project.score)
    assert bars[0] == first and bars[2] == last
    assert sum(note.beats for bar in bars[:2] for note in bar) == 8

    # A four-beat input cannot consume the next measure to fit a three-beat gap.
    qtbot.keyClicks(window.notes, "q")
    before = window.project.snapshot()
    history_count = len(window.history)
    qtbot.keyClicks(window.notes, "7")
    assert window.project == before
    assert len(window.history) == history_count
    assert window.measure_notice.isVisible()
    assert window.measure_notice.text()
    qtbot.keyClicks(window.notes, "e345")
    bars = complete_bars(window.project.score)
    assert bars[0] == first and bars[2] == last
    assert [note.degree for note in bars[1]] == [1, 2, 3, 4, 5]
    assert [note.duration for note in bars[1]] == [8, 8, 4, 4, 4]
    assert sum(note.beats for bar in bars[:2] for note in bar) == 8


@pytest.mark.parametrize("zoom", [75, 150, 200])
def test_measure_number_selection_survives_zoom_and_wrapping(measure_window, qtbot, zoom):
    window = measure_window
    load_score(window, [Note(i % 7 + 1) for i in range(32)])
    window.score_zoom.setCurrentIndex(window.score_zoom.findData(zoom))
    QApplication.processEvents()
    click_bar(qtbot, window, 4)
    assert window.notes.selected_rows() == list(range(16, 20))
    click_bar(qtbot, window, 6, Qt.KeyboardModifier.ShiftModifier)
    assert window.notes.selected_rows() == list(range(16, 28))


@pytest.mark.parametrize("size", [(1000, 630), (1380, 850)])
def test_score_and_explicit_print_preview_are_reachable(measure_window, size):
    window = measure_window
    load_score(window, [Note(1)] * 12)
    window.notes.select_rows([4, 5, 6, 7], current=5)
    before = window.project.snapshot()
    window.resize(*size)
    QApplication.processEvents()
    assert window.workspace_view.currentData() == "score"
    assert window.workspace_view.count() == 1
    assert not window.workspace_view.isVisible()
    view = window.notes
    visible_note = False
    for row in range(view.model().rowCount()):
        point = view.visualRect(view.model().index(row, 0)).center()
        if (view.viewport().rect().contains(point)
                and window.childAt(view.viewport().mapTo(window, point)) is view.viewport()):
            visible_note = True
            break
    assert visible_note, "the initial editor must expose music, without first scrolling past its toolbars"
    window.change_workspace(0)
    QApplication.processEvents()
    assert (window.width(), window.height()) == size
    assert window.editor_container.isVisible()
    assert window.notes.isVisible()
    assert not window.photo_scroll.isVisible()
    assert not window.pdf_view.isVisible()
    assert window.project == before
    assert window.notes.selected_rows() == [4, 5, 6, 7]
    assert window.notes.currentRow() == 5
    controls = [window.save_button, window.notes, window.input_mode,
                window.duration, window.staff, *window.measure_controls]
    for control in controls:
        for scroll in window.findChildren(QScrollArea):
            if scroll.widget() and scroll.widget().isAncestorOf(control):
                scroll.ensureWidgetVisible(control)
        QApplication.processEvents()
        target = window.childAt(control.mapTo(window, control.rect().center()))
        assert control.isVisible()
        assert target is control or control.isAncestorOf(target)
    window.show_print_preview()
    QApplication.processEvents()
    assert window.pdf_view.isVisible()
    window.change_workspace(window.workspace_view.findData("score"))
    QApplication.processEvents()
    assert window.notes.isVisible()
    assert not window.pdf_view.isVisible()
    assert window.project == before
    assert window.notes.selected_rows() == [4, 5, 6, 7]


def test_source_mode_protects_visual_measures_and_remains_editable(measure_window, monkeypatch):
    window = measure_window
    load_score(window, [Note(1)] * 8)
    window.notes.select_rows([0, 1, 2, 3])
    window.show_print_preview()
    window.switch_mode()
    assert window.project.mode == "source"
    assert window.source.isVisible(), "entering source mode must expose its editor from PDF-only mode"
    assert all(not control.isEnabled() for control in window.measure_controls)
    before = window.project.snapshot()
    history_count = len(window.history)
    monkeypatch.setattr(window, "error", lambda *args: None)
    window.insert_bars(position="end", count=2)
    for action in ("clear", "delete", "duplicate"):
        window.measure_action(action)
    assert window.project == before
    assert len(window.history) == history_count
    window.source.insertPlainText("% 原始碼仍可編輯\n")
    assert "% 原始碼仍可編輯" in window.project.source
    assert window.project.score == before.score


def test_editing_is_local_and_midi_pdf_are_prepared_on_demand(qtbot, tmp_path, monkeypatch):
    # A valid small PDF keeps this UI scheduling test independent of LilyPond;
    # real engine output is checked by integration tests and manual captures.
    template = tmp_path / "template.pdf"
    writer = QPdfWriter(str(template))
    painter = QPainter(writer)
    painter.drawText(60, 60, "On-demand score output")
    painter.end()
    del painter, writer
    pdf_bytes = template.read_bytes()
    calls, played = [], []

    def fake_render(source, output, lilypond, cancel, *, create_pdf, **kwargs):
        calls.append(create_pdf)
        pdf, midi, source_file = output / "score.pdf", output / "score.midi", output / "score.ly"
        midi.write_bytes(b"test MIDI output")
        source_file.write_text(source, encoding="utf-8")
        if create_pdf:
            pdf.write_bytes(pdf_bytes)
        return RenderResult(pdf, midi, source_file, "test render completed")

    monkeypatch.setattr(app_module, "render", fake_render)
    monkeypatch.setattr(MainWindow, "current_tool", lambda *args: "test-lilypond")
    monkeypatch.setattr(MainWindow, "error", lambda self, title, error: pytest.fail(f"{title}: {error}"))
    window = MainWindow(recovery=False)
    window.recovery_path = tmp_path / "recovery.jps"
    window.autosave.stop()
    window.autopreview.setInterval(20)
    qtbot.addWidget(window, before_close_func=lambda widget: setattr(widget, "dirty", False))
    window.show()
    window.activateWindow()
    window.notes.setFocus()
    qtbot.wait(300)  # Exercise the constructor's delayed startup callback.
    assert calls == []
    window.notes.select_rows([0])
    qtbot.keyClicks(window.notes, "2")
    assert window.project.score.notes[0].degree == 2
    window.autopreview.timeout.emit()
    qtbot.wait(50)
    assert calls == []
    assert window.task is None
    before = window.project.snapshot()

    monkeypatch.setattr(window.player, "play", lambda path: played.append(path.read_bytes()))
    window.play_midi()
    qtbot.waitUntil(lambda: window.task is None and bool(played))
    assert calls == [False]
    assert not window.result.pdf.exists()
    assert window.notes.isVisible()
    assert not window.pdf_view.isVisible()

    destination = tmp_path / "中文 列印樂譜.pdf"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(destination), "PDF"))
    window.export("pdf")
    qtbot.waitUntil(lambda: window.task is None and destination.exists())
    assert calls == [False, True]
    assert destination.read_bytes() == pdf_bytes
    assert window.project == before
    assert not window.pdf_view.isVisible()
    window.show_print_preview()
    assert window.pdf_view.isVisible()
    assert window.pdf_document.pageCount() == 1
    assert calls == [False, True], "the same current PDF can be reused for print preview"
    window.change_workspace(0)
    assert window.notes.isVisible()
    assert window.project == before
