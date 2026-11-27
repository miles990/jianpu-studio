"""Registry-driven edits for a palette: is a symbol applicable here, apply it, remove it, parts.

All functions edit a ``Score`` in place and are meant to run inside the editor's
``mutate(update)`` so one call is one undo step and the result is validated
(``Score.validate`` / ``notation_error``) before it is accepted. ``rows`` are note rows of
``part``; bar items always live on part 0 at the same bar, whichever part is edited.
"""
from __future__ import annotations

from dataclasses import replace

from . import symbols
from .i18n import tr
from .migrate import add_mark
from .model import MAX_PARTS, Part, Score


def _rows(score: Score, part: int, rows) -> list[int]:
    notes = score.parts[part].notes
    return sorted({r for r in rows if 0 <= r < len(notes)})


def symbol_problem(score: Score, part: int, rows, symbol_id: str) -> str | None:
    """Why ``symbol_id`` cannot be applied to the selection (a sentence), or None if it can."""
    symbol = symbols.SYMBOLS.get(symbol_id)
    if symbol is None:
        return tr("不支援的記號或放錯位置：{id}").format(id=symbol_id)
    rows = _rows(score, part, rows)
    if not rows:
        return tr("請先選取要加記號的音符。")
    notes = score.parts[part].notes
    storage = symbol.storage
    if storage == "tie":
        return tr("延音線請用「連線／反覆」中的同音延音線，兩端音高須相同。")
    if storage == "tuplet":
        return tr("三連音請選一個音符後用「拆成三連音」。")
    if storage == "composite":
        return tr("這個記號由倚音和滑音組成，請分別設定倚音與滑音。")
    if storage == "marks":
        for row in rows:
            note = notes[row]
            if note.degree == 0 and not symbol.on_rest:
                return tr("休止符不能加「{name}」。").format(name=symbol.display_name)
            if note.grace_after and symbol.slot not in ("breath", "slide"):
                return tr("有後倚音的音符只能再加換氣或滑音記號。")
        return None
    if storage == "spans":
        if rows != list(range(rows[0], rows[-1] + 1)) or len(rows) < 2:
            return tr("線條記號需連續選取兩個以上的音符。")
        if symbol.family == "gliss" and len(rows) != 2:
            return tr("大滑音請選取相鄰的兩個音符。")
        for row in (rows[0], rows[-1]):
            if notes[row].degree == 0 and not symbol.on_rest:
                return tr("休止符不能加「{name}」。").format(name=symbol.display_name)
            if notes[row].grace_after:
                return tr("有後倚音的音符只能再加換氣或滑音記號。")
        return None
    return None   # bar items: placed at the selected bars' boundary in part 0


def _bars_of(score: Score, part: int, rows) -> tuple[int, int]:
    position, starts = 0, []
    for note in score.parts[part].notes:
        starts.append(position)
        position += note.ticks
    bars = [score.bar_index(starts[r]) for r in rows]
    return min(bars), max(bars)


def apply_symbol(score: Score, part: int, rows, symbol_id: str) -> None:
    """Add a symbol to the selection; raises ValueError with the reason when it cannot."""
    if problem := symbol_problem(score, part, rows, symbol_id):
        raise ValueError(problem)
    rows = _rows(score, part, rows)
    symbol = symbols.SYMBOLS[symbol_id]
    notes = score.parts[part].notes
    if symbol.storage == "marks":
        for row in rows:
            notes[row] = replace(notes[row], marks=add_mark(notes[row].marks, symbol_id))
    elif symbol.storage == "spans":
        remove_lines(score, part, rows, symbol.family)
        start, end = rows[0], rows[-1]
        notes[start] = replace(notes[start], spans_start=symbols.sort_ids([*notes[start].spans_start, symbol_id]))
        notes[end] = replace(notes[end], spans_end=symbols.sort_ids([*notes[end].spans_end, symbol_id]))
    else:
        first, last = _bars_of(score, part, rows)
        if symbol_id in ("bar.repeat_start", "bar.repeat_end") and first != last:
            set_bar_item(score, first, "bar.repeat_start")
            set_bar_item(score, last, "bar.repeat_end")
        else:
            set_bar_item(score, first if symbol.bar_edge == "start" else last, symbol_id)


def remove_symbol(score: Score, part: int, rows, symbol_id: str) -> None:
    """Remove a symbol from the selection (a whole line for spans, the bar item for bar symbols)."""
    rows = _rows(score, part, rows)
    symbol = symbols.SYMBOLS[symbol_id]
    notes = score.parts[part].notes
    if symbol.storage == "marks":
        for row in rows:
            notes[row] = replace(notes[row], marks=tuple(m for m in notes[row].marks if m != symbol_id))
    elif symbol.storage == "spans":
        remove_lines(score, part, rows, symbol.family)
    elif symbol.storage == "bar" and rows:
        first, last = _bars_of(score, part, rows)
        for bar in range(first, last + 1):
            set_bar_item(score, bar, symbol_id, present=False)


def remove_lines(score: Score, part: int, rows, family: str) -> None:
    """Remove every line of ``family`` that touches the selected rows, both ends."""
    notes = score.parts[part].notes
    selected = set(rows)
    open_rows, spans = {}, []
    for row, note in enumerate(notes):
        for sid in note.spans_end:
            if symbols.SYMBOLS[sid].family == family:
                spans.append((open_rows.pop(family, row), row))
        for sid in note.spans_start:
            if symbols.SYMBOLS[sid].family == family:
                open_rows[family] = row
    spans += [(row, row) for row in open_rows.values()]
    for start, end in spans:
        if any(start <= row <= end for row in selected):
            for row, name in ((start, "spans_start"), (end, "spans_end")):
                notes[row] = replace(notes[row], **{name: tuple(
                    s for s in getattr(notes[row], name) if symbols.SYMBOLS[s].family != family)})


def set_bar_item(score: Score, bar: int, symbol_id: str, present: bool = True) -> None:
    """Put (or take away) a bar item at bar ``bar`` of part 0 (start or end edge per registry)."""
    symbol = symbols.SYMBOLS[symbol_id]
    if symbol.kind != "bar":
        raise ValueError(tr("不支援的記號或放錯位置：{id}").format(id=symbol_id))
    ranges = score.part_bars(0)
    if not 0 <= bar < len(ranges):
        raise ValueError(tr("選取的小節不存在。"))
    start, end = ranges[bar]
    row, name = (start, "bar_start") if symbol.bar_edge == "start" else (end - 1, "bar_end")
    notes = score.parts[0].notes
    items = tuple(s for s in getattr(notes[row], name) if s != symbol_id)
    notes[row] = replace(notes[row], **{name: symbols.sort_ids([*items, symbol_id]) if present else items})


# ---------------------------------------------------------------- parts
def add_part(score: Score, name: str = "") -> int:
    """Append a part of rests with part 0's bars; returns its index."""
    if len(score.parts) >= MAX_PARTS:
        raise ValueError(tr("樂譜須有 1 到 4 個聲部。"))
    notes, position = [], 0
    for start, end in score.part_bars(0):
        length = sum(n.ticks for n in score.parts[0].notes[start:end])
        notes += score.rests_between(position, position + length)
        position += length
    score.parts.append(Part(name, notes))
    return len(score.parts) - 1


def remove_part(score: Score, index: int) -> None:
    """Remove a part other than the first (part 0 holds the bar lines, repeats and jumps)."""
    if index == 0 or not 0 <= index < len(score.parts):
        raise ValueError(tr("第一聲部含小節線與反覆記號，不能刪除；可刪除其他聲部。"))
    del score.parts[index]

