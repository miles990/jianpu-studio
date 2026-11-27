"""One-click fixes never create a new problem, and the fix offered is the fix of the problem shown.

The sweep builds unfinished and contradictory structures (repeats, two-way repeats, 1–4
endings, 結, D.C., D.S., Fine, To Coda, Coda sign, pickups, parts) plus line, tie, rhythm and
bar-placement defects, and applies every offered fix through ``notation_edit.apply_fix`` (the
code path of the status-bar button).
"""
from __future__ import annotations

import itertools
import random
from collections import Counter
from dataclasses import replace

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from jianpu_studio import notation_rules
from jianpu_studio.app import MainWindow
from jianpu_studio.model import Note, Part, Project, Score, load_project, save_project
from jianpu_studio.notation_edit import Target, apply_fix, set_bar_item, toggle_symbol
from jianpu_studio.notation_rules import bar_items, structure_problems

STRUCTURE = ("bar.repeat_start", "bar.repeat_end", "bar.repeat_both", "volta.1", "volta.2", "volta.3",
             "volta.4", "volta.final", "volta.end", "nav.segno", "nav.ds", "nav.dc", "nav.fine",
             "nav.tocoda", "nav.coda_sign", "bar.final", "bar.double")


def plain(bars: int, *, pickup: int = 0, parts: int = 1, short: bool = False) -> Score:
    """``bars`` bars of quarter notes in 4/4 (a one-beat pickup first); ``short``: the last bar
    lacks a beat."""
    count = 4 * bars + (1 if pickup else 0) - (1 if short else 0)
    notes = [Note(i % 7 + 1) for i in range(count)]
    return Score(key="C", pickup=48 if pickup else 0,
                 parts=[Part(f"p{p}", list(notes)) for p in range(parts)])


def line_defect(score: Score, rng: random.Random) -> None:
    """One line/tie/bar-placement defect that has a one-click fix (or none, for dynamics)."""
    notes = score.parts[rng.randrange(len(score.parts))].notes
    i = rng.randrange(1, len(notes) - 2)
    kind = rng.choice(("slur_end", "slur_start", "tie", "gliss", "overlap", "mid_bar", "dyn"))
    if kind == "slur_end":
        notes[i] = replace(notes[i], spans_end=("line.slur",))
    elif kind == "slur_start":
        notes[i] = replace(notes[i], spans_start=("line.slur",))
    elif kind == "tie":
        notes[i] = replace(notes[i], degree=1, tie=True)
        notes[i + 1] = replace(notes[i + 1], degree=2)
    elif kind == "gliss":
        notes[i - 1] = replace(notes[i - 1], spans_start=("slide.gliss",))
        notes[i + 1] = replace(notes[i + 1], spans_end=("slide.gliss",))
    elif kind == "overlap":
        notes[i - 1] = replace(notes[i - 1], spans_start=("line.slur",))
        notes[i] = replace(notes[i], spans_start=("line.slur",))
        notes[i + 1] = replace(notes[i + 1], spans_end=("line.slur",))
    elif kind == "mid_bar" and score.parts[0].notes is notes:
        rows = [r for r in range(1, len(notes) - 1) if not score.is_boundary(sum(n.ticks for n in notes[:r]))]
        if rows:
            r = rng.choice(rows)
            notes[r] = replace(notes[r], bar_start=("nav.segno",))
    elif kind == "dyn":
        notes[i - 1] = replace(notes[i - 1], spans_start=("hairpin.cresc",))
        notes[i] = replace(notes[i], marks=("dyn.f",))
        notes[i + 1] = replace(notes[i + 1], spans_end=("hairpin.cresc",))


def generated(seed: int) -> Score:
    rng = random.Random(seed)
    score = plain(rng.randint(2, 6), pickup=rng.random() < .25, parts=1 + (rng.random() < .2),
                  short=rng.random() < .25)
    count = len(score.part_bars(0))
    for _ in range(rng.randint(1, 5)):
        set_bar_item(score, rng.randrange(count), rng.choice(STRUCTURE))
    if rng.random() < .3:
        line_defect(score, rng)
    return score


def state(score):
    with notation_rules.raw():
        rhythm, edit = score.rhythm_error(), score.edit_error()
        structure = Counter((str(p), p.bar) for p in structure_problems(score))
    return (str(rhythm) if rhythm else None), (str(edit) if edit else None), structure


def offers(score):
    """Every problem the editor can show with a one-click fix."""
    found = [p for p in structure_problems(score) if p.fix]
    for message in (score.rhythm_error(), score.edit_error(), score.notation_error()):
        if message is not None and message.fix and all(str(message) != str(p) for p in found):
            found.append(message)
    return found


def check(score, problem) -> str | None:
    """Why applying ``problem``'s fix is wrong (a new problem, or the problem stays), or None."""
    before = state(score)
    trial = score.copy()
    apply_fix(trial, problem.fix, problem.part, problem.row)
    trial.validate()
    rhythm, edit, structure = state(trial)
    new = structure - before[2]
    if new:
        return f"new structure problem {sorted(new)}"
    if problem.structure and structure[(str(problem), problem.bar)] >= before[2][(str(problem), problem.bar)]:
        return "the structure problem is still there"
    kind = "rhythm" if problem.fix == ("fill",) else "structure" if problem.structure else "edit"
    if rhythm is not None and (rhythm == str(problem) or kind != "rhythm" and rhythm != before[0]):
        return f"rhythm problem {rhythm}"
    if edit is not None and (edit == str(problem) or kind != "edit" and edit != before[1]):
        return f"edit problem {edit}"
    return None


SEEDS = range(600)


def test_every_offered_fix_is_safe_on_generated_structures():
    tried, kinds = 0, Counter()
    failures = []
    for seed in SEEDS:
        score = generated(seed)
        for problem in offers(score):
            tried += 1
            kinds[problem.fix[0]] += 1
            if why := check(score, problem):
                failures.append((seed, str(problem), problem.fix, why))
    assert not failures, failures[:5]
    # The sweep really exercises every kind of fix.
    assert tried > 300 and {"fill", "add", "bar_start", "bar_end", "spans_start", "spans_end", "tie"} <= set(kinds)


def test_without_verification_the_generators_offer_unsafe_fixes():
    """The raw rules (before ``fix_is_safe``) do offer fixes that create problems, so the sweep
    above would catch a missing verification."""
    unsafe = 0
    for seed in SEEDS:
        score = generated(seed)
        with notation_rules.raw():
            raw = [p for p in structure_problems(score) if p.fix]
        unsafe += sum(check(score, p) is not None for p in raw)
    assert unsafe > 0


# ---------------------------------------------------------------- the reported cases
def bar_target(score, bar):
    rows = tuple(range(*score.part_bars(0)[bar]))
    return Target("bars", 0, rows, (bar,))


def overlap_score():
    """Review 3 #2: ‖: on the first ending's bar, built from the palette."""
    score = plain(4)
    for bar, sign in ((0, "bar.repeat_end"), (1, "volta.1"), (2, "volta.2"), (1, "bar.repeat_start"),
                      (3, "bar.repeat_end")):
        toggle_symbol(score, bar_target(score, bar), sign)
    return score


def test_a_repeat_start_on_the_first_ending_is_a_located_problem():
    score = overlap_score()
    assert bar_items(score)[1][0] == ("bar.repeat_start", "volta.1")
    problems = structure_problems(score)
    assert problems and problems[0].bar == 1 and "結尾段的第一小節" in problems[0]
    assert score.notation_error() is not None          # export and playback refuse it


def test_a_two_way_repeat_into_the_first_ending_is_a_problem_with_a_safe_fix():
    score = plain(4)
    set_bar_item(score, 0, "bar.repeat_both")
    set_bar_item(score, 1, "volta.1")
    set_bar_item(score, 2, "volta.2")
    set_bar_item(score, 2, "volta.end")
    problems = structure_problems(score)
    assert [p.bar for p in problems] == [1] and problems[0].fix == ("add", 0, "bar.repeat_end")
    apply_fix(score, problems[0].fix)
    assert bar_items(score)[0][1] == ("bar.repeat_end",) and score.notation_error() is None


def test_a_repeat_start_on_the_first_ending_offers_its_removal_when_that_is_safe():
    score = plain(4)
    for bar, item in ((0, "bar.repeat_end"), (1, "bar.repeat_start"), (1, "volta.1"), (2, "volta.2"),
                      (2, "volta.end")):
        set_bar_item(score, bar, item)
    problem = structure_problems(score)[0]
    assert problem.bar == 1 and problem.fix == ("bar_start", "bar.repeat_start")
    apply_fix(score, problem.fix, problem.part, problem.row)
    assert score.notation_error() is None


SIGNS = ((0, "bar.repeat_end"), (1, "volta.1"), (2, "volta.2"), (1, "bar.repeat_start"), (3, "bar.repeat_end"))


@pytest.mark.parametrize("order", list(itertools.permutations(range(len(SIGNS)))))
def test_every_palette_order_of_a_repeat_on_an_ending_is_caught(order):
    score = plain(4)
    for step in order:
        bar, sign = SIGNS[step]
        toggle_symbol(score, bar_target(score, bar), sign)
        items = bar_items(score)
        for b, (starts, _) in enumerate(items):
            numbered = any(s.startswith("volta.") and s[6:].isdigit() for s in starts)
            both = b and "bar.repeat_both" in items[b - 1][1]
            if numbered and ("bar.repeat_start" in starts or both):
                assert structure_problems(score), (order, items)


@pytest.mark.parametrize("sign, missing", [("volta.2", "第一結尾"), ("volta.3", "第二結尾"),
                                           ("volta.4", "第三結尾"), ("volta.final", "結尾的編號")])
def test_later_endings_and_jie_are_stored_out_of_order_and_name_the_next_step(sign, missing):
    score = plain(4)
    toggle_symbol(score, bar_target(score, 1), sign)
    assert sign in bar_items(score)[1][0]
    problems = structure_problems(score)
    assert problems and problems[0].incomplete and problems[0].bar == 1 and missing in problems[0]
    # A loaded score with the same signs reaches the same state.
    loaded = plain(4)
    for bar, (starts, ends) in enumerate(bar_items(score)):
        for item in (*starts, *ends):
            set_bar_item(loaded, bar, item)
    assert bar_items(loaded) == bar_items(score)
    assert list(map(str, structure_problems(loaded))) == list(map(str, problems))
    # A second click takes it away again, leaving no stray sign.
    toggle_symbol(score, bar_target(score, 1), sign)
    assert bar_items(score) == bar_items(plain(4)) and not structure_problems(score)


@pytest.mark.parametrize("order", list(itertools.permutations(("volta.1", "volta.2", "volta.3", "volta.final"))))
def test_endings_in_any_order_give_the_same_structure(order, tmp_path):
    expected = plain(5)
    for bar, sign in ((1, "volta.1"), (2, "volta.2"), (3, "volta.3"), (3, "volta.final")):
        toggle_symbol(expected, bar_target(expected, bar), sign)
    assert expected.notation_error() is None
    score = plain(5)
    where = {"volta.1": 1, "volta.2": 2, "volta.3": 3, "volta.final": 3}
    for sign in order:
        toggle_symbol(score, bar_target(score, where[sign]), sign)
    assert bar_items(score) == bar_items(expected), order
    assert score.notation_error() is None
    save_project(tmp_path / "v.jps", Project(score))
    assert bar_items(load_project(tmp_path / "v.jps").score) == bar_items(expected)


def test_no_ds_is_offered_where_it_would_break_fine():
    """Review 3 #14: Segno at the start, Fine at the end: D.S. on Fine's bar line breaks Fine."""
    score = plain(2)
    set_bar_item(score, 0, "nav.segno")
    set_bar_item(score, 1, "nav.fine")
    with notation_rules.raw():
        assert structure_problems(score)[0].fix == ("add", 1, "nav.ds")     # what used to be offered
    problems = structure_problems(score)
    assert problems[0].incomplete and problems[0].fix is None


def test_no_ds_is_offered_for_a_segno_that_must_be_removed():
    score = plain(4)
    set_bar_item(score, 0, "bar.repeat_start")
    set_bar_item(score, 1, "nav.segno")
    set_bar_item(score, 2, "bar.repeat_end")
    problems = structure_problems(score)
    assert [p.fix for p in problems] == [None, ("bar_start", "nav.segno")]


def test_ds_is_still_offered_where_it_is_safe():
    score = plain(3)
    set_bar_item(score, 1, "nav.segno")
    problem = structure_problems(score)[0]
    assert problem.fix == ("add", 2, "nav.ds")
    apply_fix(score, problem.fix)
    assert score.notation_error() is None


def test_filling_the_last_bar_keeps_its_bar_line_signs():
    score = plain(3, short=True)
    set_bar_item(score, 1, "nav.segno")
    set_bar_item(score, 2, "nav.ds")
    rhythm = score.rhythm_error()
    assert rhythm.fix == ("fill",)
    apply_fix(score, rhythm.fix)
    assert score.notation_error() is None and bar_items(score)[2][1] == ("nav.ds",)


# ---------------------------------------------------------------- the window
@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(MainWindow, "render_score", lambda *a, **k: None)
    errors = []
    monkeypatch.setattr(MainWindow, "error", lambda self, title, error: errors.append((title, str(error))))
    QSettings("JianpuStudio", "JianpuStudio").remove("palette")
    widget = MainWindow(recovery=False)
    widget.errors = errors
    widget.losses = []
    widget.confirm_losses = lambda score, lost: widget.losses.append(lost) or True
    widget.recovery_path = tmp_path / "recovery.jps"
    widget.autosave.stop()
    qtbot.addWidget(widget, before_close_func=lambda w: setattr(w, "dirty", False))
    widget.resize(1380, 850)
    widget.show()
    QApplication.processEvents()
    return widget


def test_the_fix_button_offers_the_fix_of_the_problem_shown(window):
    """Review 3 #19: the first problem has no fix; Next Problem shows one that has."""
    score = plain(4)
    set_bar_item(score, 1, "volta.4")
    set_bar_item(score, 1, "volta.end")
    set_bar_item(score, 2, "nav.segno")
    set_bar_item(score, 3, "nav.dc")
    window.reset(Project(score))
    assert not window.fill_button.isVisibleTo(window)              # the first problem has no fix
    shown = []
    for _ in range(len(window.problem_list)):
        window.goto_problem()
        shown.append((window.statusBar().currentMessage(), window.fill_button.isVisibleTo(window)))
    segno = next(i for i, (text, _) in enumerate(shown) if "Segno 只給 D.S. 用" in text)
    assert [visible for _, visible in shown] == [i == segno for i in range(len(shown))]
    # Show the Segno problem again and use its button: the Segno goes, the fourth ending stays.
    while "Segno 只給 D.S. 用" not in window.statusBar().currentMessage():
        window.goto_problem()
    assert window.fill_button.text() == "移除孤立記號" and "Segno" in window.fill_button.toolTip()
    window.fill_button.click()
    items = bar_items(window.project.score)
    assert "nav.segno" not in items[2][0] and "volta.4" in items[1][0]
    assert not window.errors


def test_the_fix_button_withdraws_an_unsafe_ds(window):
    score = plain(2)
    set_bar_item(score, 0, "nav.segno")
    set_bar_item(score, 1, "nav.fine")
    window.reset(Project(score))
    assert window.problem_list and not window.fill_button.isVisibleTo(window)
    window.fill_button.click()                                      # also via the shortcut path
    assert bar_items(window.project.score) == [(("nav.segno",), ()), ((), ("nav.fine",))]


def test_every_offered_fix_through_the_button_creates_no_problem(window):
    """The same sweep through the real button for a sample of seeds: navigate to each problem
    that has a fix, click, and compare all problems before and after."""
    clicked = 0
    for seed in range(0, 600, 7):
        score = generated(seed)
        for index in range(12):
            window.reset(Project(score.copy()))
            window.problem_cursor, window.problem_shown = -1, None
            entries = [(i, s) for i, s in enumerate(window.problem_sources) if getattr(s, "fix", None)]
            if index >= len(entries):
                break
            target, source = entries[index]
            while window.problem_cursor != target:
                window.goto_problem()
            assert window.fill_button.isVisibleTo(window)
            before = window.project.score.copy()
            window.fill_button.click()
            clicked += 1
            assert not window.errors and not window.losses, (seed, str(source), window.errors, window.losses)
            assert check(before, source) is None
            assert state(window.project.score)[2] - state(before)[2] == Counter(), (seed, str(source))
    assert clicked > 30
