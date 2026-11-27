"""Registry-driven edits for a palette: is a symbol applicable here, apply it, remove it, parts.

All functions edit a ``Score`` in place and are meant to run inside the editor's
``mutate(update)`` so one call is one undo step and the result is validated
(``Score.validate`` / ``notation_error``) before it is accepted. ``rows`` are note rows of
``part``; bar items always live on part 0 at the same bar, whichever part is edited.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from . import symbols
from .i18n import N_, tr
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
        if symbol_id == "text.free":
            return None if len(rows) == 1 else tr("上方文字一次加在一個音上：請只選一個音符或休止符。")
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


# ---------------------------------------------------------------- one-click fixes
def apply_fix(score: Score, fix: tuple, part: int = 0, row: int | None = None) -> None:
    """Apply a problem's one-click fix (``notation_rules.Problem.fix``) to ``score`` in place.

    The status-bar button and ``notation_rules.fix_is_safe`` both call this, so a fix is
    checked exactly as it will be applied."""
    from .notation_rules import raw
    if fix == ("fill",):
        # Rests complete the last bar; the signs on the old final bar line stay on it.
        last = len(score.parts[0].notes) - 1
        ends = score.parts[0].notes[last].bar_end if last >= 0 else ()
        score.fill_last_bar()
        notes = score.parts[0].notes
        if ends and len(notes) - 1 != last:
            notes[last] = replace(notes[last], bar_end=())
            notes[-1] = replace(notes[-1], bar_end=symbols.sort_ids([*notes[-1].bar_end, *ends]))
        return
    if fix[0] == "add":
        _, bar, item = fix
        if item in BAR_STYLE_SLOT:
            for other in BAR_STYLE_SLOT - {item}:
                set_bar_item(score, bar, other, present=False)
        set_bar_item(score, bar, item)
        return
    notes = score.parts[part].notes
    if row is None or not 0 <= row < len(notes):
        raise ValueError(tr("選取的小節不存在。"))
    _remove_fix_item(score, part, row, fix)
    # Removing one end can leave its partner alone (a glissando to a far note, an overlapping
    # line): that partner goes in the same step, in the same part.
    with raw():
        for _ in range(4):
            again = score.notation_error()
            nxt = getattr(again, "fix", None) if again else None
            if not _same_line_fix(again, nxt, part, fix):
                break
            _remove_fix_item(score, part, again.row, nxt)


def _remove_fix_item(score: Score, part: int, row: int, fix: tuple) -> None:
    name, item = fix
    notes = score.parts[part].notes
    note = notes[row]
    if name == "tie":
        notes[row] = replace(note, tie=False)
    else:
        notes[row] = replace(note, **{name: tuple(i for i in getattr(note, name) if i != item)})


def _same_line_fix(problem, fix, part, first) -> bool:
    lines = ("spans_start", "spans_end")
    return bool(fix and problem.part == part and problem.row is not None and fix[0] in lines
                and first[0] in lines and symbols.SYMBOLS[fix[1]].family == symbols.SYMBOLS[first[1]].family)


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



# ---------------------------------------------------------------- palette targets (wow/design.md §2.5–§2.6)
SELECT_NONE, SELECT_NOTES, SELECT_BARS, SELECT_BARLINE = "none", "notes", "bars", "barline"
# Bar-end line styles share one place on the bar line: a new one replaces the old one.
BAR_STYLE_SLOT = frozenset({"bar.dashed", "bar.double", "bar.final", "bar.repeat_end", "bar.repeat_both"})
VOLTAS = ("volta.1", "volta.2", "volta.3", "volta.4")
_ORDINALS = {1: N_("一"), 2: N_("二"), 3: N_("三"), 4: N_("四")}  # catalog sources; show tr(value)


@dataclass(frozen=True)
class Target:
    """What the palette acts on: the editor's selection, part-relative."""
    kind: str = SELECT_NONE         # none | notes | bars | barline
    part: int = 0
    rows: tuple[int, ...] = ()      # selected rows of ``part`` (notes; for bars: every note of the bars)
    bars: tuple[int, ...] = ()      # selected bars (bars)
    barline: int = -1               # boundary k: between bar k-1 and bar k (barline)

    @property
    def contiguous(self) -> bool:
        return bool(self.rows) and list(self.rows) == list(range(self.rows[0], self.rows[-1] + 1))


@dataclass(frozen=True)
class SymbolState:
    available: bool
    reason: str = ""                # why not, and what to do (user language)
    present: int = 0                # how many targets already carry it
    total: int = 0
    replaces: tuple[str, ...] = ()  # display names that a click would replace
    removes: bool = False           # a click removes it (it is on every target)

    @property
    def all_present(self) -> bool:
        return self.total > 0 and self.present == self.total


def _positions(notes) -> list[int]:
    out, position = [], 0
    for note in notes:
        out.append(position)
        position += note.ticks
    return out


def _bar_of_rows(score: Score, part: int, rows) -> list[int]:
    positions = _positions(score.parts[part].notes)
    return sorted({score.bar_index(positions[r]) for r in rows if r < len(positions)})


def _note_place(score: Score, part: int, row: int) -> str:
    notes = score.parts[part].notes
    positions = _positions(notes)
    bar = score.bar_index(positions[row])
    first = next(r for r in range(row + 1) if score.bar_index(positions[r]) == bar)
    return tr("第 {bar} 小節第 {n} 音").format(bar=bar_number(score, bar), n=row - first + 1)


def _bar_label(score: Score, bar: int) -> str:
    return tr("弱起小節") if score.pickup and bar == 0 else tr("第 {bar} 小節").format(bar=bar + 1 - bool(score.pickup))


def bar_number(score: Score, bar: int) -> int:
    """Printed bar number: with a pickup the first full bar is 1 and the pickup is 0."""
    return bar + 1 - bool(score.pickup)


def _bar_targets(score: Score, target: Target, symbol) -> tuple[list[int], str]:
    """(bars, reason) for a bar item: the bars whose start/end edge receives it."""
    count = len(score.part_bars(0))
    if target.kind == SELECT_NONE or count == 0:
        return [], tr("請先點小節號或小節線。")
    if target.kind == SELECT_BARLINE:
        # An ending starts at the bar right of the line (like every start sign).
        bar = target.barline - 1 if symbol.bar_edge == "end" else target.barline
        if not 0 <= bar < count:
            return [], tr("這條小節線的這一側沒有小節；請點另一條小節線或小節號。")
        return [bar], ""
    if target.kind == SELECT_BARS:
        bars = sorted(target.bars)
    else:
        bars = _bar_of_rows(score, target.part, target.rows)
        if not target.contiguous and len(bars) > 1:
            return [], tr("請只選一段連續的小節。")
    if not bars:
        return [], tr("請先點小節號或小節線。")
    if bars != list(range(bars[0], bars[-1] + 1)):
        return [], tr("請只選一段連續的小節。")
    if bars[-1] >= count or bars[0] < 0:
        # Another part can be longer while it is being written; bar items live on part 1's bars.
        return [], tr("第一聲部還沒有這個小節；請先補齊各聲部的小節，再加小節記號。")
    if symbol.id in VOLTAS or symbol.id == "volta.final":
        return bars, ""
    if symbol.id in ("bar.repeat_start", "bar.repeat_end") and len(bars) > 1:
        return bars, ""
    return [bars[0] if symbol.bar_edge == "start" else bars[-1]], ""


def _bar_has(score: Score, bar: int, symbol_id: str) -> bool:
    ranges = score.part_bars(0)
    if not 0 <= bar < len(ranges):
        return False            # a bar the canvas shows but part 1 does not have (yet)
    start, end = ranges[bar]
    notes = score.parts[0].notes
    edge = notes[start].bar_start if symbols.SYMBOLS[symbol_id].bar_edge == "start" else notes[end - 1].bar_end
    return symbol_id in edge


def _volta_groups(score: Score) -> list[dict]:
    """Volta groups on part 0: [{"starts": {k: bar}, "end": bar}] in order."""
    from .notation_rules import bar_items
    groups, current = [], None
    for bar, (starts, ends) in enumerate(bar_items(score)):
        for item in starts:
            if item in VOLTAS:
                number = VOLTAS.index(item) + 1
                if number == 1 or current is None:
                    current = {"starts": {}, "end": None, "final": None}
                    groups.append(current)
                current["starts"][number] = bar
            if item == "volta.final" and current is not None:
                current["final"] = bar
        if "volta.end" in ends and current is not None:
            current["end"] = bar
            current = None
    return groups


def _place_volta(score: Score, first: int, last: int, symbol_id: str) -> None:
    """Put a volta where the user sees it printed; keep the internal form of tech.md §1.5.

    The first ending's ``:‖`` prints at its end, but is stored at the end of the repeat body
    (the bar before ``volta.1``); ``volta.end`` always closes the last ending. Endings and 結
    may be added in any order: a later ending (or 結) added first is stored, and
    ``notation_rules.structure_problems`` names the missing earlier ones.
    """
    if symbol_id == "volta.final":
        set_bar_item(score, first, "volta.final")
        return ""
    number = VOLTAS.index(symbol_id) + 1
    # An ending already right after this one (added earlier) continues the group: no 房子結束 here.
    joins = last + 1 < len(score.part_bars(0)) and any(
        VOLTAS.index(item) + 1 > number for item in _bar_starts(score, last + 1) if item in VOLTAS)
    if number == 1:
        if first == 0:
            raise ValueError(tr("第一結尾前面要有反覆的小節：請把第一結尾加在第 2 小節以後。"))
        notice = ""
        if _bar_has(score, first - 1, "bar.repeat_end"):
            pass
        elif _bar_has(score, last, "bar.repeat_end"):
            set_bar_item(score, last, "bar.repeat_end", present=False)
            set_bar_item(score, first - 1, "bar.repeat_end")
        else:
            # The :‖ prints at the end of the first ending; it goes back to ‖: (or the beginning).
            for other in BAR_STYLE_SLOT - {"bar.repeat_end"}:
                set_bar_item(score, first - 1, other, present=False)
            set_bar_item(score, first - 1, "bar.repeat_end")
            notice = tr("已一併加上反覆止 :‖：第一結尾奏完回到反覆起 ‖:（沒有反覆起時回到曲首）。")
        set_bar_item(score, first, "volta.1")
        if not joins:
            set_bar_item(score, last, "volta.end")
        return notice
    group = next((g for g in _volta_groups(score) if g["starts"].get(number - 1) is not None
                  and g["starts"][number - 1] < first), None)
    if group is not None:
        if group["end"] is not None and group["end"] != first - 1:
            if group["end"] >= first:
                raise ValueError(tr("第{n}結尾要放在前一個結尾之後的小節。").format(n=tr(_ORDINALS[number])))
            raise ValueError(tr("第{n}結尾要緊接在前一個結尾後面。").format(n=tr(_ORDINALS[number])))
        if group["end"] is not None:
            set_bar_item(score, group["end"], "volta.end", present=False)
        if group.get("final") is not None:
            set_bar_item(score, group["final"], "volta.final", present=False)
    set_bar_item(score, first, symbol_id)
    if not joins:
        set_bar_item(score, last, "volta.end")
    return ""


def _bar_starts(score: Score, bar: int) -> tuple[str, ...]:
    return score.parts[0].notes[score.part_bars(0)[bar][0]].bar_start


def g_last_start(group: dict) -> int:
    return group["starts"][max(group["starts"])]


def _remove_volta(score: Score, bar: int, symbol_id: str) -> None:
    if symbol_id == "volta.final":
        set_bar_item(score, bar, "volta.final", present=False)
        return
    number = VOLTAS.index(symbol_id) + 1
    group = next((g for g in _volta_groups(score) if g["starts"].get(number) == bar), None)
    if group is None:
        return
    if max(group["starts"]) != number:
        raise ValueError(tr("請先移除後面的結尾，再移除這一個。"))
    end = group["end"]
    set_bar_item(score, bar, symbol_id, present=False)
    if group.get("final") == bar:
        set_bar_item(score, bar, "volta.final", present=False)
    if end is not None:
        set_bar_item(score, end, "volta.end", present=False)
    if number > 1:
        if group["starts"].get(number - 1) is not None:
            set_bar_item(score, bar - 1, "volta.end")   # the previous ending is the last one now
    elif end is not None and bar > 0:
        # Back to the printed form: the :‖ returns to the end of the former first ending.
        set_bar_item(score, bar - 1, "bar.repeat_end", present=False)
        set_bar_item(score, end, "bar.repeat_end")


def _base_reason(score: Score, target: Target, symbol) -> str:
    """The selection rules of design §2.5, before any validation."""
    kind = symbol.storage
    line = kind in ("spans", "tie")
    if kind == "bar":
        return _bar_targets(score, target, symbol)[1]
    if target.kind == SELECT_NONE or not target.rows and target.kind != SELECT_BARLINE:
        if line:
            return tr("請先選一段音符（點第一個音，按住 Shift 再點最後一個）。")
        return tr("請先在譜上點一個音符。")
    if target.kind == SELECT_BARLINE:
        return tr("請點音符：線條要點第一個音，按住 Shift 再點最後一個。") if line else tr("請點音符。")
    if target.kind == SELECT_BARS and not line:
        return tr("目前選的是整個小節；請點小節裡的音符。")
    if kind == "tuplet":
        notes = score.parts[target.part].notes
        if len(target.rows) != 1 or notes[target.rows[0]].dotted or notes[target.rows[0]].tuplet \
                or notes[target.rows[0]].duration not in (2, 4, 8, 16):
            return tr("請選一個沒有附點的音，會拆成三等份。")
        return ""
    if kind == "composite":
        if symbol.id == "text.free":
            return "" if len(target.rows) == 1 else tr("上方文字一次加在一個音上：請只選一個音符或休止符。")
        return tr("贈音是後倚音：請在「選取」區按「＋倚音」設定。")
    if line:
        if not target.contiguous:
            return tr("選取不連續；線條需要一段連續的音。")
        if len(target.rows) < 2 and not (symbol.family == "gliss" or kind == "tie"):
            return tr("需要選取連續兩個以上的音符。")
    return ""


def _span_rows(score: Score, target: Target, symbol) -> tuple[int, int] | None:
    notes = score.parts[target.part].notes
    rows = sorted(target.rows)
    if len(rows) == 1:
        return (rows[0], rows[0] + 1) if rows[0] + 1 < len(notes) else None
    return rows[0], rows[-1]


def _present(score: Score, target: Target, symbol) -> tuple[int, int]:
    notes = score.parts[target.part].notes
    kind = symbol.storage
    if kind == "marks":
        rows = [r for r in target.rows if r < len(notes)]
        return sum(symbol.id in notes[r].marks for r in rows), len(rows)
    if kind == "tie":
        span = _span_rows(score, target, symbol)
        if span is None:
            return 0, 0
        rows = range(span[0], span[1])
        return sum(notes[r].tie for r in rows), len(rows)
    if kind == "tuplet":
        return 0, 1
    if kind == "spans":
        from .notation_rules import span_pairs
        span = _span_rows(score, target, symbol)
        if span is None:
            return 0, 1
        found = any(start == span[0] and end == span[1] and start_id == symbol.id
                    for _, start, end, start_id in span_pairs(notes))
        return int(found), 1
    if kind == "bar":
        bars, reason = _bar_targets(score, target, symbol)
        if reason or not bars:
            return 0, 0
        if symbol.id in VOLTAS:
            return int(any(g["starts"].get(VOLTAS.index(symbol.id) + 1) == bars[0] for g in _volta_groups(score))), 1
        if symbol.id in ("bar.repeat_start", "bar.repeat_end") and len(bars) > 1:
            return int(_bar_has(score, bars[0], "bar.repeat_start")) + int(_bar_has(score, bars[-1], "bar.repeat_end")), 2
        return sum(_bar_has(score, b, symbol.id) for b in bars), len(bars)
    return 0, 0


def _replaced(score: Score, target: Target, symbol) -> tuple[str, ...]:
    notes = score.parts[target.part].notes
    names = []
    if symbol.storage == "marks" and symbol.slot:
        for r in target.rows:
            for mark in notes[r].marks:
                other = symbols.SYMBOLS[mark]
                if mark != symbol.id and other.slot == symbol.slot and other.display_name not in names:
                    names.append(other.display_name)
    elif symbol.storage == "spans":
        from .notation_rules import span_pairs
        span = _span_rows(score, target, symbol)
        if span:
            for family, start, end, start_id in span_pairs(notes):
                if family == symbol.family and start <= span[1] and end >= span[0] and start_id != symbol.id or (
                        family == symbol.family and start_id == symbol.id and (start, end) != span
                        and start <= span[1] and end >= span[0]):
                    names.append(symbols.SYMBOLS[start_id].display_name)
    elif symbol.storage == "bar" and symbol.id in BAR_STYLE_SLOT:
        bars, reason = _bar_targets(score, target, symbol)
        for bar in bars if not reason else ():
            for item in BAR_STYLE_SLOT - {symbol.id}:
                if _bar_has(score, bar, item):
                    names.append(symbols.SYMBOLS[item].display_name)
    return tuple(dict.fromkeys(names))


def _apply(score: Score, target: Target, symbol_id: str, remove: bool) -> str:
    """Change ``score`` in place; returns a notice (skipped rests, replacements)."""
    symbol = symbols.SYMBOLS[symbol_id]
    notes = score.parts[target.part].notes
    kind = symbol.storage
    notice = ""
    if kind == "marks":
        rows = list(target.rows)
        if remove:
            remove_symbol(score, target.part, rows, symbol_id)
            return ""
        usable = [r for r in rows if notes[r].degree != 0 or symbol.on_rest]
        skipped = len(rows) - len(usable)
        if not usable:
            raise ValueError(tr("休止符不能加「{name}」。").format(name=symbol.display_name))
        apply_symbol(score, target.part, usable, symbol_id)
        if skipped:
            notice = tr("已略過 {count} 個休止符。").format(count=skipped)
        return notice
    if kind == "tie":
        span = _span_rows(score, target, symbol)
        if span is None:
            raise ValueError(tr("最後一個音後面沒有音符，不能加延音線。"))
        for row in range(span[0], span[1]):
            nxt = notes[row + 1]
            note = notes[row]
            if not remove and (note.degree == 0 or (note.degree, note.octave, note.accidental.replace("n", ""))
                               != (nxt.degree, nxt.octave, nxt.accidental.replace("n", ""))):
                raise ValueError(tr("延音線只能連到下一個相同音高的音；這兩個音高不同，要連貫演奏請用「連線（圓滑線）」。"))
            notes[row] = replace(note, tie=not remove)
        return ""
    if kind == "spans":
        span = _span_rows(score, target, symbol)
        if span is None:
            raise ValueError(tr("最後一個音後面沒有音符；請選兩個以上的音。"))
        if remove:
            remove_lines(score, target.part, list(range(span[0], span[1] + 1)), symbol.family)
            return ""
        apply_symbol(score, target.part, list(range(span[0], span[1] + 1)), symbol_id)
        return ""
    if kind == "bar":
        bars, reason = _bar_targets(score, target, symbol)
        if reason:
            raise ValueError(reason)
        if symbol_id in VOLTAS or symbol_id == "volta.final":
            if remove:
                _remove_volta(score, bars[0], symbol_id)
                return ""
            return _place_volta(score, bars[0], bars[-1], symbol_id)
        if symbol_id in ("bar.repeat_start", "bar.repeat_end") and len(bars) > 1:
            for bar, item in ((bars[0], "bar.repeat_start"), (bars[-1], "bar.repeat_end")):
                if item == "bar.repeat_end" and not remove:
                    for other in BAR_STYLE_SLOT - {item}:
                        set_bar_item(score, bar, other, present=False)
                set_bar_item(score, bar, item, present=not remove)
            return ""
        for bar in bars:
            if not remove and symbol_id in BAR_STYLE_SLOT:
                for other in BAR_STYLE_SLOT - {symbol_id}:
                    set_bar_item(score, bar, other, present=False)
            set_bar_item(score, bar, symbol_id, present=not remove)
        return ""
    raise ValueError(_base_reason(score, target, symbol) or tr("不支援的記號或放錯位置：{id}").format(id=symbol_id))


def _friendly(score: Score, target: Target, symbol, error: str) -> str:
    """Validation messages for the most common palette cases, as 現況＋下一步 (design §2.5)."""
    from .notation_rules import bar_items
    if symbol.id in ("nav.ds", "nav.dc"):
        for bar, (starts, ends) in enumerate(bar_items(score)):
            for other in ("nav.ds", "nav.dc"):
                if other in ends:
                    return tr("整首只能有一個跳回記號：{bar}已有 {name}（到那裡移除）。").format(
                        bar=_bar_label(score, bar), name=symbols.SYMBOLS[other].display_name)
        if symbol.id == "nav.ds" and not any("nav.segno" in s for s, _ in bar_items(score)):
            return tr("請先在要跳回的地方加 Segno 記號。")
    return error


_UNCHECKED = object()


def _check_marks(score: Score, trial: Score, target: Target, symbol) -> None:
    """What ``mutate`` would reject for a point mark, without re-validating the whole score.

    A point mark changes only the target notes: each changed note is validated, and the
    only relational rule a mark can break (a dynamic inside a hairpin, notation_rules
    ``_line_rules``) is checked on the lines that contain the targets. Other rules look at
    fields a mark never changes, so the result equals a full ``validate`` + ``notation_error``.
    """
    before, after = score.parts[target.part].notes, trial.parts[target.part].notes
    for row in target.rows:
        if after[row] is not before[row]:
            after[row].validate()
    if symbol.slot == "dyn":
        from .notation_rules import span_pairs
        for family, start, end, _ in span_pairs(after):
            if family == "hairpin":
                for row in target.rows:
                    if start < row < end and any(m.startswith("dyn.") for m in after[row].marks):
                        raise ValueError(tr("第 {n} 個音符的力度會中斷漸強或漸弱線；請把力度放在起終點，或拆成兩段。").format(
                            n=row + 1))


def symbol_state(score: Score, target: Target, symbol_id: str, *, check: bool = True,
                 base_error=_UNCHECKED) -> SymbolState:
    """Can the palette apply ``symbol_id`` to ``target``, and what would a click do?

    The reason comes from the selection rules and, with ``check``, from applying the change
    to a copy and running the validation ``mutate`` runs (one source of truth).
    ``base_error`` (the score's current ``notation_error()``) can be passed when many
    symbols are checked against one selection.
    """
    symbol = symbols.SYMBOLS[symbol_id]
    reason = _base_reason(score, target, symbol)
    if reason:
        return SymbolState(False, reason)
    present, total = _present(score, target, symbol)
    removes = total > 0 and present == total
    replaces = () if removes else _replaced(score, target, symbol)
    if symbol.storage == "marks" and not removes:
        notes = score.parts[target.part].notes
        if all(notes[r].degree == 0 for r in target.rows) and not symbol.on_rest:
            return SymbolState(False, tr("休止符不能加「{name}」。").format(name=symbol.display_name))
        for r in target.rows:
            if notes[r].grace_after and symbol.slot != "breath" and notes[r].degree:
                return SymbolState(False, tr("有後倚音的音只能再加換氣記號；請先移除後倚音。"))
    if check and symbol.storage not in ("tuplet", "composite"):
        trial = score.copy()
        try:
            _apply(trial, target, symbol_id, removes)
            if symbol.storage == "marks":
                _check_marks(score, trial, target, symbol)
            else:
                trial.validate()
                error = score.edit_error() if base_error is _UNCHECKED else base_error
                if error is None and (error := trial.edit_error()):
                    raise ValueError(error)
        except ValueError as exc:
            return SymbolState(False, _friendly(score, target, symbol, str(exc)), present, total)
    return SymbolState(True, "", present, total, replaces, removes)


def target_where(score: Score, target: Target, symbol_id: str) -> str:
    """Where a click would put the symbol: 會加在：第 7 小節第 1 音 (for the detail strip)."""
    symbol = symbols.SYMBOLS[symbol_id]
    if symbol.storage in ("marks", "tie", "spans", "tuplet") and target.rows:
        extra = tr(" 等 {count} 個音").format(count=len(target.rows)) if len(target.rows) > 1 and symbol.storage == "marks" else ""
        return tr("會加在：{place}").format(place=_note_place(score, target.part, target.rows[0])) + extra
    if symbol.storage == "bar":
        bars, _ = _bar_targets(score, target, symbol)
        if bars:
            return tr("會加在：{bar}").format(bar=_bar_label(score, bars[0]) if len(bars) == 1 else tr(
                "第 {a}–{b} 小節").format(a=bar_number(score, bars[0]), b=bar_number(score, bars[-1])))
    return ""


def toggle_symbol(score: Score, target: Target, symbol_id: str) -> str:
    """Apply the palette click: remove when every target has it, else add (replacing a same-slot
    mark). Edits ``score`` in place (inside ``mutate``); returns a notice for the toast."""
    symbol = symbols.SYMBOLS[symbol_id]
    reason = _base_reason(score, target, symbol)
    if reason:
        raise ValueError(reason)
    present, total = _present(score, target, symbol)
    removes = total > 0 and present == total
    replaced = () if removes else _replaced(score, target, symbol)
    notice = _apply(score, target, symbol_id, removes)
    name = symbol.display_name
    if removes:
        return tr("已移除「{name}」。").format(name=name)
    if replaced:
        text = tr("已把「{old}」換成「{new}」。").format(old="、".join(replaced), new=name)
        return text + (" " + notice if notice else "")
    return notice
