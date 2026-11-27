"""Save never writes a project that Open/recovery would refuse (review P0.2).

Property-style: generated v2 scores using every catalog symbol, parts, pickups, bar items and
adversarial text must round-trip through ``save_project``/``load_project`` unchanged; a project
the reader would refuse is refused by the writer, and the previous file is kept.
"""
import random
from dataclasses import replace

import pytest

from jianpu_studio import model, symbols
from jianpu_studio.model import (
    DURATIONS,
    KEYS,
    METERS,
    Note,
    Part,
    Project,
    Score,
    load_project,
    pickup_choices,
    project_text,
    save_project,
)

TEXT = ['', '春江花月夜', 'Erhu "1" \\ {x} % $ # NextPart', '＂全形＂％｛｝＼', 'a' * 40, '曲 ' * 60]
MARKS = [s for s in symbols.SYMBOLS.values() if s.storage == "marks"]
SPANS = [s for s in symbols.SYMBOLS.values() if s.storage == "spans"]
BARS = [s for s in symbols.SYMBOLS.values() if s.storage == "bar"]


def _valid(note):
    try:
        note.validate()
        return True
    except ValueError:
        return False


def _note(rng, marks=(), spans_start=(), spans_end=()):
    degree = rng.choice([0, 1, 2, 3, 4, 5, 6, 7])
    tuplet = 0
    duration = rng.choice(list(DURATIONS))
    candidate = Note(degree, octave=0 if not degree else rng.choice([-2, -1, 0, 1, 2]), duration=duration,
                     dotted=rng.random() < 0.2, accidental="" if not degree else rng.choice(["", "#", "b", "n"]),
                     tie=bool(degree) and rng.random() < 0.1, tuplet=tuplet,
                     grace_before="" if not degree or rng.random() < 0.8 else "#1''2,,b7",
                     grace_after="" if not degree or rng.random() < 0.9 else "3'",
                     marks=marks, spans_start=spans_start, spans_end=spans_end)
    if _valid(candidate):
        return candidate
    # Drop what this combination does not allow, keeping the pitch.
    for change in ({"grace_after": ""}, {"tie": False}, {"marks": ()}, {"degree": 1, "octave": 0, "accidental": ""},
                   {"spans_start": (), "spans_end": ()}, {"dotted": False}):
        candidate = Note(**{**{f: getattr(candidate, f) for f in
                               ("degree", "octave", "duration", "dotted", "accidental", "tie", "tuplet",
                                "grace_before", "grace_after", "marks", "spans_start", "spans_end")}, **change})
        if _valid(candidate):
            return candidate
    raise AssertionError(candidate)


def _marks(rng):
    chosen, slots = [], set()
    for symbol in rng.sample(MARKS, rng.randint(0, 6)):
        if symbol.slot and symbol.slot in slots:
            continue
        slots.add(symbol.slot)
        chosen.append(symbol.id)
    return tuple(chosen)


def generated_project(seed):
    rng = random.Random(seed)
    meter = rng.choice(METERS)
    probe = Score(meter=meter)
    pickup = rng.choice([0] + pickup_choices(probe.bar_ticks))
    parts = []
    for index in range(rng.randint(1, 4)):
        notes = []
        for _ in range(rng.randint(0, 40)):
            notes.append(_note(rng, marks=_marks(rng)))
        # paired lines (same id at both ends, as the editor writes them)
        for symbol in rng.sample(SPANS, 3):
            if len(notes) >= 2:
                a = rng.randrange(len(notes) - 1)
                b = rng.randrange(a + 1, len(notes))
                for row, field in ((a, "spans_start"), (b, "spans_end")):
                    current = getattr(notes[row], field)
                    if all(symbols.SYMBOLS[s].family != symbol.family for s in current):
                        trial = replace(notes[row], **{field: current + (symbol.id,)})
                        if _valid(trial):
                            notes[row] = trial
        if index == 0:
            for symbol in rng.sample(BARS, 6):
                if notes:
                    row = rng.randrange(len(notes))
                    field = "bar_start" if symbol.bar_edge == "start" else "bar_end"
                    notes[row] = replace(notes[row], **{field: getattr(notes[row], field) + (symbol.id,)})
        parts.append(Part(rng.choice(TEXT)[:40], notes, rng.choice(TEXT)))
    score = Score(title=rng.choice(TEXT), composer=rng.choice(TEXT), key=rng.choice(KEYS), meter=meter,
                  tempo=rng.randint(20, 300), with_staff=rng.random() < 0.5, paper_size=rng.choice(["a4", "letter"]),
                  staff_size=rng.randint(16, 30), bars_per_line=rng.randint(0, 8), pickup=pickup,
                  print_variant=rng.choice(["tc", "sc"]), parts=parts)
    score.validate()
    return Project(score)


def every_symbol_project():
    """One score carrying every catalog symbol at least once (marks, lines, bar items, tie, tuplet)."""
    notes = []
    for symbol in MARKS:
        note = Note(1 if not symbol.on_rest else 0, marks=(symbol.id,))
        notes.append(note if _valid(note) else Note(1, marks=(symbol.id,)))
    for symbol in SPANS:
        notes += [Note(2, spans_start=(symbol.id,)), Note(3, spans_end=(symbol.id,))]
    for symbol in BARS:
        field = "bar_start" if symbol.bar_edge == "start" else "bar_end"
        notes.append(Note(4, **{field: (symbol.id,)}))
    notes += [Note(5, tie=True), Note(5)]
    notes += [Note(6, duration=8, tuplet=3, tuplet_start=True), Note(6, duration=8, tuplet=3),
              Note(6, duration=8, tuplet=3, tuplet_end=True)]
    notes += [Note(7, grace_before="#1''b2,,", grace_after="3")]
    for note in notes:
        note.validate()
    score = Score(title='「上」＂下＂ \\ { } % NextPart', pickup=24, meter="3/4",
                  parts=[Part("甲", notes, "春 夏"), Part("乙 NextPart", [Note(1)] * 20, "50%")])
    score.validate()
    return Project(score)


def _roundtrip(tmp_path, project):
    path = tmp_path / "往返 測試.jps"
    save_project(path, project)
    loaded = load_project(path)
    assert loaded == project
    assert loaded.to_dict() == project.to_dict()
    assert project_text(loaded) == path.read_text(encoding="utf-8")


def test_every_catalog_symbol_round_trips(tmp_path):
    project = every_symbol_project()
    used = {i for part in project.score.parts for n in part.notes
            for i in n.marks + n.spans_start + n.spans_end + n.bar_start + n.bar_end}
    assert used >= {s.id for s in MARKS + SPANS + BARS}
    _roundtrip(tmp_path, project)


@pytest.mark.parametrize("seed", range(60))
def test_generated_scores_round_trip(tmp_path, seed):
    _roundtrip(tmp_path, generated_project(seed))


def test_largest_scores_round_trip_or_are_refused_without_touching_the_file(tmp_path):
    """Up to 10,000 notes with many marks each: whatever Save writes, Open reads back."""
    slots = {}
    for mark in MARKS:
        key = mark.slot or mark.id
        if mark.slot not in ("breath",) and len(mark.id) >= len(slots.get(key, "")):
            slots[key] = mark.id
    for count in (8, 16, len(slots)):
        note = Note(7, octave=2, accidental="#", dotted=True, duration=32, grace_before="#7''" * 8,
                    marks=tuple(slots.values())[:count])
        if not _valid(note):
            continue
        project = Project(Score(meter="3/8", notes=[note] * model.MAX_NOTES))
        path = tmp_path / f"large-{count}.jps"
        path.write_text("previous", encoding="utf-8")
        try:
            save_project(path, project)
        except ValueError as exc:
            assert "原檔未被覆蓋" in str(exc)
            assert path.read_text(encoding="utf-8") == "previous"
        else:
            assert load_project(path) == project
    # The reviewer's case (8 marks, 10,000 dotted 32nds) must save and reopen.
    note = Note(7, octave=2, accidental="#", dotted=True, duration=32, grace_before="7''" * 8,
                marks=tuple(slots.values())[:8])
    if _valid(note):
        _roundtrip(tmp_path, Project(Score(meter="3/8", notes=[note] * model.MAX_NOTES)))


def test_save_refuses_what_open_would_refuse_and_keeps_the_old_file(tmp_path, monkeypatch):
    path = tmp_path / "舊檔.jps"
    save_project(path, Project(Score(notes=[Note(1, duration=1)])))
    data = path.read_bytes()
    big = Project(Score(notes=[Note(2, marks=("finger.1", "bow.down"))] * 4000))
    monkeypatch.setattr(model, "MAX_FILE_BYTES", len(data) * 4)
    with pytest.raises(ValueError, match="原檔未被覆蓋"):
        save_project(path, big)
    assert path.read_bytes() == data
    assert load_project(path).score.notes == [Note(1, duration=1)]
    # An in-memory score that the validator rejects is never written either.
    broken = Project(Score(notes=[Note(1)]))
    broken.score.parts[0].notes[0] = "not a note"
    with pytest.raises((ValueError, AttributeError)):
        save_project(path, broken)
    assert path.read_bytes() == data
