"""Relational score rules (wow/tech.md §1.5): rhythm, lines, bar structure and navigation.

``Note.validate`` / ``Score.validate`` check each value on its own. The rules here look
across notes, bars and parts and return the first problem as a message (or None), so
the editor can keep an unfinished score and show what is missing.
"""
from __future__ import annotations

from dataclasses import replace
from fractions import Fraction

from . import symbols
from .i18n import N_, tr

TICKS_PER_QUARTER = 48


def _beats(ticks: int) -> Fraction:
    return Fraction(ticks, TICKS_PER_QUARTER)


def _part_label(score, index: int) -> str:
    if len(score.parts) == 1:
        return ""
    name = score.parts[index].name or tr("第 {n} 聲部").format(n=index + 1)
    return tr("{part}：").format(part=name)


# ---------------------------------------------------------------- locating a problem
class Problem(str):
    """A message that also knows where the problem is (part, note row and/or bar) and, when
    there is one, a one-click fix: ``("fill",)`` completes the last bar, ``(field, item)``
    removes that orphan item from the note ``row``."""

    def __new__(cls, text, part=0, row=None, bar=None, fix=None):
        obj = str.__new__(cls, text)
        obj.part, obj.row, obj.bar, obj.fix = part, row, bar, fix
        return obj


class LocatedError(ValueError):
    def __init__(self, message, row=None, bar=None, fix=None):
        super().__init__(message)
        self.row, self.bar, self.fix = row, bar, fix


def beat_ticks(meter: str) -> int:
    """Ticks of the counted beat: 2/2 counts halves, x/8 counts eighths (6/8, 9/8, 12/8 dotted quarters)."""
    numerator, denominator = (int(v) for v in meter.split("/"))
    if denominator == 8 and numerator in (6, 9, 12):
        return 3 * TICKS_PER_QUARTER // 2
    return 4 * TICKS_PER_QUARTER // denominator


def bar_label(score, bar: int) -> str:
    """「第 3 小節」, or 「弱起小節」 for the pickup (printed numbers start at 1 after a pickup)."""
    if score.pickup and bar == 0:
        return tr("弱起小節")
    return tr("第 {bar} 小節").format(bar=bar + 1 - bool(score.pickup))


def tick_place(score, position: int, nth: int = 0) -> str:
    """「第 3 小節第 2 拍」 for a tick position; off the beat 「第 2 拍後半」 or 「第 2 拍第 3 個音」.

    One catalog template per form, so a message that embeds it still converts as a whole."""
    offset, _ = score.bar_room(position)
    pickup = bool(score.pickup) and position < score.first_bar_ticks
    if pickup:
        offset += score.bar_ticks - score.first_bar_ticks     # a pickup ends on the bar's last beat
    beat = beat_ticks(score.meter)
    number, rest = divmod(offset, beat)
    values = dict(bar=score.bar_index(position) + 1 - bool(score.pickup), beat=number + 1, n=max(2, nth))
    if not rest:
        template = N_("弱起小節第 {beat} 拍") if pickup else N_("第 {bar} 小節第 {beat} 拍")
    elif rest * 2 == beat:
        template = N_("弱起小節第 {beat} 拍後半") if pickup else N_("第 {bar} 小節第 {beat} 拍後半")
    else:
        template = N_("弱起小節第 {beat} 拍第 {n} 個音") if pickup else N_("第 {bar} 小節第 {beat} 拍第 {n} 個音")
    return tr(template).format(**values)


def note_place(score, part: int, row: int, *, with_part: bool = False) -> str:
    """Where note ``row`` of ``part`` is, for messages (never a global note index)."""
    notes = score.parts[part].notes
    row = max(0, min(row, len(notes) - 1))
    position, beat = 0, beat_ticks(score.meter)
    nth = 1
    for i in range(row):
        position += notes[i].ticks
    # nth note inside its beat (for off-beat notes)
    start_of_beat = position - (score.bar_room(position)[0] % beat)
    p = position
    i = row
    while i > 0 and p - notes[i - 1].ticks >= start_of_beat:
        p -= notes[i - 1].ticks
        i -= 1
        nth += 1
    text = tick_place(score, position, nth)
    return (_part_label(score, part) + text) if with_part else text



# ---------------------------------------------------------------- rhythm
def crossing(score) -> Problem | None:
    """The first note that crosses a bar line (any part), or None."""
    for index, part in enumerate(score.parts):
        position = 0
        for i, note in enumerate(part.notes):
            offset, length = score.bar_room(position)
            if offset + note.ticks > length:
                return Problem(_part_label(score, index) + note_place(score, index, i), index, i)
            position += note.ticks
    return None


def rhythm_error(score) -> str | None:
    totals = []
    for index, part in enumerate(score.parts):
        position = 0
        for i, note in enumerate(part.notes):
            offset, length = score.bar_room(position)
            if offset + note.ticks > length:
                return Problem(_part_label(score, index) + tr(
                    "{where}的音超出小節線。請縮短時值，跨小節長音可拆成音符再加延音線。").format(
                    where=note_place(score, index, i)), index, i)
            position += note.ticks
        totals.append(position)
        if part.notes:
            target = score.final_bar_targets(position)[0]
            if target != position:
                last = len(part.notes) - 1
                return Problem(_part_label(score, index) + tr("最後一小節（{bar}）還缺 {beats} 拍。按「補齊休止符」即可完成。").format(
                    bar=bar_label(score, score.bar_index(position - 1)), beats=_beats(target - position)),
                    index, last, score.bar_index(position - 1), ("fill",))
    for index, total in enumerate(totals[1:], 1):
        if total != totals[0]:
            shorter = index if total < totals[0] else 0
            return Problem(tr("「{name}」的總拍數和第一聲部不同（相差 {beats} 拍）；各聲部長度須相同。按「補齊休止符」可把較短的聲部補齊。").format(
                name=score.parts[index].name or tr("第 {n} 聲部").format(n=index + 1),
                beats=_beats(abs(total - totals[0]))), shorter, max(0, len(score.parts[shorter].notes) - 1),
                None, ("fill",))
    return None


# ---------------------------------------------------------------- lines
def _pair_spans(notes, where=None):
    """Yield (family, start_row, end_row, start_id); raise LocatedError with the first pairing error.

    ``where(row)`` names a note's place for the message (「第 3 小節第 2 拍」)."""
    where = where or (lambda row: "")
    open_ = {}
    for i, note in enumerate(notes):
        for sid in note.spans_end:
            family = symbols.SYMBOLS[sid].family
            if family not in open_:
                fix = ("spans_end", sid)
                if family == "hairpin":
                    raise LocatedError(tr("{where}的漸強或漸弱線缺少起點。").format(where=where(i)), i, fix=fix)
                if family == "slur":
                    raise LocatedError(tr("{where}的連線缺少起點。").format(where=where(i)), i, fix=fix)
                raise LocatedError(tr("{where}的「{name}」缺少起點。").format(
                    where=where(i), name=symbols.SYMBOLS[sid].display_name), i, fix=fix)
            start, start_id = open_.pop(family)
            yield family, start, i, start_id
        for sid in note.spans_start:
            family = symbols.SYMBOLS[sid].family
            if family in open_:
                fix = ("spans_start", sid)
                if family == "hairpin":
                    raise LocatedError(tr("{where}有重疊的漸強或漸弱線，請先結束前一段。").format(where=where(i)), i, fix=fix)
                if family == "slur":
                    raise LocatedError(tr("{where}有重疊的連線，請先移除舊連線。").format(where=where(i)), i, fix=fix)
                raise LocatedError(tr("{where}的「{name}」和前一條重疊，請先結束前一條。").format(
                    where=where(i), name=symbols.SYMBOLS[sid].display_name), i, fix=fix)
            open_[family] = (i, sid)
    if open_:
        family, (row, sid) = min(open_.items(), key=lambda item: item[1][0])
        fix = ("spans_start", sid)
        if family == "slur":
            raise LocatedError(tr("{where}開始的連線缺少終點。請選取完整範圍重新設定，或移除記號。").format(
                where=where(row)), row, fix=fix)
        if family == "hairpin":
            raise LocatedError(tr("{where}開始的漸強或漸弱線缺少終點；請選取完整範圍重新設定，或移除記號。").format(
                where=where(row)), row, fix=fix)
        raise LocatedError(tr("{where}開始的「{name}」缺少終點；請選取完整範圍重新設定，或移除記號。").format(
            where=where(row), name=symbols.SYMBOLS[sid].display_name), row, fix=fix)


def span_pairs(notes) -> list[tuple[str, int, int, str]]:
    """Paired lines of one part as (family, start_row, end_row, start_id); [] if they do not pair."""
    try:
        return list(_pair_spans(notes))
    except ValueError:
        return []


def normalized_parts(score):
    """Parts whose span end ids name the id that started the line (legacy edits write a family default)."""
    out = []
    for part in score.parts:
        notes = list(part.notes)
        for family, start, end, start_id in span_pairs(notes):
            ends = notes[end].spans_end
            current = next(s for s in ends if symbols.SYMBOLS[s].family == family)
            if current != start_id:
                notes[end] = replace(notes[end], spans_end=symbols.sort_ids(
                    [s for s in ends if s != current] + [start_id]))
        out.append(replace(part, notes=notes))
    return out


# ---------------------------------------------------------------- bars
def bar_map(score, index: int = 0):
    """Row -> bar number, and the bar ranges, for one part."""
    ranges = score.part_bars(index)
    rows = {}
    for bar, (start, end) in enumerate(ranges):
        for row in range(start, end):
            rows[row] = bar
    return rows, ranges


def bar_items(score):
    """{bar: (start_items, end_items)} from parts[0]; bar items live only there."""
    _, ranges = bar_map(score)
    notes = score.parts[0].notes
    return [(notes[start].bar_start, notes[end - 1].bar_end) for start, end in ranges]


def _structure(score):
    """Validate repeats, voltas and navigation on parts[0]; return regions of repeat structures.

    Returns a list of (first_bar, last_bar) regions: a repeat body plus its alternatives.
    """
    items = bar_items(score)
    ranges = score.part_bars(0)
    regions = []
    open_repeat = None
    volta_open, expected, last_repeat_end, region_start = False, 0, None, None
    final_bar = None

    def fail(message, bar, edge=None, item=None):
        row = None if bar is None else (ranges[bar][0] if edge == "bar_start" else ranges[bar][1] - 1)
        raise LocatedError(message, row, bar, (edge, item) if item else None)

    for bar, (starts, ends) in enumerate(items):
        label = bar_label(score, bar)
        if "bar.repeat_start" in starts or (bar and "bar.repeat_both" in items[bar - 1][1]):
            if open_repeat is not None or volta_open:
                fail(tr("{bar}的反覆起點和前一段反覆重疊；反覆不能巢狀。").format(bar=label), bar,
                     "bar_start", "bar.repeat_start" if "bar.repeat_start" in starts else None)
            open_repeat = bar
        voltas = [s for s in starts if s.startswith("volta.") and s[6:].isdigit()]
        if "volta.final" in starts and not voltas:
            fail(tr("{bar}的「最後結尾（結）」須和結尾編號一起使用。").format(bar=label), bar, "bar_start", "volta.final")
        for volta in voltas:
            number = int(volta[6:])
            if number == 1:
                if last_repeat_end != bar - 1 or volta_open:
                    fail(tr("{bar}的第一結尾須緊接在反覆終點的小節之後。").format(bar=label), bar)
                volta_open, expected = True, 2
                region_start = regions.pop()[0]
            elif not volta_open or number != expected:
                fail(tr("{bar}的結尾編號不連續；請依序設定第一結尾、第二結尾…").format(bar=label), bar)
            else:
                expected += 1
            final_bar = bar if "volta.final" in starts else final_bar
        if "bar.repeat_end" in ends or "bar.repeat_both" in ends:
            if open_repeat is None and not volta_open:
                fail(tr("{bar}結尾的反覆終點（:‖）前面沒有反覆起點（‖:）。請在反覆段落開頭加「反覆起」，或移除這個反覆終點。").format(
                    bar=label), bar, "bar_end", "bar.repeat_end" if "bar.repeat_end" in ends else None)
            if volta_open:
                fail(tr("{bar}的反覆終點放在結尾段裡；最後一個結尾的末端請用「房子結束」。").format(bar=label), bar)
            regions.append((open_repeat, bar))
            last_repeat_end, open_repeat = bar, None
            if "bar.repeat_both" in ends and bar + 1 >= len(items):
                fail(tr("{bar}的雙向反覆後面需要再有小節。").format(bar=label), bar, "bar_end", "bar.repeat_both")
        if "volta.end" in ends:
            if not volta_open:
                fail(tr("{bar}的「房子結束」前面沒有結尾段。").format(bar=label), bar, "bar_end", "volta.end")
            if final_bar is not None and final_bar != _last_volta_start(items, bar):
                fail(tr("「最後結尾（結）」只能標在最後一個結尾（{bar}）。").format(bar=bar_label(score, final_bar)), final_bar,
                     "bar_start", "volta.final")
            volta_open, final_bar = False, None
            regions.append((region_start, bar))
    if open_repeat is not None:
        fail(tr("{bar}開始的反覆段落缺少反覆終點（:‖）。請在段落最後一小節加「反覆止」，或移除反覆起點。").format(
            bar=bar_label(score, open_repeat)), open_repeat, "bar_start",
            "bar.repeat_start" if "bar.repeat_start" in items[open_repeat][0] else None)
    if volta_open:
        fail(tr("最後一個結尾缺少「房子結束」。"), len(items) - 1)
    _navigation(items, regions, score)
    return regions


def _last_volta_start(items, end_bar):
    return max(b for b in range(end_bar + 1) if any(s.startswith("volta.") and s[6:].isdigit() for s in items[b][0]))


def _navigation(items, regions, score):
    pickup = bool(score.pickup)
    where = {}
    for bar, (starts, ends) in enumerate(items):
        for item in (*starts, *ends):
            if item.startswith("nav.") and not symbols.SYMBOLS[item].print_only:
                if item in where:
                    raise LocatedError(tr("「{name}」整首只能出現一次（第二個在{bar}）。").format(
                        name=symbols.SYMBOLS[item].display_name, bar=bar_label(score, bar)), bar=bar)
                where[item] = bar
    jumps = [j for j in symbols.JUMPS if j in where]
    if len(jumps) > 1:
        raise LocatedError(tr("D.S. 和 D.C. 不能同時使用；整首只能有一個跳躍。"), bar=where[jumps[1]])
    jump = where.get(jumps[0]) if jumps else None
    segno, tocoda, fine = where.get("nav.segno"), where.get("nav.tocoda"), where.get("nav.fine")
    if ("nav.ds" in where) != (segno is not None):
        raise LocatedError(tr("D.S. 需要一個 D.S. 記號（𝄋），而且記號要在 D.S. 之前。"),
                           bar=where.get("nav.ds", segno))
    if segno is not None and segno > jump:
        raise LocatedError(tr("D.S. 需要一個 D.S. 記號（𝄋），而且記號要在 D.S. 之前。"), bar=segno)
    if tocoda is not None and ("nav.ds" not in where or not segno <= tocoda < jump):
        raise LocatedError(tr("To Coda 須放在 D.S. 記號和 D.S. 之間。"), bar=tocoda)
    if fine is not None and jump is not None and not ((segno or 0) <= fine < jump):
        raise LocatedError(tr("Fine 須放在跳躍記號（D.C./D.S.）之前。"), bar=fine)
    if fine is not None and tocoda is not None:
        raise LocatedError(tr("Fine 和 To Coda 不能同時使用。"), bar=fine)
    if tocoda is not None and {"bar.repeat_end", "bar.repeat_both"} & set(items[tocoda][1]):
        # Upstream writes To Coda as an alternative of the repeat that just closed (prints a
        # volta bracket and plays the bars in the wrong order).
        raise LocatedError(tr("To Coda 不能和反覆終點（:‖）放在同一條小節線；請改放在其他小節線，或改用「僅文字」記號。"), bar=tocoda)
    if fine is not None and jump is not None and jump != len(items) - 1:
        # The return pass stops at Fine, so bars after the jump are never played; LilyPond
        # would play them anyway ("found music after \fine").
        raise LocatedError(tr("有 Fine 時，D.C./D.S. 須放在最後一小節：跳回後在 Fine 結束，"
                            "後面的第 {bar} 小節起永遠不會演奏。請刪除這些小節，或把跳躍記號移到最後一小節。").format(
            bar=jump + 2 - pickup), bar=jump)
    for item, bar in where.items():
        start_item = symbols.SYMBOLS[item].bar_edge == "start"
        for first, last in regions:
            if first <= bar < last or (start_item and bar == last) or (start_item and bar == first):
                raise LocatedError(tr("{bar}的「{name}」不能放在反覆段落或結尾段裡面；可改用「僅文字」或「僅符號」記號。").format(
                    bar=bar_label(score, bar), name=symbols.SYMBOLS[item].display_name), bar=bar)


def _bar_positions(score, notes) -> list[int]:
    positions, position = [], 0
    for note in notes:
        positions.append(position)
        position += note.ticks
    return positions


def notation_error(score) -> str | None:
    """The first relational problem as a ``Problem`` (a str that knows its part, row, bar and fix)."""
    from .model import tuplet_ranges
    index = 0
    try:
        for index, part in enumerate(score.parts):
            prefix = _part_label(score, index)
            notes = part.notes

            def where(row, index=index):
                return note_place(score, index, row)
            try:
                groups = dict(tuplet_ranges(notes, where))
            except ValueError as exc:
                return Problem(prefix + str(exc), index, getattr(exc, "row", None))
            positions = _bar_positions(score, notes)
            for start, end in groups.items():
                offset, length = score.bar_room(positions[start])
                if offset + sum(n.ticks for n in notes[start:end]) > length:
                    return Problem(prefix + tr("{where}的三連音跨越小節線；請將整組移到同一小節內。").format(
                        where=where(start)), index, start)
            try:
                pairs = list(_pair_spans(notes, where))
            except LocatedError as exc:
                return Problem(prefix + str(exc), index, exc.row, None, exc.fix)
            if error := _line_rules(score, notes, pairs, where):
                return Problem(prefix + error, index, error.row, None, error.fix)
            for i, note in enumerate(notes):
                if note.tie and (not note.degree or i + 1 == len(notes) or
                                 (note.degree, note.octave, "" if note.accidental == "n" else note.accidental) !=
                                 (notes[i+1].degree, notes[i+1].octave,
                                  "" if notes[i+1].accidental == "n" else notes[i+1].accidental)):
                    return Problem(prefix + tr("{where}的延音線須接到下一個相同音高的音符。").format(where=where(i)),
                                   index, i, None, ("tie", True))
        index = 0
        if error := _bar_rules(score):
            return error
        _structure(score)
        if error := _ottava_blocks(score):
            return error
    except LocatedError as exc:
        return Problem(str(exc), index, exc.row, exc.bar, exc.fix)
    except ValueError as exc:
        return Problem(str(exc), index)
    return None


def _line_rules(score, notes, pairs, where) -> Problem | None:
    for family, start, end, sid in pairs:
        if family == "hairpin":
            for row in range(start + 1, end):
                if any(m.startswith("dyn.") for m in notes[row].marks):
                    return Problem(tr("{where}的力度會中斷漸強或漸弱線；請把力度放在起終點，或拆成兩段。").format(
                        where=where(row)), row=row)
        if family == "gliss" and end != start + 1:
            return Problem(tr("{where}的大滑音須連到下一個音符。").format(where=where(start)), row=start,
                           fix=("spans_start", sid))
    return None


def _bar_rules(score) -> Problem | None:
    notes = score.parts[0].notes
    rows, ranges = bar_map(score)
    position = 0
    for i, note in enumerate(notes):
        start = position
        position += note.ticks
        if note.bar_start and (not score.is_boundary(start) or ranges[rows[i]][0] != i):
            where = note_place(score, 0, i)
            if "bar.repeat_start" in note.bar_start:
                return Problem(tr("{where}的反覆起點須在小節開頭，且不能重疊。").format(where=where), 0, i, None,
                               ("bar_start", "bar.repeat_start"))
            return Problem(tr("{where}的小節記號須放在小節的第一個音符。").format(where=where), 0, i, None,
                           ("bar_start", note.bar_start[0]))
        if note.bar_end and not score.is_boundary(position):
            where = note_place(score, 0, i)
            if "bar.repeat_end" in note.bar_end:
                return Problem(tr("{where}的反覆終點須在完整小節末，且需要起點。").format(where=where), 0, i, None,
                               ("bar_end", "bar.repeat_end"))
            return Problem(tr("{where}的小節記號須放在完整小節的最後一個音符。").format(where=where), 0, i, None,
                           ("bar_end", note.bar_end[0]))
    return None


def _ottava_blocks(score) -> str | None:
    """8va/8vb may not cross a repeat, alternative or jump (the MIDI rewrite adds braces)."""
    items = bar_items(score)
    for index, part in enumerate(score.parts):
        rows, _ = bar_map(score, index)
        for family, start, end, _ in span_pairs(part.notes):
            if family != "ottava":
                continue
            a, b = rows[start], rows[end]
            # bar_start words of the start bar print before the line starts and bar_end words of
            # the end bar after it ends, so only boundaries strictly inside the line count.
            inside = [items[bar][0] for bar in range(a + 1, min(b, len(items) - 1) + 1)]
            inside += [items[bar][1] for bar in range(a, min(b, len(items)))]
            crossing = [s for group in inside for s in group if s in symbols.STRUCTURE_BAR_ITEMS]
            if crossing:
                return Problem(_part_label(score, index) + tr("{where}的高／低八度線不能跨過反覆、結尾段或跳躍記號；請拆成兩段。").format(
                    where=note_place(score, index, start)), index, start)
    return None
