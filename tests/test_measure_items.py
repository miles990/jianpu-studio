"""Bar items (repeats, endings, jumps, bar lines) through every measure edit and paste.

Review P1.2 (fill erased repeats and jumps), P1.3 (editing one side of a double repeat removed
both) and P1.4 (paste dropped complete repeats and joined unrelated ties/slurs), plus the same
class of bug across every measure operation × paired item × part count.
"""
from dataclasses import replace

import pytest

from jianpu_studio.measures import (
    clean_pasted,
    clear_measures,
    clip_notes,
    delete_measures,
    duplicate_measures,
    insert_measures,
    replace_rest_input,
)
from jianpu_studio.model import Note, Part, Score
from jianpu_studio.notation_rules import bar_items, structure_problems

RS, RE = ("bar.repeat_start",), ("bar.repeat_end",)


def applied(score, result):
    out = score.copy()
    result.apply(out)
    return out


def rest_bar(**items):
    return Note(0, duration=1, **items)


# ---------------------------------------------------------------- the review's cases
def test_filling_a_rest_keeps_the_bars_repeat():
    score = Score(notes=[rest_bar(bar_start=RS, bar_end=RE), rest_bar()])
    result = replace_rest_input(score, 0, Note(1))
    after = applied(score, result)
    assert bar_items(after) == bar_items(score)
    assert result.notice == ""
    assert after.notation_error() is None


def test_filling_a_rest_keeps_navigation():
    score = Score(notes=[rest_bar(bar_start=("nav.segno",)), rest_bar(bar_end=("nav.ds",))])
    for row in (0, 1):
        after = applied(score, replace_rest_input(score, row, Note(1)))
        assert bar_items(after) == bar_items(score)
        assert after.notation_error() is None


def two_repeats():
    return Score(notes=[Note(1, duration=1, bar_start=RS, bar_end=("bar.repeat_both",)),
                        Note(2, duration=1), Note(3, duration=1, bar_end=RE), Note(4, duration=1)])


def test_inserting_inside_the_second_of_a_double_repeat_keeps_the_first():
    score = two_repeats()
    assert score.notation_error() is None
    result = insert_measures(score, 2)
    after = applied(score, result)
    assert bar_items(after) == [(RS, RE), ((), ()), ((), ()), ((), ()), ((), ())]
    assert after.notation_error() is None
    assert "2" in result.notice           # the second repeat's start (half of :‖:) and its end


def test_inserting_between_the_two_repeats_keeps_both():
    score = two_repeats()
    after = applied(score, insert_measures(score, 1))
    assert bar_items(after) == [(RS, RE), ((), ()), (RS, ()), ((), RE), ((), ())]
    assert after.notation_error() is None


def test_untouched_double_repeat_stays_one_bar_line():
    score = two_repeats()
    for result in (insert_measures(score, 4), clear_measures(score, [1]), duplicate_measures(score, [3])):
        after = applied(score, result)
        assert bar_items(after)[:3] == bar_items(score)[:3]
        assert result.notice == ""


def test_deleting_the_first_of_a_double_repeat_keeps_the_second():
    score = two_repeats()
    after = applied(score, delete_measures(score, [0]))
    assert bar_items(after) == [(RS, ()), ((), RE), ((), ())]
    assert after.notation_error() is None


def test_copying_a_whole_repeated_bar_keeps_the_repeat():
    score = Score(notes=[Note(1, bar_start=RS), Note(2), Note(3), Note(4, bar_end=RE)] + [Note(5)] * 4)
    copied = clip_notes(score, 0, range(4))
    assert copied[0].bar_start == RS and copied[3].bar_end == RE
    pasted, removed = clean_pasted(copied, keep_bar_items=True)
    trial = score.copy()
    trial.notes[8:8] = pasted
    assert trial.notation_error() is None and removed == 0
    assert bar_items(trial)[2] == (RS, RE)


def test_copying_part_of_a_repeat_drops_it():
    score = Score(notes=[Note(1, bar_start=RS), Note(2), Note(3), Note(4)] + [Note(5)] * 3 + [Note(6, bar_end=RE)])
    copied = clip_notes(score, 0, range(4))           # the first of the repeat's two bars
    assert not any(n.bar_start or n.bar_end for n in copied)


def test_a_copied_half_tie_never_joins_a_note_at_the_paste_position():
    notes = [Note(1, tie=True), Note(1), Note(2), Note(3)]
    score = Score(notes=notes)
    copied = clip_notes(score, 0, [0])
    assert not copied[0].tie
    # Old clipboard content (or another window's) with the half tie is cleaned at paste time.
    pasted, removed = clean_pasted([notes[0]])
    assert not pasted[0].tie and removed == 1
    assert Score(notes=pasted + notes).notation_error() is None
    assert Score(notes=notes + pasted).notation_error() is None
    # A complete tie inside the copy is kept.
    assert clip_notes(score, 0, [0, 1])[0].tie
    assert clean_pasted(notes[:2])[0][0].tie


def test_outer_notes_of_two_slurs_do_not_become_one_slur():
    notes = [Note(1, spans_start=("line.slur",)), Note(2, spans_end=("line.slur",)),
             Note(3, spans_start=("line.slur",)), Note(4, spans_end=("line.slur",))]
    copied = clip_notes(Score(notes=notes), 0, [0, 3])
    assert not any(n.spans_start or n.spans_end for n in copied)
    whole = clip_notes(Score(notes=notes), 0, range(4))
    assert [n.spans_start for n in whole] == [n.spans_start for n in notes]


def test_navigation_is_never_copied():
    score = Score(notes=[Note(1, duration=1, bar_start=("nav.segno",)), Note(2, duration=1, bar_end=("nav.ds",)),
                         Note(3, duration=1, bar_end=("bar.double",))])
    copied = clip_notes(score, 0, range(3))
    assert [(n.bar_start, n.bar_end) for n in copied] == [((), ()), ((), ()), ((), ("bar.double",))]


# ---------------------------------------------------------------- every operation × item × parts
STRUCTURES = {
    "one-bar repeat": {0: dict(bar_start=RS, bar_end=RE)},
    "two-bar repeat": {1: dict(bar_start=RS), 2: dict(bar_end=RE)},
    "endings": {0: dict(bar_start=RS, bar_end=RE), 1: dict(bar_start=("volta.1",)),
                2: dict(bar_start=("volta.2",), bar_end=("volta.end",))},
    "double repeat": {0: dict(bar_start=RS, bar_end=("bar.repeat_both",)), 2: dict(bar_end=RE)},
    "segno to D.S.": {1: dict(bar_start=("nav.segno",)), 3: dict(bar_end=("nav.ds",))},
    "D.C. al Fine": {1: dict(bar_end=("nav.fine",)), 3: dict(bar_end=("nav.dc",))},
    "D.S. al Coda": {0: dict(bar_start=("nav.segno",)), 1: dict(bar_end=("nav.tocoda",)),
                     2: dict(bar_end=("nav.ds",))},
    "bar lines and text": {0: dict(bar_end=("bar.double",)), 1: dict(bar_start=("nav.coda_sign",)),
                           2: dict(bar_end=("nav.dc_text",)), 3: dict(bar_end=("bar.final",))},
}


# bars spanned by each multi-bar group (a copy of bar a..b-1 placed after it lands inside)
GROUPS = {"two-bar repeat": [(1, 2)], "endings": [(0, 2)], "double repeat": [(0, 1), (1, 2)]}
GROUPS = {name: GROUPS.get(name, []) for name in STRUCTURES}


def structured(name, parts):
    first = [rest_bar(**STRUCTURES[name].get(bar, {})) for bar in range(4)]
    score = Score(parts=[Part("一", first)] + [Part("二", [rest_bar()] * 4) for _ in range(parts - 1)])
    assert score.notation_error() is None, name
    return score


def _operations(score):
    bars = len(score.part_bars(0))
    for bar in range(bars + 1):
        yield f"insert {bar}", lambda bar=bar: insert_measures(score, bar), "insert", bar
    for bar in range(bars):
        yield f"delete {bar}", lambda bar=bar: delete_measures(score, [bar]), "delete", bar
        yield f"clear {bar}", lambda bar=bar: clear_measures(score, [bar]), "clear", bar
        yield f"duplicate {bar}", lambda bar=bar: duplicate_measures(score, [bar]), "duplicate", bar
        row = score.part_bars(0)[bar][0]
        yield f"fill {bar}", lambda row=row: replace_rest_input(score, row, Note(5)), "fill", bar
        yield f"fill part 2, {bar}", lambda bar=bar: replace_rest_input(score, bar, Note(5), len(score.parts) - 1), \
            "fill", bar


@pytest.mark.parametrize("parts", [1, 2])
@pytest.mark.parametrize("name", list(STRUCTURES))
def test_measure_edits_keep_bar_items_with_their_bars(name, parts):
    score = structured(name, parts)
    before = bar_items(score)
    for label, operation, kind, bar in _operations(score):
        try:
            result = operation()
        except ValueError as exc:            # a refusal is fine; a silent change is not
            assert "記號" in str(exc) or "小節" in str(exc), (label, exc)
            continue
        after = applied(score, result)
        # Round 6: a bar edit may leave a repeat/jump unfinished (a listed problem, e.g. a
        # deleted coda bar), never a broken score.
        assert after.edit_error() is None and after.rhythm_error() is None, label
        if structure_problems(after):          # listed with its place, never silent
            assert after.notation_error() is not None, label
        assert len({len(after.part_bars(i)) for i in range(parts)}) == 1, label
        items = bar_items(after)
        if kind in ("clear", "fill"):
            # The bars stay where they are, so every bar line and its items stay too.
            assert items == before, label
            assert result.notice == "", label
        elif kind == "insert" and bar in (0, len(before)):
            assert items[:bar] + items[bar + 1:] == before, label
        elif kind == "duplicate":
            # A copy inside a repeat cuts it (like an inserted bar); otherwise nothing moves.
            inside = any(a <= bar < b for a, b in GROUPS[name])
            if not inside:
                assert items[:bar + 1] + items[bar + 2:] == before, label
            # The copy never repeats a jump.
            assert not any(i.startswith("nav.") and i not in ("nav.coda_sign", "nav.dc_text")
                           for i in items[bar + 1][0] + items[bar + 1][1]), label
        elif kind == "delete":
            # Bars away from any paired item keep theirs: unpaired bar lines move with the bar.
            for b, (starts, ends) in enumerate(before):
                if b != bar:
                    unpaired = {i for i in starts + ends if i in ("bar.double", "bar.final", "nav.coda_sign",
                                                                  "nav.dc_text")}
                    shifted = items[b - (b > bar)]
                    assert unpaired <= set(shifted[0] + shifted[1]), label
        if result.notice:
            assert any(ch.isdigit() for ch in result.notice), label


def test_measure_edit_notice_counts_only_what_was_lost():
    score = structured("endings", 1)
    result = delete_measures(score, [1])                  # the first ending: the whole group goes
    after = applied(score, result)
    assert bar_items(after) == [((), ()), ((), ()), ((), ())]
    assert "4" in result.notice          # |: :| and 2. end-of-ending on the kept bars (1. went with its bar)
    assert replace(score, parts=score.parts) == score       # the original is untouched
