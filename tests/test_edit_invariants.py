"""Randomized edit sequences through the editor's own commands (deterministic seeds).

Adapted from the independent review's harness (work/review2/random_edits.py). Scores have a
pickup or not, 1-3 parts, lines, ties, tuplets, short rests, repeats, endings and jumps. After
every accepted command:

* all parts are equally long, and a note across a bar line is listed as a problem;
* no line is left half open;
* nothing (note, mark, line end, tie, bar item, text, grace) is gone unless the command
  removes it on purpose or the editor named it in a question first;
* save -> load gives the same project; undo gives exactly the previous project and redo
  exactly the next one; a refused command changes nothing.

The inventory below is written independently of ``edit_guard`` on purpose.
"""
import random
import sys
from collections import Counter
from dataclasses import replace

import pytest

from jianpu_studio import symbols
from jianpu_studio.app import MainWindow
from jianpu_studio.model import DURATIONS, Note, Part, Project, Score, parse_project_text, project_text
from jianpu_studio.notation_rules import bar_items, crossing

SEEDS = range(24)
STEPS = 70
MODES = ("insert", "edit", "edit", "fill")
ACTIONS = ("duration", "dot", "delete", "digit", "paste", "duplicate", "split", "collapse", "grace_before",
           "grace_after", "text", "fill_rests", "bar_insert", "bar_delete", "bar_clear", "bar_duplicate",
           "symbol", "unsymbol", "part_add", "part_remove", "cut")
SYMBOLS = ("dyn.p", "finger.1", "art.staccato", "line.slur", "hairpin.cresc", "line.tie", "bar.double", "nav.fine",
           # Round 6: structures are built one sign at a time, so half-built ones occur on the way.
           "bar.repeat_start", "bar.repeat_end", "volta.1", "volta.2", "nav.segno", "nav.ds", "nav.dc", "nav.tocoda")


def fixture(rng):
    pickup, number, structure, tiny = (rng.choice([0, 48]), rng.choice([1, 2, 3]),
                                       rng.choice([None, "dc", "ds", "coda", "half"]), rng.random() < .4)
    prefix = [Note(7, duration=4)] if pickup else []
    if tiny:
        first = [Note(1, duration=32, dotted=True, text="A"),
                 Note(0, duration=32, dotted=True, marks=("dyn.pppp",), text="B"),
                 Note(2, duration=32), Note(0, duration=8), Note(3, duration=2, dotted=True)]
        second = [Note(1, duration=1, text="C")]
    else:
        first = [Note(1, tie=True, text="A", spans_start=("line.slur",)), Note(1, text="B"),
                 Note(2, text="C", spans_start=("hairpin.cresc",)), Note(3, text="D", spans_end=("line.slur",))]
        second = [Note(2, duration=8, tuplet=3, tuplet_start=True, text="E"), Note(0, duration=8, tuplet=3),
                  Note(2, duration=8, tuplet=3, tuplet_end=True, text="F"),
                  Note(3, text="G", spans_end=("hairpin.cresc",)), Note(0, duration=2, marks=("dyn.p",))]
    third = [Note(5, text="H", spans_start=("line.8va",)), Note(5), Note(0), Note(6, text="I")]
    fourth = [Note(5, text="J", spans_end=("line.8va",)), Note(0), Note(6, marks=("finger.1",)), Note(0)]
    fifth = [Note(5, text="K"), Note(0), Note(6), Note(0)]
    sixth = [Note(5), Note(0), Note(6, grace_before="23"), Note(0)]
    if structure == "half":
        # Unfinished structures are a legal state: a ‖: without :‖ and a Segno without D.S.
        first[0] = replace(first[0], bar_start=("bar.repeat_start",))
        fourth[0] = replace(fourth[0], spans_end=(), bar_start=("nav.segno",))
        third[0] = replace(third[0], spans_start=())
    elif structure:
        first[0] = replace(first[0], bar_start=("bar.repeat_start",))
        first[-1] = replace(first[-1], bar_end=("bar.repeat_end",))
        second[0] = replace(second[0], bar_start=("volta.1",))
        third[0] = replace(third[0], spans_start=(), bar_start=("volta.2",))
        third[-1] = replace(third[-1], bar_end=("volta.end",))
        fourth[0] = replace(fourth[0], spans_end=(), bar_start=("nav.segno",) if structure != "dc" else ())
        fourth[-1] = replace(fourth[-1], bar_end=("nav.tocoda",) if structure == "coda" else ("nav.fine",))
        if structure == "coda":
            fifth[-1] = replace(fifth[-1], bar_end=("nav.ds",))
        else:
            sixth[-1] = replace(sixth[-1], bar_end=("nav.ds",) if structure == "ds" else ("nav.dc",))
    notes = prefix + first + second + third + fourth + fifth + sixth
    parts = [Part("first", notes)]
    for p in range(1, number):
        parts.append(Part(f"part {p + 1}", prefix + [Note(0, duration=2), Note(p + 2, duration=2, text=f"P{p}")] * 6))
    score = Score(parts=parts, pickup=pickup)
    score.validate()
    assert score.edit_error() is None and score.rhythm_error() is None
    assert (score.notation_error() is None) == (structure != "half")
    return score


# ---------------------------------------------------------------- an independent inventory
def things(score):
    """(part, tick, row, kind, id) of everything a note carries."""
    out = []
    for p, part in enumerate(score.parts):
        tick = 0
        for row, n in enumerate(part.notes):
            items = [("note", "")] if n.degree else []
            items += [("mark", m) for m in n.marks]
            items += [("start", symbols.SYMBOLS[s].family) for s in n.spans_start]
            items += [("end", symbols.SYMBOLS[s].family) for s in n.spans_end]
            items += [("tie", "")] if n.tie else []
            items += [("bar_start", b) for b in n.bar_start] + [("bar_end", b) for b in n.bar_end]
            items += [("text", n.text)] if n.text else []
            items += [("grace", g) for g in (n.grace_before, n.grace_after) if g]
            out += [(p, tick, row, kind, ident) for kind, ident in items]
            tick += n.ticks
    return out


def lost(before, after, explicit):
    missing = Counter((p, k, i) for p, _, _, k, i in things(before)) - Counter((p, k, i) for p, _, _, k, i in things(after))
    for item in things(before):
        key = (item[0], item[3], item[4])
        if missing[key] and explicit(item):
            missing[key] -= 1
    return +missing


def declared_keys(questions):
    out = Counter()
    for refused, key in questions:
        for item in getattr(refused, "losses", {}).get(key, []) if key else []:
            out[(item.part, {"line_start": "start", "line_end": "end", "grace_before": "grace",
                             "grace_after": "grace"}.get(item.kind, item.kind), item.ident)] += 1
    return out


def explicit_for(action, score, part, rows, bars, sid):
    rows = set(rows)
    bar_of = score.bar_index
    if action in ("delete", "cut"):
        return lambda i: i[0] == part and i[2] in rows and i[3] not in ("bar_start", "bar_end")
    if action == "digit":                 # a note typed over (e.g. with 0) is replaced; its marks are not
        return lambda i: i[0] == part and i[2] in rows and i[3] == "note"
    if action == "text":
        return lambda i: i[0] == part and i[2] in rows and i[3] == "text"
    if action == "collapse":
        return lambda i: i[0] == part and i[3] == "note"
    if action in ("grace_before", "grace_after"):
        return lambda i: i[0] == part and i[3] in ("grace", "mark", "start", "end", "tie")
    if action == "bar_delete":
        return lambda i: bar_of(i[1]) in bars
    if action == "bar_clear":
        return lambda i: bar_of(i[1]) in bars and i[3] not in ("bar_start", "bar_end")
    if action in ("symbol", "unsymbol"):
        symbol = symbols.SYMBOLS[sid]
        kind = {"marks": ("mark",), "spans": ("start", "end"), "tie": ("tie",)}.get(symbol.storage,
                                                                                   ("bar_start", "bar_end"))
        return lambda i: i[3] in kind
    if action == "part_remove":
        return lambda i: True
    if action == "fill_rests":
        # The one-click fix of a conflicting repeat/jump sign removes that sign (on purpose).
        fix = getattr(score.notation_error(), "fix", None)
        if fix and fix[0] in ("bar_start", "bar_end"):
            return lambda i: i[0] == 0 and i[3] == fix[0] and i[4] == fix[1]
    return lambda i: False


# ---------------------------------------------------------------- driving the editor
@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(MainWindow, "render_score", lambda *a, **k: None)
    win = MainWindow(recovery=False)
    win.recovery_path = tmp_path / "recovery.jps"
    win.autosave.stop()
    win.errors = []
    monkeypatch.setattr(win, "error", lambda title, error: win.errors.append(str(error)))
    monkeypatch.setattr(win, "ask_note_text", lambda current: win.next_text)
    qtbot.addWidget(win, before_close_func=lambda w: setattr(w, "dirty", False))
    yield win
    # Notes left on the offscreen clipboard crash Qt when the test process exits.
    from PySide6.QtWidgets import QApplication
    QApplication.clipboard().clear()


def run(window, rng, action, part, rows, questions):
    w = window
    sid = rng.choice(SYMBOLS)

    def choose(refused):
        key = rng.choice([None] + [c.key for c in refused.choices] * 2)
        questions.append((refused, key))
        return key
    w.choose_rhythm_option = choose
    bars = sorted({w.project.score.bar_index(sum(n.ticks for n in w.part_notes()[:r])) for r in rows})
    if action == "duration":
        w.edit_property("duration", rng.choice(list(DURATIONS)))
    elif action in ("dot", "delete", "paste", "duplicate", "cut"):
        if action == "paste" and rng.random() < .6:
            w.copy_notes()
        w.note_command(action)
    elif action == "digit":
        w.input_pitch(rng.randint(0, 7))
    elif action in ("split", "collapse"):
        w.triplet_action(collapse=action == "collapse")
    elif action == "text":
        w.next_text = rng.choice(["", "中速", "深情地"])
        w.edit_note_text()
    elif action in ("grace_before", "grace_after"):
        w.apply_notation_changes({action: rng.choice(["", "#4'5", "b2", "34"])})
    elif action == "fill_rests":
        w.fix_problem()
    elif action == "bar_insert":
        w.notes.select_bars(bars or [0])
        w.insert_bars(rng.choice(["before", "after", "end"]))
    elif action.startswith("bar_"):
        w.notes.select_bars(bars or [0])
        w.measure_action(action[4:])
    elif action == "symbol":
        w.palette_apply(sid)
    elif action == "unsymbol":
        w.remove_mark(sid)
    elif action == "part_add":
        w.add_part()
    elif action == "part_remove":
        if len(w.project.score.parts) > 1 and part:
            from jianpu_studio import notation_edit
            w.mutate(lambda project: notation_edit.remove_part(project.score, part),
                     **({"explicit": lambda i: True} if "explicit" in w.mutate.__code__.co_varnames else {}))
    return bars, sid


def lengths(score):
    return [[sum(n.ticks for n in part.notes[a:b]) for a, b in score.part_bars(p)] for p, part in enumerate(score.parts)]


@pytest.mark.parametrize("seed", SEEDS)
def test_random_edit_sequences_keep_the_score_whole(window, seed, monkeypatch):
    rng = random.Random(seed)
    w = window
    w.reset(Project(fixture(rng)))
    initial = w.project.to_dict()
    escaped = []
    monkeypatch.setattr(sys, "excepthook", lambda *exc: escaped.append(exc))
    for step in range(STEPS):
        score = w.project.score
        part = rng.randrange(len(score.parts))
        w.switch_part(part)
        count = len(score.parts[part].notes)
        action = rng.choice(ACTIONS)
        mode = rng.choice(MODES)
        anchor = rng.randrange(count) if count else 0
        rows = sorted(rng.sample(range(count), min(count, rng.choice([1, 2, 3])))) if rng.random() < .35 else (
            list(range(anchor, min(count, anchor + rng.choice([1, 1, 2, 3])))))
        w.input_mode.setCurrentIndex(w.input_mode.findData(mode))
        w.insert_position.setCurrentIndex(w.insert_position.findData(rng.choice(["before", "after", "end"])))
        w.notes.select_rows(rows, rows[0] if rows else -1)
        before, before_score, history = w.project.to_dict(), w.project.score.copy(), len(w.history)
        questions, errors = [], len(w.errors)
        where = f"seed {seed} step {step}: {action} part {part} rows {rows} mode {mode}"
        bars, sid = run(w, rng, action, part, rows, questions)
        assert not escaped, (where, escaped)
        assert not any("各聲部的長度不同" in e for e in w.errors[errors:]), where
        after, after_score = w.project.to_dict(), w.project.score
        if before == after:
            assert len(w.history) == history, where
            continue
        assert len(w.history) == history + 1, where
        totals = {sum(n.ticks for n in p.notes) for p in after_score.parts}
        assert len(totals) == 1, (where, totals)
        if crossing(after_score) is not None:
            assert w.problem_list and w.rhythm_label.text().startswith("⚠"), where
        if before_score.edit_error() is None:
            assert after_score.edit_error() is None, (where, after_score.edit_error())
        # An unfinished repeat/jump is listed with its place (and stays editable).
        if after_score.notation_error() is not None and after_score.edit_error() is None:
            assert w.problem_list, where
        gone = lost(before_score, after_score, explicit_for(action, before_score, part, rows, bars, sid))
        undeclared = gone - declared_keys(questions)
        assert not undeclared, (where, undeclared, [str(q) for q, _ in questions])
        if lengths(before_score) == lengths(after_score) and action not in (
                "symbol", "unsymbol", "bar_delete", "fill_rests") and not action.startswith("bar_"):
            assert bar_items(before_score) == bar_items(after_score), where
        assert parse_project_text(project_text(w.project)).to_dict() == after, where
        w.undo()
        assert w.project.to_dict() == before, where
        w.redo()
        assert w.project.to_dict() == after, where
    while w.history:
        w.undo()
    assert w.project.to_dict() == initial
