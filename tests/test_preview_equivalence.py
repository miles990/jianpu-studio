"""Preview MIDI, exported MIDI and the play map agree on randomized valid scores.

Review 3 compared the three on 112 randomized scores and found note differences in 58
(ties with staccato, grace notes, hairpins, articulations) plus a repeat played in two
orders. This test keeps that comparison in the suite: deterministic seeds, every repeat /
ending / jump structure crossed with every note feature, with the real engine.

Agreement required for every case:
* each part has the same notes: same count, and every preview note pairs with an export
  note of the same pitch starting within 10 ms (so the order agrees up to 10 ms);
* then note ends within 20 ms, velocities within 1;
* the files end together (within 20 ms);
* the play map: every written note that starts a sound has a note-on of its pitch at the
  map's time (within 10 ms), and the map lasts as long as the exported file.
"""
from __future__ import annotations

import os
import random
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from jianpu_studio import smf
from jianpu_studio.engine import find_tool, render
from jianpu_studio.model import KEYS, Note, Part, Project, Score
from jianpu_studio.notation_edit import set_bar_item
from jianpu_studio.notation_rules import structure_problems
from jianpu_studio.playmap import PlayMap
from jianpu_studio.preview_midi import _ottava, pitch, playing_order, preview_midi

STRUCTURES = ("plain", "repeat", "implicit", "both", "volta1", "volta2", "volta3", "volta4", "dc", "ds", "dc-fine",
              "ds-fine", "dc-coda", "ds-coda", "repeat-dc", "repeat-fine", "volta-ds")
FEATURES = ("ties", "grace", "ottava", "rhythm", "dynamics", "articulation", "mixed")
BARS = 9
ONSET, END, VELOCITY = 0.010, 0.020, 1


def _structure(score, kind, rng):
    def put(bar, *items):
        for item in items:
            set_bar_item(score, bar, item)
    if kind == "plain":
        return
    if kind.startswith("volta"):
        count = int(kind[-1]) if kind[-1].isdigit() else 2
        start = 0 if kind == "volta-ds" else rng.choice([0, 1])
        end = 1 if kind == "volta-ds" else rng.choice([start, start + 1])
        if start or rng.random() < .6:
            put(start, "bar.repeat_start")
        put(end, "bar.repeat_end")
        last = end
        for k in range(1, count + 1):
            last += 1
            put(last, f"volta.{k}")
            if k < count and rng.random() < .4 and kind != "volta-ds":
                last += 1
            if k == count and rng.random() < .4:
                put(last, "volta.final")
        put(last, "volta.end")
        if kind == "volta-ds":
            put(last + 1, "nav.segno")
            put(BARS - 1, "nav.ds")
        return
    if kind in ("repeat", "implicit", "both", "repeat-dc", "repeat-fine"):
        start = 0 if kind in ("implicit", "repeat-dc", "repeat-fine") else rng.choice([0, 1, 2])
        end = rng.choice([max(1, start), max(1, start) + 1, max(1, start) + 2])
        if kind != "implicit":
            put(start, "bar.repeat_start")
        put(end, "bar.repeat_both" if kind == "both" else "bar.repeat_end")
        if kind == "both":
            put(end + rng.choice([1, 2, 3]), "bar.repeat_end")
        if kind == "repeat-dc":
            put(end if rng.random() < .5 else BARS - 2, "nav.dc")
        if kind == "repeat-fine":
            put(end, "nav.fine")
            put(BARS - 1, "nav.dc")
        return
    jump = "nav.ds" if kind.startswith("ds") else "nav.dc"
    end = BARS - 1 if kind.endswith("fine") else rng.choice([3, 4, 5, 6, 7])
    start = rng.randrange(0, end)
    if jump == "nav.ds":
        put(start, "nav.segno")
    if kind.endswith("fine"):
        put(rng.randrange(start, end), "nav.fine")
    if kind.endswith("coda"):
        put(rng.randrange(start, end), "nav.tocoda")
        if rng.random() < .3:
            put(end + 1, "nav.coda_sign")
    put(end, jump)


# every dynamic of the catalog (absolute levels and the accent-like ones)
DYNAMICS = ("dyn.pppp", "dyn.ppp", "dyn.pp", "dyn.p", "dyn.mp", "dyn.mf", "dyn.f", "dyn.ff", "dyn.fff", "dyn.ffff",
            "dyn.sf", "dyn.fz", "dyn.sfz", "dyn.sfzz", "dyn.fp", "dyn.sfp", "dyn.sfpp", "dyn.rf", "dyn.rfz")
TEMPLATES = ([4, 4, 4, 4], [2, 4, 4], [4, 2, 4], [8, 8, 4, 2], [1], [2, 2], [8, 8, 8, 8, 2])


def _bar(rng, feature, ticks, first):
    if ticks != 192:
        durations = [4, 8] if ticks == 72 else [4]
    else:
        durations = list(rng.choice(TEMPLATES))
    notes = [Note(rng.randint(1, 7), duration=d, octave=rng.choice([-1, 0, 0, 1]), accidental=rng.choice(["", "", "#", "b", "n"]))
             for d in durations]
    if rng.random() < .15 and len(notes) > 1:
        k = rng.randrange(1, len(notes))
        notes[k] = Note(0, duration=notes[k].duration)
    sounding = [k for k, n in enumerate(notes) if n.degree]
    if feature == "ties" and len(notes) > 1:
        for k in sorted(rng.randrange(len(notes) - 1) for _ in range(rng.choice([1, 2]))):
            if notes[k].degree:
                notes[k] = replace(notes[k], tie=True, marks=(rng.choice(["art.staccato", "art.staccatissimo", "art.staccato_hollow",
                                                                         "art.accent"]),) if rng.random() < .4 else ())
                notes[k + 1] = replace(notes[k + 1], degree=notes[k].degree, octave=notes[k].octave, accidental=notes[k].accidental,
                                       marks=(rng.choice(["art.staccato", "art.accent", "dyn.p", "pedal.on"]),) if rng.random() < .3 else ())
    if feature == "grace" and sounding:
        k = rng.choice(sounding)
        text = rng.choice(["2", "#3'b2", "467", "75", "1"])
        if (k == 0 and not first) or rng.random() < .5:
            previous = notes[k - 1] if k else None
            if previous is None or not previous.degree or previous.ticks * 54 >= len([c for c in text if c.isdigit()]) * 144:
                notes[k] = replace(notes[k], grace_before=text)
        else:
            notes[k] = replace(notes[k], grace_after=text)
    if feature == "ottava" and len(sounding) > 1:
        sid = rng.choice(["line.8va", "line.8vb"])
        a, b = sounding[0], sounding[-1]
        notes[a] = replace(notes[a], spans_start=(sid,))
        notes[b] = replace(notes[b], spans_end=(sid,))
        if rng.random() < .5:
            notes[b] = replace(notes[b], grace_before="5" if b and notes[b - 1].ticks >= 12 else "")
    if feature == "rhythm" and ticks == 192:
        notes = [Note(rng.randint(1, 7), duration=8, tuplet=3, tuplet_start=i == 0, tuplet_end=i == 2) for i in range(3)] + [
            Note(rng.randint(0, 7), duration=4, dotted=True), Note(rng.randint(1, 7), duration=8), Note(rng.randint(0, 7))]
        if rng.random() < .4:
            if rng.random() < .5:
                notes[1] = replace(notes[1], grace_after="3")
            else:
                notes[2] = replace(notes[2], grace_before="3")
    if feature == "dynamics" and len(sounding) > 2:
        a, b = sounding[0], sounding[-1]
        notes[a] = replace(notes[a], marks=(rng.choice(DYNAMICS),))
        if rng.random() < .6:
            sid = rng.choice(["hairpin.cresc", "hairpin.dim", "hairpin.cresc_text", "hairpin.dim_text"])
            notes[a] = replace(notes[a], spans_start=(sid,))
            notes[b] = replace(notes[b], spans_end=(sid,),
                               marks=(rng.choice(["dyn.f", "dyn.p", "dyn.mf"]),) if rng.random() < .5 else ())
    if feature == "articulation" and sounding:
        for k in sounding:
            if rng.random() < .5:
                notes[k] = replace(notes[k], marks=(rng.choice(["art.staccato", "art.staccatissimo", "art.staccato_hollow",
                                                                "art.accent", "art.marcato", "art.tenuto", "dyn.sfz",
                                                                "breath.comma", "pedal.on", "pedal.off", "orn.trill"]),))
    return notes


def make_score(seed: int, kind: str, feature: str) -> Score:
    """A valid score for ``kind`` × ``feature`` (deterministic: the first of a fixed series
    of attempts whose structure fits and validates)."""
    for attempt in range(50):
        try:
            score = _attempt(random.Random(seed * 100 + attempt), kind, feature)
            score.validate()
        except ValueError:
            continue
        if score.rhythm_error() is None and score.edit_error() is None and not structure_problems(score):
            return score
    raise AssertionError(f"no valid score for {seed} {kind} {feature}")


def _attempt(rng, kind: str, feature: str) -> Score:
    pickup = rng.choice([0, 0, 48, 72])
    parts = []
    for p in range(rng.choice([1, 2, 2, 3, 4])):
        notes = []
        for bar in range(BARS):
            ticks = pickup if bar == 0 and pickup else 192
            actual = rng.choice(FEATURES[:-1]) if feature == "mixed" else feature
            notes += _bar(rng, actual, ticks, bar == 0)
        parts.append(Part(f"P{p + 1}", notes))
    score = Score(key=rng.choice(KEYS), tempo=rng.choice([32, 60, 91, 96, 120, 175, 240, 300]), parts=parts, pickup=pickup)
    _structure(score, kind, rng)
    return score


def cases():
    """Every structure × every feature, ``JIANPU_EQUIVALENCE_ROUNDS`` seeds each (default 3;
    for a longer exploratory run set it higher, and ``JIANPU_EQUIVALENCE_FIRST`` to start
    at a later round)."""
    rounds = int(os.environ.get("JIANPU_EQUIVALENCE_ROUNDS", "3"))
    first = int(os.environ.get("JIANPU_EQUIVALENCE_FIRST", "0"))
    return [(5000 + 10000 * r + 100 * i + j, kind, feature) for r in range(first, first + rounds)
            for i, feature in enumerate(FEATURES) for j, kind in enumerate(STRUCTURES)]


def _compare(score, preview: bytes, exported: bytes, order) -> list[str]:
    problems = []
    ours, _, _ = smf.read(preview)
    lily, _, _ = smf.read(exported)
    for track in range(1, len(score.parts) + 1):
        key = lambda n: (round(n.start, 4), n.pitch)   # noqa: E731
        a = sorted((n for n in ours if n.track == track), key=key)
        b = sorted((n for n in lily if n.track == track), key=key)
        if len(a) != len(b):
            problems.append(f"part {track}: {len(a)} notes vs {len(b)}")
            continue
        # Pair in time order by pitch: grace notes a tick apart may sort either way.
        unpaired = list(b)
        for x in a:
            y = next((n for n in unpaired if n.pitch == x.pitch and abs(n.start - x.start) < ONSET), None)
            if y is None:
                problems.append(f"part {track}: {x} has no export note")
                break
            unpaired.remove(y)
            if abs(x.end - y.end) >= END or abs(x.velocity - y.velocity) > VELOCITY:
                problems.append(f"part {track}: {x} vs {y}")
                break
        # the play map: every written note that starts a sound, at the map's time
        notes = score.parts[track - 1].notes
        shifts = _ottava(notes)
        starts = {(round(n.start, 2), n.pitch) for n in b}
        play = PlayMap(score, track - 1, order)
        for onset in play.onsets:
            note = notes[onset.row]
            tied = onset.row and notes[onset.row - 1].tie
            if note.degree and not tied:
                expected = round(play.seconds(onset.tick), 2)
                value = pitch(score, note) + shifts[onset.row]
                if not any((round(expected + d, 2), value) in starts for d in (-0.01, 0, 0.01)):
                    problems.append(f"part {track}: map onset {expected}s pitch {value} not in the export")
                    break
    end_a, end_b = _length(preview), _length(exported)
    if abs(end_a - end_b) >= END or abs(PlayMap(score, 0, order).total_seconds - end_b) >= END:
        problems.append(f"length: preview {end_a:.3f}s, export {end_b:.3f}s, map {PlayMap(score, 0, order).total_seconds:.3f}s")
    return problems


def _length(data: bytes) -> float:
    """Seconds to the last End-of-Track, through the file's tempo changes."""
    division = int.from_bytes(data[12:14], "big")
    tempos, end, offset = [], 0, 14
    while offset < len(data):
        size = int.from_bytes(data[offset + 4:offset + 8], "big")
        body, offset = data[offset + 8:offset + 8 + size], offset + 8 + size
        position, tick, running = 0, 0, 0
        while position < len(body):
            value = 0
            for _ in range(4):
                byte = body[position]
                position += 1
                value = (value << 7) | (byte & 0x7F)
                if byte < 0x80:
                    break
            tick += value
            if body[position] & 0x80:
                running = body[position]
                position += 1
            if running == 0xFF:
                kind, length = body[position], body[position + 1]
                if kind == 0x51:
                    tempos.append((tick, int.from_bytes(body[position + 2:position + 5], "big")))
                if kind == 0x2F:
                    end = max(end, tick)
                position += 2 + length
            elif running in (0xF0, 0xF7):
                position += 1 + body[position]
            elif running >> 4 in (0xC, 0xD):
                position += 1
            else:
                position += 2
    seconds, last, tempo = 0.0, 0, 500000
    for at, value in sorted(tempos, key=lambda t: t[0]):
        seconds += (min(at, end) - last) * tempo / division / 1e6
        last, tempo = min(at, end), value
    return seconds + (end - last) * tempo / division / 1e6


def test_generated_scores_are_valid_and_cover_every_structure():
    for seed, kind, feature in cases():
        score = make_score(seed, kind, feature)
        score.validate()
        assert score.rhythm_error() is None and score.edit_error() is None, (seed, kind, feature, score.edit_error())
        assert not structure_problems(score), (seed, kind, feature, structure_problems(score)[:1])


@pytest.mark.integration
def test_preview_export_and_play_map_agree(tmp_path):
    lilypond = find_tool("lilypond")
    if not lilypond:
        pytest.skip("JIANPU_LILYPOND not configured")

    def run(case):
        seed, kind, feature = case
        score = make_score(seed, kind, feature)
        order, structured = playing_order(score)
        assert structured
        folder = Path(tmp_path, f"{seed}-{kind}-{feature}").resolve()
        result = render(Project(score).notation(), folder, lilypond.resolve(), threading.Event(), create_pdf=False)
        problems = _compare(score, preview_midi(score, order), result.midi.read_bytes(), order)
        return f"{seed} {kind} {feature}: " + "; ".join(problems) if problems else None

    workers = max(2, min(8, (os.cpu_count() or 2) - 1))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        failures = [f for f in pool.map(run, cases()) if f]
    assert not failures, f"{len(failures)} of {len(cases())} differ:\n" + "\n".join(failures)
