"""Relational score rules (wow/tech.md §1.5): rhythm, lines, bar structure and navigation.

``Note.validate`` / ``Score.validate`` check each value on its own. The rules here look
across notes, bars and parts and return the first problem as a message (or None), so
the editor can keep an unfinished score and show what is missing.
"""
from __future__ import annotations

from dataclasses import replace
from fractions import Fraction

from . import symbols
from .i18n import tr

TICKS_PER_QUARTER = 48


def _beats(ticks: int) -> Fraction:
    return Fraction(ticks, TICKS_PER_QUARTER)


def _part_label(score, index: int) -> str:
    if len(score.parts) == 1:
        return ""
    name = score.parts[index].name or tr("第 {n} 聲部").format(n=index + 1)
    return tr("{part}：").format(part=name)


# ---------------------------------------------------------------- rhythm
def rhythm_error(score) -> str | None:
    totals = []
    for index, part in enumerate(score.parts):
        position = 0
        for i, note in enumerate(part.notes):
            offset, length = score.bar_room(position)
            if offset + note.ticks > length:
                return _part_label(score, index) + tr(
                    "第 {n} 個音符超出第 {bar} 小節。請縮短時值，跨小節長音可拆成音符再加延音線。").format(
                    n=i + 1, bar=score.bar_index(position) + 1)
            position += note.ticks
        totals.append(position)
        if part.notes:
            target = score.final_bar_targets(position)[0]
            if target != position:
                return _part_label(score, index) + tr("最後一小節還缺 {beats} 拍。按「補齊休止符」即可完成。").format(
                    beats=_beats(target - position))
    for index, total in enumerate(totals[1:], 1):
        if total != totals[0]:
            return tr("「{name}」的總拍數和第一聲部不同（相差 {beats} 拍）；各聲部長度須相同。").format(
                name=score.parts[index].name or tr("第 {n} 聲部").format(n=index + 1),
                beats=_beats(abs(total - totals[0])))
    return None


# ---------------------------------------------------------------- lines
def _pair_spans(notes, part_prefix=""):
    """Yield (family, start_row, end_row, start_id); raise ValueError with the first pairing error."""
    open_ = {}
    for i, note in enumerate(notes):
        for sid in note.spans_end:
            family = symbols.SYMBOLS[sid].family
            if family not in open_:
                if family == "hairpin":
                    raise ValueError(tr("第 {n} 個音符的漸強或漸弱線缺少起點。").format(n=i + 1))
                if family == "slur":
                    raise ValueError(tr("第 {n} 個音符的連弓線缺少起點。").format(n=i + 1))
                raise ValueError(tr("第 {n} 個音符的「{name}」缺少起點。").format(
                    n=i + 1, name=symbols.SYMBOLS[sid].display_name))
            start, start_id = open_.pop(family)
            yield family, start, i, start_id
        for sid in note.spans_start:
            family = symbols.SYMBOLS[sid].family
            if family in open_:
                if family == "hairpin":
                    raise ValueError(tr("第 {n} 個音符有重疊的漸強或漸弱線，請先結束前一段。").format(n=i + 1))
                if family == "slur":
                    raise ValueError(tr("第 {n} 個音符有重疊連弓線，請先移除舊連線。").format(n=i + 1))
                raise ValueError(tr("第 {n} 個音符的「{name}」和前一條重疊，請先結束前一條。").format(
                    n=i + 1, name=symbols.SYMBOLS[sid].display_name))
            open_[family] = (i, sid)
    if open_:
        family, (row, sid) = min(open_.items(), key=lambda item: item[1][0])
        if family == "slur":
            raise ValueError(tr("連弓線或反覆段落缺少終點。請選取完整範圍重新設定，或移除記號。"))
        if family == "hairpin":
            raise ValueError(tr("漸強或漸弱線缺少終點；請選取完整範圍重新設定，或移除記號。"))
        raise ValueError(tr("第 {n} 個音符開始的「{name}」缺少終點；請選取完整範圍重新設定，或移除記號。").format(
            n=row + 1, name=symbols.SYMBOLS[sid].display_name))


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
    for bar, (starts, ends) in enumerate(items):
        if "bar.repeat_start" in starts or (bar and "bar.repeat_both" in items[bar - 1][1]):
            if open_repeat is not None or volta_open:
                raise ValueError(tr("第 {bar} 小節的反覆起點和前一段反覆重疊；反覆不能巢狀。").format(bar=bar + 1))
            open_repeat = bar
        voltas = [s for s in starts if s.startswith("volta.") and s[6:].isdigit()]
        if "volta.final" in starts and not voltas:
            raise ValueError(tr("第 {bar} 小節的「結尾房」須和房號一起使用。").format(bar=bar + 1))
        for volta in voltas:
            number = int(volta[6:])
            if number == 1:
                if last_repeat_end != bar - 1 or volta_open:
                    raise ValueError(tr("第 {bar} 小節的第一房須緊接在反覆終點的小節之後。").format(bar=bar + 1))
                volta_open, expected = True, 2
                region_start = regions.pop()[0]
            elif not volta_open or number != expected:
                raise ValueError(tr("第 {bar} 小節的房號不連續；請依序設定第一房、第二房…").format(bar=bar + 1))
            else:
                expected += 1
            final_bar = bar if "volta.final" in starts else final_bar
        if "bar.repeat_end" in ends or "bar.repeat_both" in ends:
            if open_repeat is None and not volta_open:
                raise ValueError(tr("第 {n} 個音符的反覆終點須在完整小節末，且需要起點。").format(n=ranges[bar][1]))
            if volta_open:
                raise ValueError(tr("第 {bar} 小節的反覆終點放在房子裡；最後一房的結尾請用「房子結束」。").format(bar=bar + 1))
            regions.append((open_repeat, bar))
            last_repeat_end, open_repeat = bar, None
            if "bar.repeat_both" in ends and bar + 1 >= len(items):
                raise ValueError(tr("第 {bar} 小節的雙向反覆後面需要再有小節。").format(bar=bar + 1))
        if "volta.end" in ends:
            if not volta_open:
                raise ValueError(tr("第 {bar} 小節的「房子結束」前面沒有房子。").format(bar=bar + 1))
            if final_bar is not None and final_bar != _last_volta_start(items, bar):
                raise ValueError(tr("「結尾房」只能標在最後一房。"))
            volta_open, final_bar = False, None
            regions.append((region_start, bar))
    if open_repeat is not None:
        raise ValueError(tr("連弓線或反覆段落缺少終點。請選取完整範圍重新設定，或移除記號。"))
    if volta_open:
        raise ValueError(tr("最後一房缺少「房子結束」。"))
    _navigation(items, regions)
    return regions


def _last_volta_start(items, end_bar):
    return max(b for b in range(end_bar + 1) if any(s.startswith("volta.") and s[6:].isdigit() for s in items[b][0]))


def _navigation(items, regions):
    where = {}
    for bar, (starts, ends) in enumerate(items):
        for item in (*starts, *ends):
            if item.startswith("nav.") and not symbols.SYMBOLS[item].print_only:
                if item in where:
                    raise ValueError(tr("「{name}」整首只能出現一次。").format(name=symbols.SYMBOLS[item].display_name))
                where[item] = bar
    jumps = [j for j in symbols.JUMPS if j in where]
    if len(jumps) > 1:
        raise ValueError(tr("D.S. 和 D.C. 不能同時使用；整首只能有一個跳躍。"))
    jump = where.get(jumps[0]) if jumps else None
    segno, tocoda, fine = where.get("nav.segno"), where.get("nav.tocoda"), where.get("nav.fine")
    if ("nav.ds" in where) != (segno is not None):
        raise ValueError(tr("D.S. 需要一個 D.S. 記號（𝄋），而且記號要在 D.S. 之前。"))
    if segno is not None and segno > jump:
        raise ValueError(tr("D.S. 需要一個 D.S. 記號（𝄋），而且記號要在 D.S. 之前。"))
    if tocoda is not None and ("nav.ds" not in where or not segno <= tocoda < jump):
        raise ValueError(tr("To Coda 須放在 D.S. 記號和 D.S. 之間。"))
    if fine is not None and jump is not None and not ((segno or 0) <= fine < jump):
        raise ValueError(tr("Fine 須放在跳躍記號（D.C./D.S.）之前。"))
    if fine is not None and tocoda is not None:
        raise ValueError(tr("Fine 和 To Coda 不能同時使用。"))
    for item, bar in where.items():
        start_item = symbols.SYMBOLS[item].bar_edge == "start"
        for first, last in regions:
            if first <= bar < last or (start_item and bar == last) or (start_item and bar == first):
                raise ValueError(tr("「{name}」不能放在反覆段落或房子裡面；可改用「僅文字」或「僅符號」記號。").format(
                    name=symbols.SYMBOLS[item].display_name))


def _bar_positions(score, notes) -> list[int]:
    positions, position = [], 0
    for note in notes:
        positions.append(position)
        position += note.ticks
    return positions


def notation_error(score) -> str | None:
    from .model import tuplet_ranges
    try:
        for index, part in enumerate(score.parts):
            prefix = _part_label(score, index)
            notes = part.notes
            try:
                groups = dict(tuplet_ranges(notes))
            except ValueError as exc:
                return prefix + str(exc)
            positions = _bar_positions(score, notes)
            for start, end in groups.items():
                offset, length = score.bar_room(positions[start])
                if offset + sum(n.ticks for n in notes[start:end]) > length:
                    return prefix + tr("第 {n} 個音符的三連音跨越小節；請將整組移到同一小節內。").format(n=start + 1)
            try:
                pairs = list(_pair_spans(notes))
            except ValueError as exc:
                return prefix + str(exc)
            if error := _line_rules(score, notes, pairs, positions):
                return prefix + error
            for i, note in enumerate(notes):
                if note.tie and (not note.degree or i + 1 == len(notes) or
                                 (note.degree, note.octave, "" if note.accidental == "n" else note.accidental) !=
                                 (notes[i+1].degree, notes[i+1].octave,
                                  "" if notes[i+1].accidental == "n" else notes[i+1].accidental)):
                    return prefix + tr("第 {n} 個音符的延音線須接到下一個相同音高的音符。").format(n=i + 1)
        if error := _bar_rules(score):
            return error
        _structure(score)
        if error := _ottava_blocks(score):
            return error
    except ValueError as exc:
        return str(exc)
    return None


def _line_rules(score, notes, pairs, positions) -> str | None:
    for family, start, end, _ in pairs:
        if family == "hairpin":
            for row in range(start + 1, end):
                if any(m.startswith("dyn.") for m in notes[row].marks):
                    return tr("第 {n} 個音符的力度會中斷漸強或漸弱線；請把力度放在起終點，或拆成兩段。").format(n=row + 1)
        if family == "gliss" and end != start + 1:
            return tr("第 {n} 個音符的大滑音須連到下一個音符。").format(n=start + 1)
    return None


def _bar_rules(score) -> str | None:
    notes = score.parts[0].notes
    rows, ranges = bar_map(score)
    position = 0
    for i, note in enumerate(notes):
        start = position
        position += note.ticks
        if note.bar_start and (not score.is_boundary(start) or ranges[rows[i]][0] != i):
            if "bar.repeat_start" in note.bar_start:
                return tr("第 {n} 個音符的反覆起點須在小節開頭，且不能重疊。").format(n=i + 1)
            return tr("第 {n} 個音符的小節記號須放在小節的第一個音符。").format(n=i + 1)
        if note.bar_end and not score.is_boundary(position):
            if "bar.repeat_end" in note.bar_end:
                return tr("第 {n} 個音符的反覆終點須在完整小節末，且需要起點。").format(n=i + 1)
            return tr("第 {n} 個音符的小節記號須放在完整小節的最後一個音符。").format(n=i + 1)
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
                return _part_label(score, index) + tr("第 {n} 個音符的高／低八度線不能跨過反覆、房子或跳躍記號；請拆成兩段。").format(n=start + 1)
    return None
