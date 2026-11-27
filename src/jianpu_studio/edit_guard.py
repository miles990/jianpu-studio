"""What an edit removes, so nothing disappears without being named first.

Every edit goes through ``EditActions.mutate``. Before it is applied, ``losses`` compares the
score before and after: notes, marks, line ends, ties, bar items, free text and grace notes
that are gone. What the command removes on purpose (the notes being deleted, the bars being
deleted, the mark being toggled off...) is ``explicit``; anything else is a loss the user is
told about -- count, kind and 小節/拍 -- before the edit happens, and can cancel.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from . import symbols
from .i18n import tr
from .notation_rules import bar_label, tick_place

LINE_KINDS = ("line_start", "line_end")
BAR_KINDS = ("bar_start", "bar_end")


@dataclass(frozen=True)
class Item:
    part: int
    kind: str          # note | mark | line_start | line_end | tie | bar_start | bar_end | text | grace_before | grace_after
    ident: str         # mark / bar item id, line family, text, grace notes ("" for notes and ties)
    tick: int          # where it is (before the edit)
    row: int
    name: str = field(default="", compare=False)    # what to call it (a line's symbol id, a note's pitch)

    @property
    def key(self) -> tuple[int, str, str]:
        return self.part, self.kind, self.ident


def _pitch(note) -> str:
    sign = {"#": "♯", "b": "♭", "n": "♮"}.get(note.accidental, "")
    octave = "'" * note.octave if note.octave > 0 else "," * -note.octave
    return sign + str(note.degree) + octave


def inventory(score) -> list[Item]:
    out = []
    for part, notes in enumerate(p.notes for p in score.parts):
        tick = 0
        for row, note in enumerate(notes):
            def add(kind, ident="", name=""):
                out.append(Item(part, kind, ident, tick, row, name))
            if note.degree:
                add("note", "", _pitch(note))
            for mark in note.marks:
                add("mark", mark)
            for sid in note.spans_start:
                add("line_start", symbols.SYMBOLS[sid].family, sid)
            for sid in note.spans_end:
                add("line_end", symbols.SYMBOLS[sid].family, sid)
            if note.tie:
                add("tie")
            for item in note.bar_start:
                add("bar_start", item)
            for item in note.bar_end:
                add("bar_end", item)
            if note.text:
                add("text", note.text)
            if note.grace_before:
                add("grace_before", note.grace_before)
            if note.grace_after:
                add("grace_after", note.grace_after)
            tick += note.ticks
    return out


def losses(before, after, explicit=None) -> list[Item]:
    """Items of ``before`` that ``after`` no longer has, apart from those ``explicit(item)`` names.

    Items are counted by (part, kind, id); which occurrence is reported prefers the ones that
    are no longer at the same tick."""
    old = inventory(before)
    new = inventory(after)
    missing = Counter(i.key for i in old) - Counter(i.key for i in new)
    if not missing:
        return []
    still = Counter((i.key, i.tick) for i in new)
    out = []
    for key, count in missing.items():
        candidates = [i for i in old if i.key == key]
        intended = [i for i in candidates if explicit is not None and explicit(i)]
        count -= len(intended)
        if count <= 0:
            continue
        others = [i for i in candidates if i not in intended]
        others.sort(key=lambda i: (still[(i.key, i.tick)] > 0, i.tick))
        out += others[:count]
    return sorted(out, key=lambda i: (i.part, i.tick, i.row, i.kind))


def subtract(items: list[Item], declared) -> list[Item]:
    """``items`` minus those already declared (by key and tick)."""
    left = Counter((i.key, i.tick) for i in declared or ())
    out = []
    for item in items:
        if left[(item.key, item.tick)]:
            left[(item.key, item.tick)] -= 1
        else:
            out.append(item)
    return out


def _what(item: Item, where: str) -> str:
    if item.kind == "note":
        return tr("{place}的音符 {pitch}").format(place=where, pitch=item.name)
    if item.kind == "mark":
        return tr("{place}的記號「{name}」").format(place=where, name=symbols.SYMBOLS[item.ident].display_name)
    if item.kind == "line_start":
        return tr("{place}的「{name}」起點").format(place=where, name=symbols.SYMBOLS[item.name].display_name)
    if item.kind == "line_end":
        return tr("{place}的「{name}」終點").format(place=where, name=symbols.SYMBOLS[item.name].display_name)
    if item.kind == "tie":
        return tr("{place}的延音線").format(place=where)
    if item.kind in BAR_KINDS:
        return tr("{place}的小節記號「{name}」").format(place=where, name=symbols.SYMBOLS[item.ident].display_name)
    if item.kind == "text":
        return tr("{place}的上方文字「{text}」").format(place=where, text=item.ident)
    if item.kind == "grace_before":
        return tr("{place}的前倚音「{grace}」").format(place=where, grace=item.ident)
    return tr("{place}的後倚音「{grace}」").format(place=where, grace=item.ident)


def place(score, item: Item) -> str:
    from .notation_rules import _part_label
    if item.kind in BAR_KINDS:
        where = bar_label(score, score.bar_index(item.tick))
    else:
        where = tick_place(score, item.tick)
    return (_part_label(score, item.part) if item.part < len(score.parts) else "") + where


def describe(score, items: list[Item], limit: int = 12) -> str:
    """「第 2 小節第 1 拍的音符 3；第 2 小節第 3 拍的「連線」的終點」 (at most ``limit`` entries)."""
    lines = [_what(i, place(score, i)) for i in items[:limit]]
    text = "；".join(lines)
    if len(items) > limit:
        text += tr("……另外還有 {count} 項").format(count=len(items) - limit)
    return text


def message(score, items: list[Item]) -> str:
    return tr("這項修改會移除 {count} 項：{items}。要繼續嗎？（之後也可以用復原還原）").format(
        count=len(items), items=describe(score, items))
