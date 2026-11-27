"""Every meter and pickup change, every way of moving the bar lines, on generated scores.

Deterministic seeds build scores with a pickup or not, 1-3 parts, tuplets, ties, front and
after graces, marks, text, lines (slur, hairpin, 8va, trill, rit., glissando) and bar items
(double/dashed/final barlines, repeats, endings, Segno/D.S./Fine/To Coda/Coda). For every
meter in ``METERS`` and every pickup length, each way the editor offers (``rebar.options``)
is applied to a copy and checked independently of ``rebar.preview``:

* no rhythm or edit problem the score did not have, no new structure problem (so the
  preview never has to withhold a way it built);
* the same sounding music (tied pieces joined) at the same place after the rests in front,
  front graces on a chain's first piece and after-graces on its last;
* nothing lost except what the preview names; all parts equally long;
* the preview's numbers (bars, split notes, rests in front and at the end, signs moved) are
  what applying did.

A smaller sweep runs the same changes through the editor and checks that one undo restores
the original project exactly.
"""
import random
from collections import Counter
from dataclasses import replace

import pytest

from jianpu_studio import notation_edit, rebar, symbols
from jianpu_studio.app import MainWindow
from jianpu_studio.model import METERS, Note, Part, Project, Score, pickup_choices
from jianpu_studio.notation_rules import structure_problems

SEEDS = range(120)
UI_SEEDS = range(12)
LENGTHS = [(1, False), (2, True), (2, False), (4, True), (4, False), (8, True), (8, False), (16, False), (32, True),
           (32, False)]
SPANS = ("line.slur", "hairpin.cresc", "line.8va", "tempo.rit_span", "orn.trill_span", "slide.gliss")
MARKS = ("dyn.f", "finger.1", "art.staccato")
BAR_PATTERNS = ("double", "dashed", "final", "repeat", "volta", "segno", "fine", "coda")


def _fill(rng, length, part):
    notes, position = [], 0
    while position < length:
        left = length - position
        if left >= 48 and position % 48 == 0 and rng.random() < .15:
            degree = rng.randint(1, 7)
            notes += [Note(degree if k != 1 or part else 0, duration=8, tuplet=3, tuplet_start=k == 0,
                           tuplet_end=k == 2) for k in range(3)]
            position += 48
            continue
        fits = [Note(1, duration=d, dotted=t) for d, t in LENGTHS]
        fits = [n for n in fits if n.ticks <= left and (left - n.ticks == 0 or left - n.ticks >= 6)]
        note = rng.choice(fits[:4])
        degree = 0 if rng.random() < .2 else rng.randint(1, 7)
        notes.append(replace(note, degree=degree))
        position += note.ticks
    return notes


def _ok(score):
    try:
        score.validate()
    except ValueError:
        return False
    return score.rhythm_error() is None and score.edit_error() is None


def _try(score, change):
    trial = score.copy()
    try:
        change(trial)
    except (ValueError, IndexError):
        return score
    return trial if _ok(trial) else score


def make_score(seed):
    rng = random.Random(seed)
    meter = rng.choice(METERS)
    bar = Score(meter=meter).bar_ticks
    pickup = rng.choice([0, 0, *pickup_choices(bar)])
    count = rng.randint(2 if pickup else 1, 5)
    lengths = [pickup or bar] + [bar] * (count - 1)
    if pickup and count > 1 and rng.random() < .5:
        lengths[-1] = bar - pickup                   # a short last bar that completes the pickup
    parts = [Part(f"聲部{p + 1}", [n for length in lengths for n in _fill(rng, length, p)])
             for p in range(rng.choice([1, 1, 2, 3]))]
    if rng.random() < .12:
        # A new or template score: only rests, which is simply redrawn (bar items kept per bar).
        parts = [replace(p, notes=[Note(0, duration=n.duration, dotted=n.dotted) if not n.tuplet else Note(0)
                                   for n in p.notes if not n.tuplet or n.tuplet_start]) for p in parts]
    score = Score(meter=meter, pickup=pickup, parts=parts)
    assert _ok(score), seed
    for _ in range(rng.randint(0, 12)):
        part = rng.randrange(len(score.parts))
        notes = score.parts[part].notes
        row = rng.randrange(len(notes))
        what = rng.choice(("tie", "grace_before", "grace_after", "mark", "text", "span", "span", "bar", "bar"))

        def change(s, what=what, part=part, row=row):
            ns = s.parts[part].notes
            note = ns[row]
            if what == "tie":
                nxt = ns[row + 1]
                if not note.degree or nxt.tuplet != note.tuplet:
                    raise ValueError
                ns[row + 1] = replace(nxt, degree=note.degree, octave=note.octave, accidental=note.accidental)
                ns[row] = replace(note, tie=True)
            elif what in ("grace_before", "grace_after"):
                if not note.degree:
                    raise ValueError
                ns[row] = replace(note, **{what: rng.choice(("5", "23", "#4'5"))})
            elif what == "mark":
                ns[row] = replace(note, marks=symbols.sort_ids([*note.marks, rng.choice(MARKS)]))
            elif what == "text":
                ns[row] = replace(note, text=rng.choice(("中速", "引子")))
            elif what == "span":
                sid = rng.choice(SPANS)
                end = row + 1 if sid == "slide.gliss" else rng.randrange(row + 1, min(len(ns), row + 8))
                ns[row] = replace(ns[row], spans_start=symbols.sort_ids([*ns[row].spans_start, sid]))
                ns[end] = replace(ns[end], spans_end=symbols.sort_ids([*ns[end].spans_end, sid]))
            else:
                bars = len(s.part_bars(0))
                pattern, a = rng.choice(BAR_PATTERNS), rng.randrange(bars)
                b = rng.randrange(a, bars)
                items = {"double": [(a, "bar.double")], "dashed": [(a, "bar.dashed")],
                         "final": [(bars - 1, "bar.final")],
                         "repeat": [(a, "bar.repeat_start"), (b, "bar.repeat_end")],
                         "volta": [(a, "volta.1"), (a, "bar.repeat_end"), (a + 1, "volta.2"), (a + 1, "volta.end")],
                         "segno": [(a, "nav.segno"), (bars - 1, "nav.ds")],
                         "fine": [(a, "nav.fine"), (bars - 1, "nav.dc")],
                         "coda": [(0, "nav.segno"), (a, "nav.tocoda"), (b, "nav.ds"),
                                  (min(b + 1, bars - 1), "nav.coda_sign")]}[pattern]
                for at, sid in items:
                    notation_edit.set_bar_item(s, at, sid)
        score = _try(score, change)
    return score


# ---------------------------------------------------------------- independent views of a score
def chains(notes):
    """Sounding notes, tied pieces joined: (onset, pitch, ticks, pieces, front graces, after graces)."""
    out, position, open_ = [], 0, None
    for note in notes:
        if note.degree:
            pitch = (note.degree, note.octave, "" if note.accidental == "n" else note.accidental)
            if open_ is None:
                open_ = [position, pitch, 0, 0, [], []]
            open_[2] += note.ticks
            open_[3] += 1
            open_[4].append(note.grace_before)
            open_[5].append(note.grace_after)
            if not note.tie:
                out.append(open_)
                open_ = None
        position += note.ticks
    return out


def things(score):
    out = Counter()
    for p, part in enumerate(score.parts):
        for n in part.notes:
            out[(p, "note", "")] += bool(n.degree)
            for kind, ids in (("mark", n.marks), ("bar_start", n.bar_start), ("bar_end", n.bar_end)):
                for ident in ids:
                    out[(p, kind, ident)] += 1
            for kind, ids in (("line_start", n.spans_start), ("line_end", n.spans_end)):
                for sid in ids:
                    out[(p, kind, symbols.SYMBOLS[sid].family)] += 1
            for kind in ("text", "grace_before", "grace_after"):
                if getattr(n, kind):
                    out[(p, kind, getattr(n, kind))] += 1
    return +out


def anchors(score):
    out, position = [], 0
    for note in score.parts[0].notes:
        out += [(i, "start", position) for i in note.bar_start]
        position += note.ticks
        out += [(i, "end", position) for i in note.bar_end]
    return out


def targets(score):
    for meter in METERS:
        if meter != score.meter:
            new_bar = Score(meter=meter).bar_ticks
            yield meter, score.pickup if score.pickup in pickup_choices(new_bar) else 0
    for pickup in [0, *pickup_choices(score.bar_ticks)]:
        if pickup != score.pickup:
            yield score.meter, pickup


def check(score, meter, pickup, key, result, view):
    where = f"{meter} pickup {pickup} {key}"
    after = score.copy()
    result.apply(after, meter=meter, pickup=pickup)
    after.validate()
    if score.rhythm_error() is None:
        assert after.rhythm_error() is None, where
    if score.edit_error() is None:
        assert after.edit_error() is None, where
    assert len(structure_problems(after)) <= len(structure_problems(score)), where
    totals = {sum(n.ticks for n in p.notes) for p in after.parts}
    assert len(totals) == 1, where
    missing = things(score) - things(after)
    declared = Counter((i.part, i.kind, i.ident) for i in view.lost)
    assert missing == declared, where
    # The same music, moved by the rests in front.
    shift = view.lead
    split = 0
    for old, new in zip(score.parts, after.parts):
        a, b = chains(old.notes), chains(new.notes)
        assert [(c[0] + shift, c[1], c[2]) for c in a] == [(c[0], c[1], c[2]) for c in b], where
        for x, y in zip(a, b):
            assert [g for g in x[4] if g] == [g for g in y[4] if g], where
            assert [g for g in x[5] if g] == [g for g in y[5] if g], where
            if x[3] == 1:
                assert y[4][0] == x[4][0] and y[5][-1] == x[5][0], where
            split += y[3] > x[3]
    extra = sum(y[3] - x[3] for old, new in zip(score.parts, after.parts)
                for x, y in zip(chains(old.notes), chains(new.notes)))
    assert split <= len(view.split) <= extra, where
    assert (len(view.split) == 0) == (extra == 0), where
    # The preview's numbers are what happened.
    assert view.bars_before == len(score.part_bars(0)) and view.bars_after == len(after.part_bars(0)), where
    grown = sum(n.ticks for n in after.parts[0].notes) - sum(n.ticks for n in score.parts[0].notes)
    assert grown == view.lead + view.filled, where
    old, new = anchors(score), anchors(after)
    assert Counter(i[:2] for i in old) == Counter(i[:2] for i in new), where
    moved = 0
    for kind in {i[:2] for i in old}:
        moved += sum(x[2] + shift != y[2] for x, y in zip([i for i in old if i[:2] == kind],
                                                         [i for i in new if i[:2] == kind]))
    assert moved == len(view.moved), where
    assert view.unchanged == ([p.notes for p in score.parts] == [p.notes for p in after.parts]), where


@pytest.mark.parametrize("seed", SEEDS)
def test_every_offered_way_of_moving_bar_lines_does_what_it_says(seed):
    score = make_score(seed)
    for meter, pickup in targets(score):
        for key, label, result, view in rebar.options(score, meter=meter, pickup=pickup):
            if result is None:
                assert isinstance(view, str) and view, (meter, pickup, key)
                continue
            # The preview's problem check keeps a broken result from being offered; none of the
            # ways should ever need it.
            assert view.problem is None, (meter, pickup, key, view.problem)
            assert rebar.describe(score, view, label)
            check(score, meter, pickup, key, result, view)


def test_the_generated_scores_cover_every_kind_of_sign():
    kinds = Counter()
    for seed in SEEDS:
        score = make_score(seed)
        notes = [n for p in score.parts for n in p.notes]
        kinds["parts>1"] += len(score.parts) > 1
        kinds["pickup"] += bool(score.pickup)
        kinds["tuplet"] += any(n.tuplet for n in notes)
        kinds["tie"] += any(n.tie for n in notes)
        kinds["grace_before"] += any(n.grace_before for n in notes)
        kinds["grace_after"] += any(n.grace_after for n in notes)
        for n in notes:
            for sid in {*n.spans_start, *n.bar_start, *n.bar_end}:
                kinds[sid] += 1
    wanted = ["parts>1", "pickup", "tuplet", "tie", "grace_before", "grace_after", *SPANS, "bar.double",
              "bar.dashed", "bar.final", "bar.repeat_start", "bar.repeat_end", "volta.1", "volta.2",
              "nav.segno", "nav.ds", "nav.fine", "nav.dc", "nav.tocoda"]
    assert all(kinds[k] for k in wanted), {k: kinds[k] for k in wanted}


# ---------------------------------------------------------------- through the editor
@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(MainWindow, "render_score", lambda *a, **k: None)
    monkeypatch.setattr(MainWindow, "error", lambda self, title, error: pytest.fail(f"{title}: {error}"))
    win = MainWindow(recovery=False)
    win.recovery_path = tmp_path / "recovery.jps"
    win.autosave.stop()
    monkeypatch.setattr(win, "confirm_losses", lambda score, lost: pytest.fail(f"second question: {lost}"))
    monkeypatch.setattr(win.toast, "show_text", lambda *a, **k: None)
    qtbot.addWidget(win, before_close_func=lambda w: setattr(w, "dirty", False))
    return win


@pytest.mark.parametrize("seed", UI_SEEDS)
def test_moving_bar_lines_in_the_editor_is_one_undo_step(window, seed, monkeypatch):
    score = make_score(seed)
    rng = random.Random(seed)
    for meter, pickup in rng.sample(list(targets(score)), 4):
        usable = {key: (result, view) for key, _, result, view in rebar.options(score, meter=meter, pickup=pickup)
                  if result is not None and view.problem is None}
        auto = "rests" if "rests" in usable else "rebar" if "rebar" in usable and usable["rebar"][1].trivial else None
        for key, (result, view) in usable.items():
            if auto and key != auto:
                continue
            window.reset(Project(score.copy()))
            before = window.project.to_dict()
            asked = []
            monkeypatch.setattr(window, "choose_rhythm_option", lambda refused, key=key: asked.append(refused) or key)
            assert window.move_bar_lines(meter=meter, pickup=pickup, label="改")
            expected = score.copy()
            result.apply(expected, meter=meter, pickup=pickup)
            assert window.project.score == expected and len(window.history) == 1
            assert bool(asked) == (auto is None)
            window.undo()
            assert window.project.to_dict() == before
