from dataclasses import replace

import pytest

from jianpu_studio.engine import find_tool, render
from jianpu_studio.measures import (
    clear_measures,
    delete_measures,
    duplicate_measures,
    insert_measures,
    measure_ranges,
    replace_rest_input,
)
from jianpu_studio.model import METERS, TICKS_PER_QUARTER, Note, Score


def applied(score, result):
    return replace(score, notes=result.notes)


def four_bars():
    return Score(notes=[Note(degree) for degree in range(1, 5) for _ in range(4)])


def test_ranges_include_incomplete_tail_and_empty_score():
    assert measure_ranges(Score()) == []
    score = Score(notes=[Note(1, duration=1), Note(2), Note(3, duration=8)])
    assert measure_ranges(score) == [(0, 1), (1, 3)]
    assert "還缺" in score.rhythm_error()


@pytest.mark.parametrize("meter", METERS)
def test_insert_clear_and_duplicate_have_exact_meter_and_renderable_source(meter):
    empty = Score(meter=meter)
    inserted = insert_measures(empty, 0, 3)
    score = applied(empty, inserted)
    assert score.rhythm_error() is None
    assert len(measure_ranges(score)) == 3
    assert all(note.degree == 0 for note in score.notes)
    cleared = clear_measures(score, [1])
    duplicated = duplicate_measures(applied(score, cleared), [1, 2])
    final = applied(score, duplicated)
    assert len(measure_ranges(final)) == 5
    assert sum(note.ticks for note in final.notes) == int(score.bar_length * TICKS_PER_QUARTER) * 5
    assert final.rhythm_error() is None
    assert final.to_jianpu()
    assert empty.notes == []


@pytest.mark.parametrize("position", [0, 1, 4])
def test_insert_preserves_original_notes_and_selects_new_bars(position):
    score = four_bars()
    original = list(score.notes)
    result = insert_measures(score, position, 2)
    new_score = applied(score, result)
    ranges = measure_ranges(new_score)
    assert len(ranges) == 6
    start, end = ranges[position][0], ranges[position+1][1]
    assert result.selection == tuple(range(start, end))
    assert result.current == start
    assert new_score.notes[:start] + new_score.notes[end:] == original
    assert score.notes == original
    assert new_score.rhythm_error() is None


def test_delete_noncontiguous_measures_and_clear_have_distinct_behavior():
    score = four_bars()
    deleted = delete_measures(score, [3, 1, 1])
    assert [note.degree for note in deleted.notes] == [1]*4 + [3]*4
    assert deleted.selection == (4, 5, 6, 7)
    assert deleted.current == 4
    cleared = clear_measures(score, [3, 1])
    final = applied(score, cleared)
    ranges = measure_ranges(final)
    assert len(ranges) == 4
    assert final.notes[ranges[0][0]:ranges[0][1]] == score.notes[:4]
    assert final.notes[ranges[2][0]:ranges[2][1]] == score.notes[8:12]
    assert all(final.notes[row].degree == 0 for row in cleared.selection)
    assert final.rhythm_error() is None
    assert score == four_bars()


def test_delete_all_measures_yields_empty_score_without_phantom_bar():
    result = delete_measures(four_bars(), range(4))
    assert result.notes == []
    assert result.selection == ()
    assert result.current == -1
    assert measure_ranges(Score(notes=result.notes)) == []


def test_unfinished_tail_only_completes_when_operation_needs_boundary():
    score = Score(notes=[Note(1, duration=1), Note(2, duration=8, dotted=True)])
    before = insert_measures(score, 1)
    assert sum(n.ticks for n in before.notes) == 8*TICKS_PER_QUARTER + Note(duration=8, dotted=True).ticks
    assert before.notes[-1] is score.notes[-1]
    after = insert_measures(score, 2)
    assert len(measure_ranges(applied(score, after))) == 3
    assert applied(score, after).rhythm_error() is None
    assert after.selection[0] > 1  # Original incomplete tail was padded first.
    duplicated = duplicate_measures(score, [1])
    assert applied(score, duplicated).rhythm_error() is None
    assert len(measure_ranges(applied(score, duplicated))) == 3
    cleared = clear_measures(score, [1])
    assert sum(n.ticks for n in cleared.notes) == 8*TICKS_PER_QUARTER
    deleted = delete_measures(score, [0])
    assert deleted.notes == [score.notes[1]]
    assert sum(n.ticks for n in score.notes) == 4*TICKS_PER_QUARTER + Note(duration=8, dotted=True).ticks


def test_unrepresentable_one_tick_tail_rejected_without_mutation():
    score = Score(notes=[Note(1, duration=32, dotted=True)] * 21)
    original = list(score.notes)
    for operation in (lambda: insert_measures(score, 1), lambda: duplicate_measures(score, [0])):
        with pytest.raises(ValueError, match="剩餘拍數"):
            operation()
        assert score.notes == original
    assert applied(score, clear_measures(score, [0])).rhythm_error() is None


@pytest.mark.parametrize("operation", [
    lambda s: insert_measures(s, 0),
    lambda s: clear_measures(s, [0]),
    lambda s: delete_measures(s, [0]),
    lambda s: duplicate_measures(s, [0]),
    lambda s: replace_rest_input(s, 0, Note(1)),
])
def test_cross_bar_notes_are_rejected_before_any_edit(operation):
    score = Score(notes=[Note(0, duration=2, dotted=True), Note(1, duration=2)])
    original = list(score.notes)
    with pytest.raises(ValueError, match="第 2 個音符超出第 1 小節"):
        operation(score)
    assert score.notes == original


def test_deleted_tie_cannot_reconnect_to_an_equal_pitch_new_neighbor():
    notes = [Note(1)] * 12
    notes[3] = replace(notes[3], tie=True)
    notes[9] = replace(notes[9], tie=True, dynamic="p")
    score = Score(notes=notes)
    result = delete_measures(score, [1])
    assert not result.notes[3].tie
    assert result.notes[5].tie
    assert result.notes[5].dynamic == "p"
    assert result.notice
    assert applied(score, result).notation_error() is None
    assert "~" in applied(score, result).to_jianpu()
    assert score.notes[3].tie


@pytest.mark.parametrize("operation", [
    lambda s: delete_measures(s, [1]),
    lambda s: clear_measures(s, [1]),
    lambda s: insert_measures(s, 1),
])
def test_cut_spans_removed_as_pairs_but_unaffected_spans_preserved(operation):
    notes = [Note(1)] * 16
    notes[0] = replace(notes[0], slur_start=True, repeat_start=True)
    notes[11] = replace(notes[11], slur_end=True, repeat_end=True)
    notes[12] = replace(notes[12], slur_start=True, finger=2, bow="downbow")
    notes[15] = replace(notes[15], slur_end=True)
    score = Score(notes=notes)
    result = operation(score)
    assert sum(n.slur_start for n in result.notes) == 1
    assert sum(n.slur_end for n in result.notes) == 1
    assert not any(n.repeat_start or n.repeat_end for n in result.notes)
    assert result.notes[-4].finger == 2
    assert result.notes[-4].bow == "downbow"
    assert result.notice
    assert applied(score, result).notation_error() is None


def test_duplicate_complete_marks_creates_independent_copies():
    score = Score(notes=[Note(1, slur_start=True, repeat_start=True), Note(2, slur_end=True),
                         Note(3, tie=True), Note(3, repeat_end=True), Note(5, duration=1)])
    result = duplicate_measures(score, [0])
    assert result.notes[:4] == result.notes[4:8] == score.notes[:4]
    assert result.notes[8] is score.notes[4]
    assert result.selection == (4, 5, 6, 7)
    assert result.notice == ""
    assert applied(score, result).notation_error() is None
    assert applied(score, result).to_jianpu().count("R{") == 2


def test_duplicate_does_not_copy_a_partial_connection():
    notes = [Note(1)] * 8
    notes[0] = replace(notes[0], slur_start=True)
    notes[6] = replace(notes[6], slur_end=True)
    score = Score(notes=notes)
    result = duplicate_measures(score, [1])
    assert result.notes[0].slur_start
    assert result.notes[6].slur_end
    assert not any(n.slur_start or n.slur_end for n in result.notes[8:])
    assert result.notice
    assert applied(score, result).notation_error() is None


def test_original_invalid_connections_are_not_guessed_or_repaired_silently():
    score = Score(notes=[Note(1, duration=1, slur_start=True)])
    with pytest.raises(ValueError, match="請先修正連線"):
        delete_measures(score, [0])


def test_rest_input_splits_rest_and_preserves_later_notes_and_bar_positions():
    score = Score(notes=[Note(0, duration=1, dynamic="p"), Note(5, duration=1)])
    result = replace_rest_input(score, 0, Note(3, duration=8, finger=2))
    final = applied(score, result)
    assert final.notes[0] == Note(3, duration=8, finger=2, dynamic="p")
    assert sum(n.ticks for n in final.notes[:-1]) == 4*TICKS_PER_QUARTER
    assert final.notes[-1] is score.notes[-1]
    assert result.selection == (0,) and result.current == 0
    assert final.rhythm_error() is None
    assert final.to_jianpu()
    assert score.notes[0].degree == 0


@pytest.mark.parametrize("meter", ("3/8", "6/8"))
def test_repeated_rest_input_fills_compound_meter_without_moving_next_bar(meter):
    empty = Score(meter=meter)
    score = applied(empty, insert_measures(empty, 0, 2))
    original_next = score.notes[measure_ranges(score)[1][0]:]
    count = int(score.bar_length*2)
    for pitch in range(1, count+1):
        row = next(i for i, n in enumerate(score.notes) if n.degree == 0)
        score = applied(score, replace_rest_input(score, row, Note(pitch, duration=8)))
    ranges = measure_ranges(score)
    assert score.notes[ranges[1][0]:] == original_next
    assert [n.degree for n in score.notes[:ranges[0][1]]] == list(range(1, count+1))
    assert score.rhythm_error() is None
    assert score.to_jianpu()


def test_odd_tick_remainder_can_consume_an_additional_rest_exactly():
    notes = [Note(0, duration=32, dotted=True), Note(0, duration=32),
             Note(5, duration=32, dotted=True), Note(6, duration=8), Note(7, duration=2, dotted=True)]
    score = Score(notes=notes)
    result = replace_rest_input(score, 0, Note(2, duration=32))
    assert result.notes[:2] == [Note(2, duration=32), Note(0, duration=32, dotted=True)]
    assert result.notes[2:] == notes[2:]
    assert applied(score, result).rhythm_error() is None
    assert applied(score, result).to_jianpu()


@pytest.mark.parametrize("notes,replacement,message", [
    ([Note(0, duration=32, dotted=True), Note(1, duration=32)], Note(2, duration=32), "剩餘拍數"),
    ([Note(0), Note(5), Note(0, duration=2)], Note(2, duration=2), "不會覆蓋"),
    ([Note(0, duration=1), Note(0, duration=1)], Note(2, duration=1, dotted=True), "同小節"),
    ([Note(0)], Note(2, duration=2), "同小節"),
    ([Note(1, duration=1)], Note(2), "只會覆寫休止符"),
    ([Note(0, duration=32, dotted=True), Note(0, duration=32, dynamic="p")], Note(2, duration=32), "力度"),
    ([Note(0, duration=1)], Note(2, slur_start=True), "先填入音符"),
])
def test_rest_input_refuses_overwrite_or_unrepresentable_remainders(notes, replacement, message):
    score = Score(notes=list(notes))
    with pytest.raises(ValueError, match=message):
        replace_rest_input(score, 0, replacement)
    assert score.notes == notes


def test_rest_input_removes_affected_repeat_with_notice():
    score = Score(notes=[Note(0, duration=1, repeat_start=True), Note(0, duration=1, repeat_end=True),
                         Note(1, slur_start=True), Note(2), Note(3), Note(4, slur_end=True)])
    result = replace_rest_input(score, 0, Note(5))
    assert not any(n.repeat_start or n.repeat_end for n in result.notes)
    assert result.notes[-4].slur_start and result.notes[-1].slur_end
    assert result.notice
    assert applied(score, result).notation_error() is None


def test_entering_identical_rest_preserves_existing_repeat():
    score = Score(notes=[Note(0, duration=1, repeat_start=True, repeat_end=True)])
    result = replace_rest_input(score, 0, Note(0, duration=1))
    assert result.notes == score.notes
    assert result.notice == ""


@pytest.mark.parametrize("operation", [
    lambda s: insert_measures(s, -1), lambda s: insert_measures(s, 5),
    lambda s: insert_measures(s, 0, 0), lambda s: insert_measures(s, 0, True),
    lambda s: delete_measures(s, []), lambda s: clear_measures(s, [True]),
    lambda s: duplicate_measures(s, [0, 2]), lambda s: delete_measures(s, [4]),
    lambda s: replace_rest_input(s, -1, Note()),
])
def test_invalid_selections_and_counts_are_rejected(operation):
    with pytest.raises(ValueError):
        operation(four_bars())


def test_note_limit_is_checked_without_mutating_original():
    score = Score(notes=[Note(0)] * 10000)
    for operation in (lambda: insert_measures(score, 0), lambda: duplicate_measures(score, [0]),
                      lambda: replace_rest_input(score, 0, Note(1, duration=8))):
        with pytest.raises(ValueError, match="10,000"):
            operation()
    with pytest.raises(ValueError, match="10,000"):
        insert_measures(Score(), 0, 10**9)
    assert len(score.notes) == 10000


@pytest.mark.integration
def test_measure_edits_compile_with_real_lilypond(tmp_path):
    lilypond = find_tool("lilypond")
    if not lilypond:
        pytest.skip("LilyPond is not installed")
    score = Score(notes=[Note(1, finger=0, bow="downbow", slur_start=True, repeat_start=True),
                         Note(2, finger=1, slur_end=True), Note(3, tie=True), Note(3, repeat_end=True)])
    score = applied(score, duplicate_measures(score, [0]))
    score = applied(score, insert_measures(score, 1))
    score = applied(score, replace_rest_input(score, measure_ranges(score)[1][0], Note(4, duration=2)))
    odd = Score(notes=[Note(0, duration=32, dotted=True), Note(0, duration=32),
                       Note(5, duration=32, dotted=True), Note(6, duration=8), Note(7, duration=2, dotted=True)])
    score.notes.extend(replace_rest_input(odd, 0, Note(2, duration=32)).notes)
    result = render(score.to_jianpu(), tmp_path / "小節編輯", lilypond)
    assert result.pdf.is_file()
    assert result.midi and result.midi.is_file()
    assert "Success" in result.log
