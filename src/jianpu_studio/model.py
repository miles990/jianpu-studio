"""Editable score (project file v2): notes carry registry marks; a score has parts and a pickup.

Fractions avoid rhythm rounding errors. Notes are immutable and hashable, so undo history
can share them. The 0.3.x note fields (finger, bow, ...) remain as read-only views and as
keyword arguments of ``Note``/``dataclasses.replace``; see ``migrate.py``.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field, fields, replace
from fractions import Fraction
from functools import lru_cache
from pathlib import Path

from . import i18n, migrate, symbols
from .i18n import N_, tr

KEYS = ("C", "G", "D", "A", "E", "B", "F#", "F", "Bb", "Eb", "Ab", "Db", "Gb")


def key_label(key: str) -> str:
    """Jianpu writes the accidental before the letter: 「♭B」, 「♯F」."""
    if len(key) == 2 and key[1] in "#b":
        return ("♯" if key[1] == "#" else "♭") + key[0]
    return key
METERS = ("4/4", "3/4", "2/4", "6/8", "3/8", "2/2", "9/8", "12/8")
DURATIONS = {1: N_("全音符 · 4 拍"), 2: N_("二分 · 2 拍"), 4: N_("四分 · 1 拍"), 8: N_("八分 · ½ 拍"),
             16: N_("十六分 · ¼ 拍"), 32: N_("三十二分 · 1/8 拍")}  # values are catalog sources; show tr(value)
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_NOTES = 10000
MAX_PARTS = 4
MAX_NOTE_TEXT = 24
FORMAT_VERSION = 2
TICKS_PER_QUARTER = 48
GRACE_PATTERN = r"(?:[#b]?[1-7](?:'{1,2}|,{1,2})?)"
PRINT_VARIANTS = ("tc", "sc")
ID_FIELDS = ("marks", "spans_start", "spans_end", "bar_start", "bar_end")


def text_value(value: object, name: str, limit: int = 200) -> str:
    if (not isinstance(value, str) or len(value) > limit
            or any(ord(c) < 32 or c in "\x85  " for c in value)):
        raise ValueError(tr("{name}須為單行文字，最多 {limit} 字。").format(name=name, limit=limit))
    return value


def _ids(value):
    return symbols.sort_ids(value) if isinstance(value, (list, tuple)) else value


@dataclass(frozen=True, init=False)
class Note:
    degree: int = 1
    octave: int = 0
    duration: int = 4
    dotted: bool = False
    accidental: str = ""
    tie: bool = False                    # structural: same pitch follows; MIDI merges the notes
    tuplet: int = 0
    tuplet_start: bool = False
    tuplet_end: bool = False
    grace_before: str = ""
    grace_after: str = ""
    marks: tuple[str, ...] = ()          # point/pre symbol ids, registry order, unique
    spans_start: tuple[str, ...] = ()    # line ids starting on this note
    spans_end: tuple[str, ...] = ()      # line ids ending on this note (paired by family)
    bar_start: tuple[str, ...] = ()      # bar items, parts[0] only, first note of a bar
    bar_end: tuple[str, ...] = ()        # bar items, parts[0] only, last note of a bar
    text: str = ""                       # free text above the note: 中速、深情地、引子… (print + canvas)

    def __init__(self, degree=1, octave=0, duration=4, dotted=False, accidental="", *, tie=False, tuplet=0,
                 tuplet_start=False, tuplet_end=False, grace_before="", grace_after="", marks=(), spans_start=(),
                 spans_end=(), bar_start=(), bar_end=(), text="", **legacy):
        values = dict(degree=degree, octave=octave, duration=duration, dotted=dotted, accidental=accidental, tie=tie,
                      tuplet=tuplet, tuplet_start=tuplet_start, tuplet_end=tuplet_end, grace_before=grace_before,
                      grace_after=grace_after, marks=_ids(marks), spans_start=_ids(spans_start),
                      spans_end=_ids(spans_end), bar_start=_ids(bar_start), bar_end=_ids(bar_end),
                      text=text)
        if legacy:
            for name in ID_FIELDS:
                if not isinstance(values[name], tuple):
                    raise ValueError(tr("記號資料格式不正確。"))
            migrate.apply_legacy(values, legacy)
        for name, value in values.items():
            object.__setattr__(self, name, value)

    # ---- 0.3.x read-only views (writes go through Note(...)/replace(note, finger=...))
    finger = property(lambda self: migrate.legacy_value(self, "finger"))
    bow = property(lambda self: migrate.legacy_value(self, "bow"))
    dynamic = property(lambda self: migrate.legacy_value(self, "dynamic"))
    articulation = property(lambda self: migrate.legacy_value(self, "articulation"))
    slide = property(lambda self: migrate.legacy_value(self, "slide"))
    slur_start = property(lambda self: migrate.legacy_value(self, "slur_start"))
    slur_end = property(lambda self: migrate.legacy_value(self, "slur_end"))
    repeat_start = property(lambda self: migrate.legacy_value(self, "repeat_start"))
    repeat_end = property(lambda self: migrate.legacy_value(self, "repeat_end"))
    hairpin = property(lambda self: migrate.legacy_value(self, "hairpin"))
    hairpin_end = property(lambda self: migrate.legacy_value(self, "hairpin_end"))

    @property
    def has_marks(self) -> bool:
        return any(getattr(self, name) for name in ID_FIELDS)

    def validate(self) -> None:
        if type(self.degree) is not int or not 0 <= self.degree <= 7:
            raise ValueError(tr("音高必須是 0（休止）到 7。"))
        if type(self.octave) is not int or self.octave not in range(-2, 3):
            raise ValueError(tr("八度範圍為低兩個到高兩個八度。"))
        if type(self.duration) is not int or self.duration not in DURATIONS:
            raise ValueError(tr("不支援這個音符時值。"))
        if type(self.dotted) is not bool or self.accidental not in ("", "#", "b", "n"):
            raise ValueError(tr("無效的附點或升降記號。"))
        if self.degree == 0 and (self.octave or self.accidental):
            raise ValueError(tr("休止符沒有八度或升降記號。"))
        if any(type(getattr(self, name)) is not bool for name in ("tie", "tuplet_start", "tuplet_end")):
            raise ValueError(tr("連線或反覆格式不正確。"))
        if type(self.tuplet) is not int or self.tuplet not in (0, 3):
            raise ValueError(tr("目前支援三連音，每組三個等時值音符。"))
        if self.tuplet and (self.dotted or self.duration not in (4, 8, 16, 32)):
            raise ValueError(tr("三連音支援未附點的四分、八分、十六分或三十二分音符。"))
        if not self.tuplet and (self.tuplet_start or self.tuplet_end):
            raise ValueError(tr("三連音起終點須屬於三連音群組。"))
        for grace in (self.grace_before, self.grace_after):
            if not isinstance(grace, str) or (grace and re.fullmatch(GRACE_PATTERN + "{1,8}", grace) is None):
                raise ValueError(tr("倚音須為 1–8 個音（1–7），可加 #、b 及最多兩個八度後綴，例如 23 或 #4'5。"))
        self._validate_ids()
        if not isinstance(self.text, str) or len(self.text) > MAX_NOTE_TEXT or any(
                ord(c) < 32 or c in "\x85\u2028\u2029" for c in self.text) or self.text != self.text.strip():
            raise ValueError(tr("音符上方的文字須為一行，最多 {limit} 字。").format(limit=MAX_NOTE_TEXT))
        rest = self.degree == 0
        if rest and (self.tie or any(not symbols.SYMBOLS[m].on_rest and m not in symbols.LEGACY_REST_MARKS
                                     for m in self.marks)
                     or any(symbols.SYMBOLS[s].family not in ("hairpin", "ottava", "text")
                            for s in self.spans_start + self.spans_end)):
            raise ValueError(tr("休止符不能加指法、弓法、滑音或連線。"))
        if rest and (self.grace_before or self.grace_after):
            raise ValueError(tr("休止符不能加倚音。"))
        if self.grace_after and (self.tie or self.spans_start or self.spans_end
                                 or any(symbols.SYMBOLS[m].slot not in ("breath", "slide") for m in self.marks)):
            # Upstream places these post-events outside afterGrace's main
            # note, or moves a tie onto the last ornament. Refuse that precise
            # combination instead of printing detached marks or wrong MIDI.
            raise ValueError(tr("後倚音目前不能與同一主音的指法、弓法、力度、演奏記號或連線端點並用；"
                                "這個組合尚不支援，請分開設定或保留原譜。換氣記號可並用。"))

    def _validate_ids(self) -> None:
        for name in ID_FIELDS:
            value = getattr(self, name)
            if not isinstance(value, tuple) or any(not isinstance(i, str) for i in value) or len(set(value)) != len(value):
                raise ValueError(tr("記號資料格式不正確。"))
            for ident in value:
                symbol = symbols.SYMBOLS.get(ident)
                fits = symbol is not None and (
                    (name == "marks" and symbol.kind in ("point", "pre") and symbol.storage == "marks")
                    or (name.startswith("spans") and symbol.storage == "spans")
                    or (name == "bar_start" and symbol.bar_edge == "start")
                    or (name == "bar_end" and symbol.bar_edge == "end"))
                if not fits:
                    raise ValueError(tr("不支援的記號或放錯位置：{id}").format(id=ident))
        slots = {}
        for mark in self.marks:
            slot = symbols.SYMBOLS[mark].slot
            if slot and slot in slots:
                raise ValueError(tr("「{a}」與「{b}」不能同時加在一個音符上。").format(
                    a=symbols.SYMBOLS[slots[slot]].display_name, b=symbols.SYMBOLS[mark].display_name))
            slots[slot] = mark
        for name in ("spans_start", "spans_end"):
            families = [symbols.SYMBOLS[s].family for s in getattr(self, name)]
            if len(set(families)) != len(families):
                raise ValueError(tr("同一個音符不能有兩條同類的線條起點或終點。"))

    @property
    def beats(self) -> Fraction:
        return Fraction(self.ticks, TICKS_PER_QUARTER)

    @property
    def ticks(self) -> int:
        """Exact units for both dotted 32nds and undotted 3:2 tuplets."""
        ticks = (4 * TICKS_PER_QUARTER // self.duration) * (3 if self.dotted else 2) // 2
        return ticks * 2 // 3 if self.tuplet == 3 else ticks

    def token(self) -> str:
        self.validate()
        # Upstream's unaltered degree cancels a previous accidental in the same
        # measure and lets LilyPond print a natural where needed. It has no 'n'
        # token for a redundant cautionary natural on an already natural note.
        accidental = "" if self.accidental == "n" else self.accidental
        pitch = accidental + str(self.degree) + ("'" * self.octave if self.octave >= 0 else "," * -self.octave)
        if self.duration <= 2:
            nominal_beats = Fraction(4, self.duration) * (Fraction(3, 2) if self.dotted else 1)
            # jianpu writes a long rest as one 0 per beat ("0 0 0 0"), never "0 - - -".
            return " ".join([pitch] + ["0" if self.degree == 0 else "-"] * (int(nominal_beats) - 1))
        return {4: "c", 8: "q", 16: "s", 32: "d"}[self.duration] + pitch + ("." if self.dotted else "")

    def notation_token(self) -> str:
        """This note's jianpu-ly words on their own (no bar context), e.g. for the clipboard text."""
        from .export import note_words
        return " ".join(note_words(self, {}))

    def to_dict(self) -> dict:
        """Compact form: the pitch, then only fields that differ from the defaults."""
        out = {}
        for f in fields(self):
            value = getattr(self, f.name)
            if value != f.default or f.name == "degree":
                out[f.name] = list(value) if isinstance(value, tuple) else value
        return out

    @classmethod
    def from_dict(cls, data: dict) -> Note:
        if not isinstance(data, dict) or set(data) - {f.name for f in fields(cls)}:
            raise TypeError("unexpected note fields")
        return cls(**data)


def tuplet_ranges(notes: list[Note], where=None) -> list[tuple[int, int]]:
    """Validate 3:2 groups and return zero-based, end-exclusive note ranges.

    This helper checks group structure independently of meter. A caller can
    use the ranges to reject copy/delete/duration edits that split a group.
    ``where(row)`` names a note's place (「第 3 小節第 2 拍」) for the message.
    """
    from .notation_rules import LocatedError
    where = where or (lambda row: tr("這段音符中"))
    ranges, row = [], 0
    while row < len(notes):
        note = notes[row]
        if not note.tuplet:
            if note.tuplet_start or note.tuplet_end:
                raise LocatedError(tr("{where}的三連音端點沒有群組。").format(where=where(row)), row)
            row += 1
            continue
        group = notes[row:row+3]
        if (len(group) != 3 or not note.tuplet_start
                or any(n.tuplet != 3 or n.dotted or n.duration not in (4, 8, 16, 32) for n in group)
                or len({n.duration for n in group}) != 1
                or any(n.tuplet_start != (i == 0) or n.tuplet_end != (i == 2) for i, n in enumerate(group))):
            raise LocatedError(tr("{where}的三連音不完整；請選取完整的三個等時值、未附點音符。").format(where=where(row)), row)
        ranges.append((row, row+3))
        row += 3
    return ranges


@lru_cache(maxsize=32)
def _bar_ticks(meter: str) -> int:
    """Ticks per bar (hot: every bar-boundary test asks), exact integer arithmetic."""
    numerator, denominator = meter.split("/")
    return int(numerator) * 4 * TICKS_PER_QUARTER // int(denominator)


def _place(score, part, row) -> str:
    from .notation_rules import note_place
    return note_place(score, part, row, with_part=True)


def pickup_choices(bar_ticks: int) -> list[int]:
    """Pickup lengths upstream can write (``4/4,8`` / ``3/4,4.``): one plain or dotted value."""
    out = []
    for duration in DURATIONS:
        base = 4 * TICKS_PER_QUARTER // duration
        out += [t for t in (base, base * 3 // 2) if 0 < t < bar_ticks]
    return sorted(set(out))


def rest_fill(ticks: int) -> list[Note]:
    """Shortest exact rest decomposition (longest first), including odd dotted-32nd ticks."""
    choices = [Note(0, duration=d, dotted=dot) for d in DURATIONS for dot in (True, False)]
    fills: list[list[Note] | None] = [[]] + [None] * ticks
    for length in range(1, ticks + 1):
        candidates = [fills[length - n.ticks] + [n] for n in choices
                      if n.ticks <= length and fills[length - n.ticks] is not None]
        if candidates:
            fills[length] = min(candidates, key=len)
    if fills[ticks] is None:
        raise ValueError(tr("剩餘拍數無法用目前支援的休止符補齊，請調整附點三十二分音符。"))
    return list(reversed(fills[ticks]))


@dataclass
class Part:
    name: str = ""
    notes: list[Note] = field(default_factory=list)
    lyrics: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "lyrics": self.lyrics, "notes": [n.to_dict() for n in self.notes]}


def _default_variant() -> str:
    return "sc" if i18n.current_language() == i18n.SIMPLIFIED else "tc"


@dataclass(init=False)
class Score:
    title: str
    composer: str
    key: str
    meter: str
    tempo: int
    with_staff: bool
    paper_size: str
    staff_size: int
    bars_per_line: int
    pickup: int                # pickup length in ticks (48 per quarter); 0 = none
    print_variant: str         # technique text printed in Traditional ("tc") or Simplified ("sc")
    parts: list[Part]

    def __init__(self, title=None, composer="", key="C", meter="4/4", tempo=96, with_staff=False, paper_size="a4",
                 staff_size=20, bars_per_line=0, pickup=0, print_variant=None, parts=None, *, notes=None, lyrics=None):
        self.title = tr("未命名樂曲") if title is None else title
        self.composer, self.key, self.meter, self.tempo = composer, key, meter, tempo
        self.with_staff, self.paper_size, self.staff_size = with_staff, paper_size, staff_size
        self.bars_per_line, self.pickup = bars_per_line, pickup
        self.print_variant = _default_variant() if print_variant is None else print_variant
        # New Part objects (sharing note lists, like 0.3.x replace()) so a changed copy
        # never edits the original's parts.
        self.parts = [replace(p) if isinstance(p, Part) else p for p in parts] if parts else [Part()]
        if notes is not None:
            self.parts[0] = replace(self.parts[0], notes=notes)
        if lyrics is not None:
            self.parts[0] = replace(self.parts[0], lyrics=lyrics)

    # ---- part 0 as the 0.3.x single voice
    @property
    def notes(self) -> list[Note]:
        return self.parts[0].notes

    @notes.setter
    def notes(self, value: list[Note]) -> None:
        self.parts[0].notes = value

    @property
    def lyrics(self) -> str:
        return self.parts[0].lyrics

    @lyrics.setter
    def lyrics(self, value: str) -> None:
        self.parts[0].lyrics = value

    def copy(self) -> Score:
        """Independent part/note lists; notes themselves are immutable and shared."""
        return replace(self, parts=[replace(p, notes=list(p.notes)) for p in self.parts])

    # ---- bars
    @property
    def bar_length(self) -> Fraction:
        numerator, denominator = self.meter.split("/")
        return Fraction(int(numerator) * 4, int(denominator))

    @property
    def bar_ticks(self) -> int:
        return _bar_ticks(self.meter)

    @property
    def first_bar_ticks(self) -> int:
        return self.pickup or self.bar_ticks

    def bar_index(self, position: int) -> int:
        """Zero-based bar holding the tick ``position`` (bar 0 is the pickup when there is one)."""
        first = self.first_bar_ticks
        return 0 if position < first else 1 + (position - first) // self.bar_ticks

    def bar_room(self, position: int) -> tuple[int, int]:
        """(offset inside its bar, length of that bar) for a tick position."""
        first = self.first_bar_ticks
        return (position, first) if position < first else ((position - first) % self.bar_ticks, self.bar_ticks)

    def is_boundary(self, position: int) -> bool:
        return self.bar_room(position)[0] == 0

    def part_bars(self, index: int = 0) -> list[tuple[int, int]]:
        """Note ranges (start, end-exclusive) of each bar of one part; an unfinished last bar counts."""
        ranges, start, position = [], 0, 0
        notes = self.parts[index].notes
        for row, note in enumerate(notes):
            position += note.ticks
            if self.is_boundary(position):
                ranges.append((start, row + 1))
                start = row + 1
        if start < len(notes):
            ranges.append((start, len(notes)))
        return ranges

    def final_bar_targets(self, total: int) -> list[int]:
        """Allowed end positions >= total for the last bar (pickup: bar - pickup, or a full bar)."""
        if not self.pickup:
            return [total + (-total) % self.bar_ticks]
        if total <= self.pickup:
            return [self.pickup + self.bar_ticks - self.pickup]
        offset = (total - self.pickup) % self.bar_ticks
        base = total - offset
        short = base + self.bar_ticks - self.pickup
        return sorted({t for t in (short, base + self.bar_ticks) if t >= total} | ({total} if offset == 0 else set()))

    # ---- validation
    def validate(self) -> None:
        text_value(self.title, tr("曲名"))
        text_value(self.composer, tr("作曲者"))
        if self.key not in KEYS or self.meter not in METERS:
            raise ValueError(tr("不支援此調號或拍號。"))
        if type(self.tempo) is not int or not 20 <= self.tempo <= 300:
            raise ValueError(tr("速度須介於每分鐘 20 到 300 拍。"))
        if type(self.with_staff) is not bool:
            raise ValueError(tr("樂譜格式不正確或音符太多（上限 10,000）。"))
        if self.paper_size not in ("a4", "letter") or type(self.staff_size) is not int or not 16 <= self.staff_size <= 30:
            raise ValueError(tr("紙張須為 A4 / Letter，譜字大小須為 16–30。"))
        if type(self.bars_per_line) is not int or not 0 <= self.bars_per_line <= 8:
            raise ValueError(tr("每行小節數須為自動（0）到 8。"))
        if type(self.pickup) is not int or (self.pickup and self.pickup not in pickup_choices(self.bar_ticks)):
            raise ValueError(tr("弱起長度須為單一時值或附點時值，且短於一小節。"))
        if self.print_variant not in PRINT_VARIANTS:
            raise ValueError(tr("譜面文字須為繁體或簡體。"))
        if (not isinstance(self.parts, list) or not 1 <= len(self.parts) <= MAX_PARTS
                or any(not isinstance(p, Part) for p in self.parts)):
            raise ValueError(tr("樂譜須有 1 到 4 個聲部。"))
        if (any(not isinstance(p.notes, list) for p in self.parts)
                or sum(len(p.notes) for p in self.parts) > MAX_NOTES):
            raise ValueError(tr("樂譜格式不正確或音符太多（上限 10,000）。"))
        for index, part in enumerate(self.parts):
            text_value(part.name, tr("聲部名稱"), 40)
            text_value(part.lyrics, tr("歌詞"), 20000)
            for note in part.notes:
                if not isinstance(note, Note):
                    raise ValueError(tr("樂譜音符格式不正確。"))
                note.validate()
                if index and (note.bar_start or note.bar_end):
                    raise ValueError(tr("小節線、反覆與跳躍記號只能放在第一聲部。"))
            tuplet_ranges(part.notes, lambda row, index=index: _place(self, index, row))

    def rhythm_error(self) -> str | None:
        from .notation_rules import rhythm_error
        return rhythm_error(self)

    def notation_error(self) -> str | None:
        from .notation_rules import notation_error
        return notation_error(self)

    def edit_error(self) -> str | None:
        """notation_error() without the repeat/jump structure, which may be unfinished while editing."""
        from .notation_rules import edit_error
        return edit_error(self)

    def export_warnings(self) -> list[str]:
        """Things the export silently adjusts (shown to the user; the PDF is still correct)."""
        from .export import export_warnings
        return export_warnings(self)

    def needs_sloppy_final_bar(self) -> bool:
        """A pickup with a full final bar: upstream needs j2ly_sloppy_bars (tech.md §3.2e)."""
        if not self.pickup or not self.notes:
            return False
        total = sum(n.ticks for n in self.notes)
        return total > self.pickup and (total - self.pickup) % self.bar_ticks == 0

    def fill_last_bar(self) -> None:
        """Complete the last bar of every part with rests (to the next allowed length)."""
        for index, part in enumerate(self.parts):
            tuplet_ranges(part.notes, lambda row, index=index: _place(self, index, row))
        totals = [sum(n.ticks for n in p.notes) for p in self.parts]
        target = max(self.final_bar_targets(t)[0] for t in totals)
        fills = [self.rests_between(total, target) for total in totals]
        if sum(len(p.notes) for p in self.parts) + sum(map(len, fills)) > MAX_NOTES:
            raise ValueError(tr("音符太多（上限 10,000）。"))
        for part, fill in zip(self.parts, fills):
            part.notes.extend(fill)

    def rests_between(self, start: int, end: int) -> list[Note]:
        """Rests from tick ``start`` to ``end``, split at bar lines."""
        out, position = [], start
        while position < end:
            offset, length = self.bar_room(position)
            step = min(end, position - offset + length) - position
            out += rest_fill(step)
            position += step
        return out

    def to_jianpu(self, draft: bool = False, layout: bool = False) -> str:
        """``draft``: an unfinished repeat/jump structure is printed as plain signs instead of
        refusing (the user confirmed a draft print). ``layout``: with the line breaks planned
        for the page (``Project.notation``); without, the text holds only what the user set, so
        it stays comparable with a stored source."""
        self.validate()
        if not self.notes:
            raise ValueError(tr("請先加入音符。"))
        if error := self.rhythm_error():
            raise ValueError(error)
        if error := (self.edit_error() if draft else self.notation_error()):
            raise ValueError(error)
        from .export import to_jianpu
        return to_jianpu(self, draft, layout)

    def to_dict(self) -> dict:
        from .notation_rules import normalized_parts
        data = {f.name: getattr(self, f.name) for f in fields(self) if f.name != "parts"}
        data["parts"] = [p.to_dict() for p in normalized_parts(self)]
        return data

    @classmethod
    def from_dict(cls, raw: dict) -> Score:
        allowed = {f.name for f in fields(cls)}
        if not isinstance(raw, dict) or set(raw) - allowed or not isinstance(raw.get("parts"), list):
            raise TypeError("unexpected score fields")
        values = dict(raw)
        if len(values["parts"]) > MAX_PARTS or sum(len(p.get("notes", [])) if isinstance(p, dict) else 0
                                                   for p in values["parts"]) > MAX_NOTES:
            raise ValueError(tr("樂譜格式不正確或音符太多（上限 10,000）。"))
        parts = []
        for item in values["parts"]:
            if not isinstance(item, dict) or set(item) - {"name", "notes", "lyrics"} or not isinstance(item.get("notes", []), list):
                raise TypeError("unexpected part fields")
            parts.append(Part(item.get("name", ""), [Note.from_dict(n) for n in item.get("notes", [])], item.get("lyrics", "")))
        values["parts"] = parts
        values.setdefault("print_variant", "tc")
        return cls(**values)


@dataclass
class Project:
    score: Score = field(default_factory=Score)
    mode: str = "visual"
    source: str = ""

    def snapshot(self) -> Project:
        # Notes are immutable: history shares notes, never mutable part lists.
        return replace(self, score=self.score.copy())

    def to_dict(self) -> dict:
        return {"format": "jianpu-studio", "version": FORMAT_VERSION, "mode": self.mode, "source": self.source,
                "score": self.score.to_dict()}

    @classmethod
    def from_dict(cls, data: dict) -> Project:
        if (not isinstance(data, dict) or data.get("format") != "jianpu-studio"
                or type(data.get("version")) is not int or data.get("version") not in (1, FORMAT_VERSION)):
            raise ValueError(tr("不是支援的簡譜工房專案（需要格式版本 1 或 2）。"))
        if set(data) - {"format", "version", "mode", "source", "score"}:
            raise ValueError(tr("專案資料不完整或欄位格式錯誤。"))
        try:
            raw = data["score"]
            if data["version"] == 1:
                if not isinstance(raw, dict) or not isinstance(raw.get("notes"), list) or len(raw["notes"]) > MAX_NOTES:
                    raise ValueError(tr("樂譜格式不正確或音符太多（上限 10,000）。"))
                raw = migrate.v1_score_to_v2(raw)
            score = Score.from_dict(raw)
            score.validate()
            mode, source = data.get("mode", "visual"), data.get("source", "")
            if mode not in ("visual", "source") or not isinstance(source, str) or len(source) > MAX_FILE_BYTES:
                raise ValueError(tr("專案模式或原始碼不正確。"))
            return cls(score, mode, source)
        except (KeyError, TypeError, AttributeError) as exc:
            raise ValueError(tr("專案資料不完整或欄位格式錯誤。")) from exc

    def notation(self, draft: bool = False) -> str:
        """jianpu-ly text for the engine; visual scores carry their print options in a comment."""
        if self.mode != "visual":
            return self.source
        text = self.score.to_jianpu(draft, layout=True)
        options = render_options(self.score)
        if draft:
            from .i18n import current_language
            options["draft"] = current_language()         # the page says 「草稿」 in this language
        return (directive(options) + text) if options else text


# ``% jianpu-studio: print=sc sloppy-bars`` — read by worker.py; harmless to upstream (a comment).
DIRECTIVE = "% jianpu-studio:"


def render_options(score: Score) -> dict:
    options = {}
    if score.print_variant == "sc":
        options["print"] = "sc"
    if score.needs_sloppy_final_bar():
        options["sloppy-bars"] = True
    return options


def directive(options: dict) -> str:
    words = [f"{k}={v}" if v is not True else k for k, v in options.items()]
    return DIRECTIVE + " " + " ".join(words) + "\n"


def atomic_write(path: Path, text: str | bytes) -> None:
    """Replace only after flush succeeds; never truncate the user's old file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="." + path.name, suffix=".tmp", dir=path.parent)
    try:
        options = {} if isinstance(text, bytes) else {"encoding": "utf-8", "newline": "\n"}
        with os.fdopen(fd, "wb" if isinstance(text, bytes) else "w", **options) as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def project_text(project: Project) -> str:
    """The saved form: readable JSON with one note per line (keeps large scores well under the cap)."""
    data = project.to_dict()
    notes = []
    for index, part in enumerate(data["score"]["parts"]):
        notes.append(part["notes"])
        part["notes"] = f"\x00notes {index}\x00"       # a string a valid project can never contain
    text = json.dumps(data, ensure_ascii=False, indent=2)
    for index, part_notes in enumerate(notes):
        lines = ",\n".join(" " * 10 + json.dumps(n, ensure_ascii=False, separators=(", ", ": ")) for n in part_notes)
        body = "[\n" + lines + "\n" + " " * 8 + "]" if part_notes else "[]"
        text = text.replace(json.dumps(f"\x00notes {index}\x00"), body, 1)
    return text + "\n"


def parse_project_text(data: bytes | str) -> Project:
    """The one check every saved project passes: Open, recovery and Save (before writing) use it."""
    size = len(data.encode("utf-8") if isinstance(data, str) else data)
    if size > MAX_FILE_BYTES:
        raise ValueError(tr("專案檔太大（上限 4 MB）。"))
    if isinstance(data, bytes):
        data = data.decode("utf-8-sig")
    try:
        raw = json.loads(data)
    except json.JSONDecodeError as exc:
        raise ValueError(tr("專案資料不完整或欄位格式錯誤。")) from exc
    return Project.from_dict(raw)


def save_project(path: Path, project: Project) -> None:
    """Write only what ``load_project`` accepts back unchanged; otherwise keep the old file."""
    text = project_text(project)
    try:
        reloaded = parse_project_text(text)
    except ValueError as exc:
        raise ValueError(tr("這份樂譜無法安全儲存，原檔未被覆蓋：{detail}").format(detail=exc)) from exc
    if reloaded.to_dict() != project.to_dict():
        raise ValueError(tr("這份樂譜無法安全儲存，原檔未被覆蓋：{detail}").format(
            detail=tr("專案資料不完整或欄位格式錯誤。")))
    atomic_write(path, text)


def load_project(path: Path) -> Project:
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError(tr("專案檔太大（上限 4 MB）。"))
    return parse_project_text(path.read_bytes())


def demo_project() -> Project:
    notes = []
    for line in ("1 1 5 5 6 6 5-", "4 4 3 3 2 2 1-", "5 5 4 4 3 3 2-", "5 5 4 4 3 3 2-", "1 1 5 5 6 6 5-", "4 4 3 3 2 2 1-"):
        notes.extend(Note(int(token[0]), duration=2 if token.endswith("-") else 4) for token in line.split())
    return Project(Score(title=tr("小星星"), composer=tr("法國傳統旋律"), notes=notes))
