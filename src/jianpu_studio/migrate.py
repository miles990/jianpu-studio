"""0.3.x compatibility: the v1 note fields as views over registry marks, and v1 -> v2 files.

Every v1 field (finger, bow, dynamic, articulation, slide, slur_*, repeat_*, hairpin*) is a
*view* of the v2 data. Reading returns what a 0.3.x screen would show; writing replaces
only the value that view showed, so marks the old screens cannot see are never dropped.
"""
from __future__ import annotations

import re

from . import symbols
from .i18n import tr

LEGACY_FIELDS = ("finger", "bow", "dynamic", "articulation", "slide", "slur_start", "slur_end",
                 "repeat_start", "repeat_end", "hairpin", "hairpin_end")
DYNAMICS = ("ppp", "pp", "p", "mp", "mf", "f", "ff", "fff", "sfz")
ARTICULATIONS = {"staccato": "art.staccato", "tenuto": "art.tenuto", "accent": "art.accent",
                 "fermata": "art.fermata", "trill": "orn.trill", "mordent": "orn.mordent",
                 "breathe": "breath.comma", "turn": "orn.turn", "marcato": "art.marcato",
                 "staccatissimo": "art.staccatissimo", "harmonic": "harmonic"}
BOWS = {"upbow": "bow.up", "downbow": "bow.down"}
SLIDES = {"up": "slide.up", "down": "slide.down"}
HAIRPINS = {"crescendo": "hairpin.cresc", "diminuendo": "hairpin.dim"}
HAIRPIN_IDS = ("hairpin.cresc", "hairpin.dim", "hairpin.cresc_text", "hairpin.dim_text")


def legacy_value(note, name: str):
    """The value a 0.3.x screen shows for ``name``."""
    marks = note.marks
    if name == "finger":
        return next((int(m[7:]) for m in marks if m in ("finger.0", "finger.1", "finger.2", "finger.3", "finger.4")), -1)
    if name == "bow":
        return next((k for k, v in BOWS.items() if v in marks), "")
    if name == "dynamic":
        return next((d for d in DYNAMICS if f"dyn.{d}" in marks), "")
    if name == "articulation":
        return next((k for k, v in ARTICULATIONS.items() if v in marks), "")
    if name == "slide":
        return next((k for k, v in SLIDES.items() if v in marks), "")
    if name == "slur_start":
        return "line.slur" in note.spans_start
    if name == "slur_end":
        return "line.slur" in note.spans_end
    if name == "repeat_start":
        return "bar.repeat_start" in note.bar_start
    if name == "repeat_end":
        return "bar.repeat_end" in note.bar_end
    if name == "hairpin":
        found = next((s for s in note.spans_start if s in HAIRPIN_IDS), "")
        return "" if not found else "diminuendo" if found.startswith("hairpin.dim") else "crescendo"
    if name == "hairpin_end":
        return any(s in HAIRPIN_IDS for s in note.spans_end)
    raise AttributeError(name)


def _mark_for(name: str, value) -> str:
    """Registry id for a legacy value ("" = none); raises the 0.3.x message for bad values."""
    if name == "finger":
        if type(value) is not int or value not in range(-1, 5):
            raise ValueError(tr("二胡指法須為空弦 0 到 4 指。"))
        return f"finger.{value}" if value >= 0 else ""
    table = {"bow": BOWS, "slide": SLIDES}.get(name)
    if table is not None:
        if value not in ("", *table):
            raise ValueError(tr("不支援的弓法或滑音。"))
        return table.get(value, "")
    if name == "dynamic":
        if value not in ("", *DYNAMICS):
            raise ValueError(tr("不支援的力度。"))
        return f"dyn.{value}" if value else ""
    if name == "articulation":
        if value not in ("", *ARTICULATIONS):
            raise ValueError(tr("不支援的演奏記號。"))
        return ARTICULATIONS.get(value, "")
    raise AttributeError(name)


def _without(ids, removed) -> tuple:
    return tuple(i for i in ids if i not in removed)


def add_mark(marks, mark: str) -> tuple:
    """Add ``mark``; another mark in the same conflict slot is replaced."""
    slot = symbols.SYMBOLS[mark].slot if mark in symbols.SYMBOLS else ""
    kept = [m for m in marks if not (slot and m in symbols.SYMBOLS and symbols.SYMBOLS[m].slot == slot)]
    return symbols.sort_ids([*kept, mark])


def apply_legacy(values: dict, legacy: dict) -> None:
    """Apply legacy keyword changes onto ``values`` (marks/spans/bar tuples) in place.

    ``values`` holds the note's v2 fields; ``legacy`` maps v1 names to new values.
    """
    unknown = set(legacy) - set(LEGACY_FIELDS)
    if unknown:
        raise TypeError(f"unexpected note field(s): {', '.join(sorted(unknown))}")
    view = _View(values)
    for name, value in legacy.items():
        if name in ("finger", "bow", "dynamic", "articulation", "slide"):
            new = _mark_for(name, value)
            old = _mark_for(name, legacy_value(view, name))
            if new == old:
                continue
            marks = _without(values["marks"], {old})
            values["marks"] = add_mark(marks, new) if new else marks
            continue
        if name in ("hairpin",):
            if value not in ("", *HAIRPINS):
                raise ValueError(tr("不支援的漸強或漸弱記號。"))
            if legacy_value(view, "hairpin") == value:
                continue
            starts = _without(values["spans_start"], HAIRPIN_IDS)
            values["spans_start"] = symbols.sort_ids([*starts, HAIRPINS[value]]) if value else starts
            continue
        if type(value) is not bool:
            raise ValueError(tr("連線或反覆格式不正確。"))
        field, ident = {"slur_start": ("spans_start", "line.slur"), "slur_end": ("spans_end", "line.slur"),
                        "repeat_start": ("bar_start", "bar.repeat_start"), "repeat_end": ("bar_end", "bar.repeat_end"),
                        "hairpin_end": ("spans_end", "")}[name]
        if name == "hairpin_end":
            if value == legacy_value(view, name):
                continue
            # The exact end id is fixed up from the open start (Score.normalized / export pairing).
            ends = _without(values[field], HAIRPIN_IDS)
            values[field] = symbols.sort_ids([*ends, "hairpin.cresc"]) if value else ends
            continue
        current = values[field]
        values[field] = symbols.sort_ids([*current, ident]) if value else _without(current, {ident})


class _View:
    """Read-only legacy view over a dict of v2 note fields (used while constructing a Note)."""

    def __init__(self, values):
        self._values = values

    def __getattr__(self, name):
        return self._values[name]


# ---------------------------------------------------------------- v1 project files
V1_SCORE_FIELDS = {"title", "composer", "key", "meter", "tempo", "lyrics", "with_staff", "notes", "paper_size",
                   "staff_size", "bars_per_line"}
V1_NOTE_FIELDS = {"degree", "octave", "duration", "dotted", "accidental", "tie", "tuplet", "tuplet_start", "tuplet_end",
                  "grace_before", "grace_after", *LEGACY_FIELDS}


def v1_score_to_v2(raw: dict) -> dict:
    """Convert a v1 ``score`` object to v2 (tech.md §1.4). Raises KeyError/TypeError on bad shape."""
    if not isinstance(raw, dict) or set(raw) - V1_SCORE_FIELDS:
        raise TypeError("unexpected v1 score fields")
    raw = dict(raw)
    notes = raw.pop("notes")
    if not isinstance(notes, list):
        raise TypeError("notes")
    converted, open_hairpin = [], ""
    for item in notes:
        if not isinstance(item, dict) or set(item) - V1_NOTE_FIELDS:
            raise TypeError("unexpected v1 note fields")
        note = {k: v for k, v in item.items() if k not in LEGACY_FIELDS}
        marks, starts, ends, bar_start, bar_end = [], [], [], [], []
        finger = item.get("finger", -1)
        if finger != -1:
            mark = _mark_for("finger", finger)
            marks.append(mark)
        for name in ("bow", "dynamic", "articulation", "slide"):
            if item.get(name, ""):
                marks.append(_mark_for(name, item[name]))
        if item.get("slur_start"):
            starts.append("line.slur")
        if item.get("slur_end"):
            ends.append("line.slur")
        if item.get("repeat_start"):
            bar_start.append("bar.repeat_start")
        if item.get("repeat_end"):
            bar_end.append("bar.repeat_end")
        if item.get("hairpin_end"):
            ends.append(open_hairpin or "hairpin.cresc")
            open_hairpin = ""
        if item.get("hairpin", ""):
            if item["hairpin"] not in HAIRPINS:
                raise ValueError(tr("不支援的漸強或漸弱記號。"))
            starts.append(HAIRPINS[item["hairpin"]])
            open_hairpin = HAIRPINS[item["hairpin"]]
        for key, values in (("marks", marks), ("spans_start", starts), ("spans_end", ends),
                            ("bar_start", bar_start), ("bar_end", bar_end)):
            if values:
                note[key] = list(symbols.sort_ids(values))
        converted.append(note)
    lyrics = raw.pop("lyrics", "")
    raw.update(pickup=0, print_variant="tc", parts=[{"name": "", "lyrics": lyrics, "notes": converted}])
    return raw


# ---------------------------------------------------------------- v1 export text
_HEAD = re.compile(r"^[cqsd]?[#b]?[0-7][',]*\.?(?:///)?$")


def _v1_to_v2_words(text: str) -> list[list[str]]:
    """Apply the documented v1 -> v2 export differences to v1 text, one word list per line.

    * open string: upstream ``Fr=0`` (prints 宀) -> ``\\jsFingerZero`` (bold 0, JP-Word form)
    * ``\\sfz`` -> ``\\jsSfz`` (same print; MIDI accents one note instead of changing the level)
    * a repeat starting the piece prints its ``|:`` (``R{ \\bar ".|:"``, tech.md P6)
    """
    out = []
    for index, line in enumerate(text.strip().splitlines()):
        words = ["\\jsFingerZero" if w == "Fr=0" else "\\jsSfz" if w == "\\sfz" else w for w in line.split()]
        if words[:1] == ["R{"] and words[1:2] != ["\\bar"] and not any(_HEAD.match(w) for previous in out for w in previous):
            words[1:1] = ["\\bar", '".|:"']
        out.append(words)
    return out


def _segments(words: list[str]) -> list[tuple[str, list[str]]]:
    """(note head, sorted words up to the next head); the first entry holds words before any head."""
    out: list[tuple[str, list[str]]] = [("", [])]
    for word in words:
        if _HEAD.match(word):
            out.append((word, []))
        else:
            out[-1][1].append(word)
    return [(head, sorted(rest)) for head, rest in out]


def same_music_text(v1_text: str, v2_text: str) -> bool:
    """True when ``v2_text`` is the v2 export of the score that produced ``v1_text``.

    Besides the documented differences above, v2 orders a note's post-event words by the
    registry (e.g. ``\\mf \\downbow`` instead of ``\\downbow \\mf``); LilyPond output is
    the same. Such reordering never moves a word past a note head, so the words between
    two heads are compared as a multiset and the heads in order: a mark that moved to a
    different note (``1 \\mf 2`` vs ``1 2 \\mf``) is a different piece of music.
    """
    old, new = _v1_to_v2_words(v1_text), [line.split() for line in v2_text.strip().splitlines()]
    if len(old) != len(new):
        return False
    return all(_segments(a) == _segments(b) for a, b in zip(old, new))
