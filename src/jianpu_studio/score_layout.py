"""Canvas geometry (wow/design.md §3): systems of all parts, a shared time grid per bar,
and lanes that stack each note's marks from the note outward without empty layers.

Everything is computed once per score/size change and cached on the view; painting and
hit-testing only read it. Coordinates are content pixels (scroll offset not applied).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from PySide6.QtCore import QPointF, QRect, Qt
from PySide6.QtGui import QFont, QFontMetrics, QFontMetricsF, QPainterPath

from . import glyphs, symbols
from .i18n import tr, ui_font_family
from .model import TICKS_PER_QUARTER

GRACE_PATTERN = re.compile(r"([#b]?)([1-7])([,']{0,2})")
PAPER_MAX = 1240
DESK_MARGIN = 14
LABEL_TIER = 18.0      # unscaled minimum height of one row of free text above the notes
LABEL_GAP = 6.0       # unscaled room kept between two labels on one row


def grace_tokens(text):
    return tuple((accidental, degree, -octave.count(",") if "," in octave else len(octave))
                 for accidental, degree, octave in GRACE_PATTERN.findall(text)) if text else ()


def long_beats(note):
    # Draw nominal quarter-note dashes, independent of tuplet performance time.
    return (4 // note.duration) * (3 if note.dotted else 2) // 2 if note.duration <= 2 else 1


def beams(note) -> int:
    return {8: 1, 16: 2, 32: 3}.get(note.duration, 0)


@dataclass
class Row:
    """One part inside one system."""
    part: int
    top: float
    baseline: float
    bottom: float
    dynamic_y: float = 0.0       # centre of the dynamics lane
    hairpin_y: float = 0.0
    pedal_y: float = 0.0
    trem_y: float = 0.0
    below_span_y: float = 0.0    # 8vb line
    arc_y: float = 0.0           # slur/tie lane (top of arcs' ends)
    tuplet_y: float = 0.0
    text_y: float = 0.0          # free text above the notes (中速、引子…)
    lyric_y: float = 0.0         # lyrics under the notes
    span_lanes: dict = field(default_factory=dict)   # family -> y (8va, trill, text spans)


@dataclass
class System:
    index: int
    bars: list
    rect: QRect
    rows: dict                   # part -> Row
    volta_y: float = 0.0
    nav_y: float = 0.0


def _marks_by_place(note):
    above, left, right, below = [], [], [], []
    for mark in note.marks:
        symbol = symbols.SYMBOLS.get(mark)
        if symbol is None:
            continue
        where = glyphs.position(symbol)
        {"above": above, "left": left, "right": right, "below": below}[where].append(symbol)
    above.sort(key=lambda s: (glyphs.layer(s), symbols.ORDER[s.id]))
    return above, left, right, below


def note_head_need(note, graces, variant) -> float:
    """Unscaled horizontal room a note needs at its onset."""
    above, left, right, _ = _marks_by_place(note)
    need = 34.0
    need += sum(14 * len(group) + 16 for group in graces if group)
    if left:
        need += 16
    if right:
        need += 12
    if note.accidental:
        need += 8
    if above:
        need = max(need, max(glyphs.mark_width(s, variant) for s in above) + 10)
    return need


class LayoutMixin:
    """Computes ``systems`` and the 0.3.x geometry names the tests and hit-testing use."""

    _header_text = ""      # first-note tempo words shown in the header line (as printed)

    def relayout(self):
        s = self.scale
        score = self.score_model.score
        # As the print: tempo words on the very first note join the header line (export.header_words).
        from .export import header_words
        first = score.parts[0].notes[0] if score.parts and score.parts[0].notes else None
        self._header_text = first.text if first is not None and header_words(first.text) else ""
        variant = score.print_variant
        parts = score.parts
        active = self.score_model.part
        multi = len(parts) > 1
        width = self.viewport().width()
        paper_w = min(max(200, width - 2 * DESK_MARGIN), int(PAPER_MAX * max(1.0, s)))
        paper_x = max(DESK_MARGIN, (width - paper_w) // 2) if width - 2 * DESK_MARGIN > paper_w else DESK_MARGIN
        self.paper_rect = QRect(paper_x, DESK_MARGIN, paper_w, 100)
        left_margin = int((76 if multi else 26) * s)
        right_margin = int(24 * s)
        x0 = paper_x + left_margin
        available = max(1, paper_w - left_margin - right_margin)
        self.names_x = paper_x + int(10 * s)

        part_notes = [p.notes for p in parts]
        part_ranges = [score.part_bars(i) for i in range(len(parts))]
        bar_count = max((len(r) for r in part_ranges), default=0)
        self.part_ranges = part_ranges
        # Grace notes show the signs the print shows (a written ♯ already in force prints none,
        # a plain degree after a ♯ prints ♮), from the same rule as the main notes.
        from .engraving import shown_grace_accidentals
        self.part_graces = []
        for index, notes in enumerate(part_notes):
            signs = shown_grace_accidentals(score, index)
            self.part_graces.append([tuple(
                tuple((sign, degree, octave) for (_, degree, octave), sign in zip(grace_tokens(text), shown))
                for text, shown in ((n.grace_before, signs[row][0]), (n.grace_after, signs[row][1])))
                for row, n in enumerate(notes)])
        self.note_graces = self.part_graces[active]
        positions = []
        for notes in part_notes:
            out, position = [], 0
            for note in notes:
                out.append(position)
                position += note.ticks
            positions.append(out)
        self.part_positions = positions
        # Per bar: onset grid shared by all parts, widths from the notes that start there.
        bar_grids = []
        bar_widths = []
        for bar in range(bar_count):
            length = score.first_bar_ticks if bar == 0 else score.bar_ticks
            start_tick = 0 if bar == 0 else score.first_bar_ticks + (bar - 1) * score.bar_ticks
            onsets = {}
            for p, ranges in enumerate(part_ranges):
                if bar >= len(ranges):
                    continue
                a, b = ranges[bar]
                for row in range(a, b):
                    offset = positions[p][row] - start_tick
                    need = note_head_need(part_notes[p][row], self.part_graces[p][row], variant)
                    onsets[offset] = max(onsets.get(offset, 0), need)
            ticks = sorted(onsets)
            segments = []
            for i, tick in enumerate(ticks):
                end = ticks[i + 1] if i + 1 < len(ticks) else max(length, tick + 1)
                segments.append((tick, max(onsets[tick], (end - tick) * 40 / TICKS_PER_QUARTER) * s))
            used = sum(w for _, w in segments)
            minimum = 144 * s * (length / score.bar_ticks if bar == 0 and score.pickup else 1)
            bar_grids.append(segments)
            bar_widths.append(max(minimum, used + 32 * s))
        self._row_cache = {}
        # Systems.
        per_line = score.bars_per_line
        rows_of_bars, line, used = [], [], 0.0
        for bar in range(bar_count):
            w = bar_widths[bar]
            if line and ((per_line and len(line) >= per_line) or (not per_line and used + w > available)):
                rows_of_bars.append(line)
                line, used = [], 0.0
            line.append(bar)
            used += w
        if line:
            rows_of_bars.append(line)
        # Header.
        y = self._layout_header(paper_x + int(26 * s), paper_w - int(50 * s)) + int(10 * s)
        self.systems = []
        self.rects = []
        self.part_rects = [[QRect() for _ in notes] for notes in part_notes]
        self.part_labels = [[None] * len(notes) for notes in part_notes]          # (baseline point, width)
        self.part_onsets = [[(0, 0) for _ in notes] for notes in part_notes]     # (bar, offset in bar)
        self.part_lines = [[None] * len(notes) for notes in part_notes]          # system of each note
        from .engraving import shown_accidentals
        self.part_accidentals = [shown_accidentals(score, p) for p in range(len(parts))]
        from .engraving import lyric_map
        self.part_lyrics = [lyric_map(p.notes, p.lyrics) for p in parts]
        self.part_centers = [[0.0 for _ in notes] for notes in part_notes]
        self.note_lines = [0] * len(part_notes[active])
        self.bar_rects, self.bar_bottoms = [], []
        self.bar_frames = {}
        self.line_rects, self.line_bottoms = [], []
        self.line_links, self.line_hairpins, self.line_tuplets = [], [], []
        self.line_spans, self.line_baselines, self.line_dynamic_offsets = [], [], []
        self.bar_onset_x = {}
        items = self._bar_items(score, bar_count)
        # What is drawn: a print-only sign that repeats an automatic one is not (engraving.py).
        from .engraving import repeat_end_bars, without_redundant
        items = without_redundant(items)
        self.bar_items_cache = items
        self.repeat_ends = repeat_end_bars(items)
        from .engraving import coda_start
        coda_bar = coda_start(items)
        gap = max(4, int(18 * s))
        for number, bars in enumerate(rows_of_bars):
            base_total = sum(bar_widths[b] for b in bars)
            # Full lines are justified; the last line keeps its natural width (ragged-last, as
            # the print does), so a short last line is not stretched and its slurs stay small.
            last_line = number == len(rows_of_bars) - 1
            extra = 0.0 if last_line else max(0.0, (available - base_total) / len(bars))
            # Horizontal positions first: they do not depend on the vertical metrics, and the
            # free-text labels need them to choose their rows.
            bar_x, spans, x = {}, {}, float(x0)
            for bar in bars:
                w = bar_widths[bar] + extra
                start_tick = 0 if bar == 0 else score.first_bar_ticks + (bar - 1) * score.bar_ticks
                length = score.first_bar_ticks if bar == 0 else score.bar_ticks
                segments = bar_grids[bar]
                # The dots of a ‖: take room at the bar's start: keep the first note's slide
                # or accidental clear of them.
                starts = items[bar][0] if bar < len(items) else ()
                dots = "bar.repeat_start" in starts or (bar > 0 and bar - 1 < len(items)
                                                         and "bar.repeat_both" in items[bar - 1][1])
                lead = (26 if dots else 16) * s
                inner = w - 16 * s - lead
                total = sum(sw for _, sw in segments) or 1
                xs, cx = {}, x + lead
                for tick, sw in segments:
                    xs[tick] = cx
                    cx += inner * sw / total
                xs[length] = x + w - 16 * s
                bar_x[bar] = (x, w, xs)
                for p, ranges in enumerate(part_ranges):
                    if bar >= len(ranges):
                        continue
                    for r in range(*ranges[bar]):
                        offset = positions[p][r] - start_tick
                        end = offset + part_notes[p][r].ticks
                        left = xs.get(offset, x + 16 * s)
                        right = xs.get(end) or self._x_at(xs, end, x + w - 16 * s)
                        spans[p, r] = (bar, offset, left, right)
                x += w
            labels = self._label_rows(part_notes, spans, variant, paper_x + 8 * s, paper_x + paper_w - 8 * s)
            # Vertical metrics per part.
            volta = any(any(i.startswith("volta.") and i != "volta.end" for i in items[b][0]) for b in bars)
            nav = any(any(symbols.SYMBOLS[i].category == "navigation" for i in items[b][0] + items[b][1]) for b in bars) \
                or coda_bar in bars           # the automatic "⊕ Coda" label needs the same room
            top = y
            rows = {}
            cursor = y + (22 * s if volta else 0) + (18 * s if nav and not volta else 0)
            volta_y = y + 4 * s
            nav_y = y + (8 * s if not volta else 30 * s)
            if nav and volta:
                cursor += 16 * s
            for p in range(len(parts)):
                tiers = 1 + max((t for (q, _), (t, _, _) in labels.items() if q == p), default=0)
                above, below, lanes = self._row_metrics(p, bars, part_notes[p], part_ranges[p], variant, tiers)
                row_top = cursor
                baseline = row_top + above * s
                row = Row(p, row_top, baseline, baseline + below * s)
                for key, value in lanes.items():
                    if key == "spans":
                        row.span_lanes = {f: baseline - v * s for f, v in value.items()}
                    elif key in ("arc_y", "tuplet_y", "text_y"):
                        setattr(row, key, baseline - value * s)
                    else:
                        setattr(row, key, baseline + value * s)
                rows[p] = row
                cursor = row.bottom + (8 * s if p + 1 < len(parts) else 0)
            height = max(1, int(cursor - top + 6 * s))
            system = System(number, bars, QRect(x0, int(top), 1, height), rows, volta_y, nav_y)
            for bar in bars:
                x, w, xs = bar_x[bar]
                frame = QRect(int(x), int(top), max(1, int(x + w) - int(x)), height)
                self.bar_frames[bar] = frame
                self.bar_onset_x[bar] = xs
                for p, ranges in enumerate(part_ranges):
                    if bar >= len(ranges):
                        continue
                    a, b = ranges[bar]
                    row_geom = rows[p]
                    for r in range(a, b):
                        _, offset, left, right = spans[p, r]
                        rect = QRect(int(left), int(row_geom.top + 18 * s), max(1, int(right - left)),
                                     max(1, int(row_geom.bottom - row_geom.top - 22 * s)))
                        self.part_rects[p][r] = rect
                        self.part_onsets[p][r] = (bar, offset)
                        self.part_lines[p][r] = number
                        if (p, r) in labels:
                            tier, label_x, label_w = labels[p, r]
                            # Tier 0 sits on the text lane; further tiers stack upward.
                            baseline_y = row_geom.text_y + (5 - self.label_step * tier) * s
                            self.part_labels[p][r] = (QPointF(label_x, baseline_y), label_w)
                if bar < len(part_ranges[active]):
                    a, b = part_ranges[active][bar]
                    self.bar_rects.append((frame, bar, list(range(a, b))))
                    self.bar_bottoms.append(frame.bottom())
                    for r in range(a, b):
                        self.note_lines[r] = number
                x += w
            system.rect = QRect(x0, int(top), max(1, int(x - x0)), height)
            self.systems.append(system)
            self.line_rects.append(system.rect)
            self.line_bottoms.append(system.rect.bottom())
            self.line_baselines.append(rows[active].baseline)
            self.line_dynamic_offsets.append(rows[active].dynamic_y - rows[active].baseline - 11 * s)
            self.line_links.append([])
            self.line_hairpins.append([])
            self.line_tuplets.append([])
            self.line_spans.append([])
            y = system.rect.bottom() + gap
        self.rects = self.part_rects[active] if part_notes else []
        for p, notes in enumerate(part_notes):
            for r in range(len(notes)):
                self.part_centers[p][r] = self._center(p, r)
        self.empty_bar_rect, self.empty_button_rect = QRect(), QRect()
        if not any(part_notes):
            self.empty_bar_rect = QRect(x0, int(y), available, max(80, int(114 * s)))
            button_width = min(available - int(12 * s), int(208 * s))
            self.empty_button_rect = QRect(x0 + (available - button_width) // 2, int(y + 24 * s),
                                           max(1, button_width), max(24, int(36 * s)))
            y = self.empty_bar_rect.bottom() + gap
        self.content_width = max(self.viewport().width(), paper_x + paper_w + DESK_MARGIN)
        self.content_height = max(self.viewport().height(), int(y + 30 * s))
        self.paper_rect.setHeight(int(self.content_height - 2 * DESK_MARGIN))
        self._layout_connections()
        self.updateGeometries()
        self.viewport().update()

    @staticmethod
    def _x_at(xs, tick, fallback):
        keys = sorted(xs)
        for a, b in zip(keys, keys[1:]):
            if a <= tick <= b:
                return xs[a] + (xs[b] - xs[a]) * (tick - a) / max(1, b - a)
        return fallback

    def _bar_items(self, score, count):
        notes = score.parts[0].notes
        out = []
        for bar in range(count):
            if bar < len(self.part_ranges[0]):
                a, b = self.part_ranges[0][bar]
                out.append((notes[a].bar_start, notes[b - 1].bar_end))
            else:
                out.append(((), ()))
        return out

    def _row_metrics(self, part, bars, notes, ranges, variant, text_tiers=1):
        """(above, below, lanes) in unscaled px for one part in one system."""
        above_need = 34.0
        below_need = 24.0
        has_dyn = has_hair = has_pedal = has_trem = has_8vb = has_text = False
        span_families = set()
        cache = self._row_cache.get(part)
        if cache is None:
            cache = (self._covered(notes, "arc"), self._covered(notes, "tuplet"), self._family_rows(notes))
            self._row_cache[part] = cache
        under_arc, under_tuplet, families = cache
        arc_lift = 0.0
        for bar in bars:
            if bar >= len(ranges):
                continue
            a, b = ranges[bar]
            for r in range(a, b):
                note = notes[r]
                has_text = has_text or (bool(note.text) and not (part == 0 and r == 0 and self._header_text))
                above, left, right, below = _marks_by_place(note)
                base = 22 + 6 * max(0, note.octave)
                if r in under_arc:
                    arc_lift = max(arc_lift, base)
                    base += 12
                if r in under_tuplet:
                    base += 14
                stack = base + sum(glyphs.mark_height(sym) + 2 for sym in above) + sum(glyphs.stack_gaps(above))
                above_need = max(above_need, stack + 6)
                low = 22 + 4 * beams(note) + 6 * max(0, -note.octave)
                below_need = max(below_need, low + 4)
                for sym in below:
                    if glyphs.is_dynamic(sym):
                        has_dyn = True
                    elif sym.category == "pedal":
                        has_pedal = True
                    else:
                        has_trem = True
                for sid in families.get(r, ()):
                    sym = symbols.SYMBOLS[sid]
                    if sym.family == "hairpin":
                        has_hair = True
                        if sym.canvas.startswith("text:"):
                            has_dyn = True
                    elif sym.id == "line.8vb":
                        has_8vb = True
                    elif sym.family in ("ottava", "trill", "text"):
                        span_families.add(sym.family)
        lanes = {}
        below = below_need
        if has_trem:
            lanes["trem_y"] = below + 4
            below += 12
        if has_dyn or has_hair:
            lanes["dynamic_y"] = below + 9
            below += 20
        if has_hair:
            lanes["hairpin_y"] = below + 7
            below += 16
        if has_8vb:
            lanes["below_span_y"] = below + 6
            below += 16
        if has_pedal:
            lanes["pedal_y"] = below + 8
            below += 18
        lyrics = self.part_lyrics[part] if part < len(getattr(self, "part_lyrics", ())) else {}
        if lyrics and any(r in lyrics for bar in bars if bar < len(ranges) for r in range(*ranges[bar])):
            lanes["lyric_y"] = below + 10
            below += 22
        lanes["arc_y"] = arc_lift + 3 if arc_lift else 25
        lanes["tuplet_y"] = max(lanes["arc_y"] + 22, 40) if arc_lift else 36
        above = above_need
        spans = {}
        for family in ("ottava", "trill", "text"):
            if family in span_families:
                spans[family] = above + 6
                above += 16
        lanes["spans"] = spans
        if has_text:
            lanes["text_y"] = above + 10
            # Further rows of text stack upward, and the bar-number strip at the top of the row
            # stays clear so a label running into the next bar never covers its number.
            above += 20 + getattr(self, "label_step", LABEL_TIER) * (text_tiers - 1) + 16
        return above, below + 4, lanes

    def label_font(self, variant):
        """Font of the free text above notes (layout measures with the font paint uses)."""
        return glyphs.font("cjk", 13 * self.scale, variant)

    def _label_rows(self, part_notes, spans, variant, left_limit, right_limit):
        """(part, row) -> (row of text, x, width) for the free text of one system.

        A label starts just left of its note (as printed) and goes on the lowest row of text
        where it clears the label before it; a long label on a crowded beat moves up a row
        instead of spreading the notes, so note spacing never depends on the labels.
        """
        s = self.scale
        metrics = QFontMetricsF(self.label_font(variant))
        # One row of text is the font's full line height (CJK fonts differ by platform) plus air.
        self.label_step = max(LABEL_TIER, metrics.height() / s + 2)
        out, row_ends = {}, {}
        for (p, r), (_, _, left, right) in sorted(spans.items()):
            text = part_notes[p][r].text
            if not text or (p, r) == (0, 0) and self._header_text:
                continue        # tempo words on the first note print in the header line
            graces = self.part_graces[p][r]
            prefix = (14 * len(graces[0]) + 16) * s if graces[0] else 0
            ornament = sum((14 * len(group) + 16) * s for group in graces if group)
            main = max(1, max(1, int(right - left)) - ornament)      # as _center / _main_width
            width = metrics.horizontalAdvance(text)
            x = int(left) + prefix + min(main / 2, 17 * s) - 7 * s
            x = max(left_limit, min(x, right_limit - width))         # keep it on the paper
            ends = row_ends.setdefault(p, [])
            tier = next((t for t, end in enumerate(ends) if x >= end + LABEL_GAP * s), len(ends))
            if tier == len(ends):
                ends.append(0.0)
            ends[tier] = x + width
            out[p, r] = (tier, x, width)
        return out

    @staticmethod
    def _family_rows(notes):
        """row -> line ids covering it (so a line continuing on a new system keeps its lane)."""
        from .notation_rules import span_pairs
        out = {}
        for family, a, b, sid in span_pairs(notes):
            if family in ("hairpin", "ottava", "trill", "text"):
                for r in range(a, b + 1):
                    out.setdefault(r, []).append(sid)
        return out

    @staticmethod
    def _covered(notes, kind):
        rows = set()
        if kind == "arc":
            from .notation_rules import span_pairs
            for family, a, b, _ in span_pairs(notes):
                if family == "slur":
                    rows.update(range(a, b + 1))
            for i, note in enumerate(notes):
                if note.tie and i + 1 < len(notes):
                    rows.update((i, i + 1))
        else:
            start = None
            for i, note in enumerate(notes):
                if note.tuplet_start:
                    start = i
                if note.tuplet_end and start is not None:
                    rows.update(range(start, i + 1))
                    start = None
        return rows

    def _layout_header(self, left, width):
        """Cache plain-text wrapping so header changes also reflow the music."""
        score, s = self.score_model.score, self.scale
        self.header_blocks = []
        top = DESK_MARGIN + max(8, int(22 * s))
        y = top
        title = score.title or tr("未命名樂曲")
        title_font = QFont(ui_font_family())
        title_font.setPixelSize(max(9, int(21 * s)))
        title_font.setWeight(QFont.Weight.DemiBold)
        flags = Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap | Qt.TextFlag.TextWrapAnywhere
        height = max(QFontMetrics(title_font).height(),
                     QFontMetrics(title_font).boundingRect(QRect(0, 0, width, 100000), flags, title).height())
        self.header_blocks.append((QRect(left, y, width, height), title, title_font, flags))
        y += height + max(4, int(8 * s))
        small = QFont(ui_font_family())
        small.setPixelSize(max(7, int(12 * s)))
        from .model import key_label
        words = self._header_text
        meta = f"1 = {key_label(score.key)}    {score.meter}    " + (f"{words}    " if words else "") + f"♩ = {score.tempo}"
        line_top = y
        bottom = y + QFontMetrics(small).height()
        if score.composer:
            flags = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap | Qt.TextFlag.TextWrapAnywhere
            half = width // 2
            h = max(QFontMetrics(small).height(),
                    QFontMetrics(small).boundingRect(QRect(0, 0, half, 100000), flags, score.composer).height())
            self.header_blocks.append((QRect(left + width - half, line_top, half, h), score.composer, small, flags))
            bottom = max(bottom, line_top + h)
        flags = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        self.header_blocks.append((QRect(left, line_top, width // 2, QFontMetrics(small).height()), meta, small, flags))
        y = bottom + max(3, int(4 * s))
        self.header_rect = QRect(left, top, width, y - top)
        return y

    def _center(self, part, row):
        rect = self.part_rects[part][row]
        before = self.part_graces[part][row][0]
        prefix = (14 * len(before) + 16) * self.scale if before else 0
        main = self._main_width(part, row)
        # The same offset from the onset for every note, long or short, so notes that start
        # together line up across parts.
        return rect.left() + prefix + min(main / 2, 17 * self.scale)

    def beat_x(self, part, row, beat):
        """x of a long note's ``beat``-th dash (or a long rest's zero): on the shared time grid."""
        bar, offset = self.part_onsets[part][row]
        xs = self.bar_onset_x.get(bar, {})
        rect = self.part_rects[part][row]
        tick = offset + beat * TICKS_PER_QUARTER
        x = xs.get(tick)
        if x is None:
            x = self._x_at(xs, tick, rect.right())
        return x + min(self._main_width(part, row) / 2, 17 * self.scale)

    def _main_width(self, part, row):
        ornament = sum((14 * len(group) + 16) * self.scale for group in self.part_graces[part][row] if group)
        return max(1, self.part_rects[part][row].width() - ornament)

    def note_center(self, row):
        return self.part_centers[self.score_model.part][row] if self.part_centers else 0.0

    def note_main_width(self, row):
        return self._main_width(self.score_model.part, row)

    long_beats = staticmethod(long_beats)

    def row_of(self, part, row) -> Row | None:
        bar_row = self.note_line(part, row)
        return self.systems[bar_row].rows.get(part) if bar_row is not None else None

    def note_line(self, part, row):
        lines = getattr(self, "part_lines", None)
        if lines is not None and part < len(lines) and row < len(lines[part]):
            return lines[part][row]
        rect = self.part_rects[part][row]
        for number, system in enumerate(self.systems):
            if system.rect.top() <= rect.top() <= system.rect.bottom():
                return number
        return None

    # ---- lines that may continue across systems
    def _layout_connections(self):
        from .notation_rules import span_pairs
        s = self.scale
        score = self.score_model.score
        self.score_model.links, self.score_model.hairpins, self.score_model.tuplets = [], [], []
        for p, part in enumerate(score.parts):
            notes = part.notes
            if not notes:
                continue
            lines = [self.note_line(p, r) for r in range(len(notes))]
            pairs = span_pairs(notes)
            links = [(a, b, False) for family, a, b, _ in pairs if family == "slur"]
            links += [(i, i + 1, True) for i, n in enumerate(notes) if n.tie and i + 1 < len(notes)]
            for a, b, tie in links:
                self._add_arcs(p, lines, a, b, tie)
            tuplets, start = [], None
            for i, note in enumerate(notes):
                if note.tuplet_start:
                    start = i
                if note.tuplet_end and start is not None:
                    tuplets.append((start, i, 3))
                    start = None
            for a, b, number in tuplets:
                for line in range(lines[a], lines[b] + 1):
                    frame = self.line_rects[line]
                    row = self.systems[line].rows[p]
                    x1 = self.part_centers[p][a] - 10 * s if line == lines[a] else frame.left() + 10 * s
                    x2 = self.part_centers[p][b] + 10 * s if line == lines[b] else frame.right() - 10 * s
                    x2 = max(x1 + 24 * s, x2)
                    yy, center = row.tuplet_y, (x1 + x2) / 2
                    path = QPainterPath(QPointF(x1, yy + 5 * s))
                    path.lineTo(x1, yy)
                    path.lineTo(center - 7 * s, yy)
                    path.moveTo(center + 7 * s, yy)
                    path.lineTo(x2, yy)
                    path.lineTo(x2, yy + 5 * s)
                    text_rect = QRect(int(center - 8 * s), int(yy - 10 * s), max(1, int(16 * s)), max(1, int(20 * s)))
                    self.line_tuplets[line].append((path, text_rect, str(number)))
            for family, a, b, start_id in pairs:
                symbol = symbols.SYMBOLS[start_id]
                if family == "hairpin":
                    self._add_hairpin(p, lines, a, b, symbol)
                elif family == "gliss":
                    for line in range(lines[a], lines[b] + 1):
                        row = self.systems[line].rows[p]
                        x1 = self.part_centers[p][a] + 9 * s if line == lines[a] else self.line_rects[line].left() + 8 * s
                        x2 = self.part_centers[p][b] - 9 * s if line == lines[b] else self.line_rects[line].right() - 8 * s
                        self.line_spans[line].append(("gliss", p, symbol, x1, x2, row.baseline, line == lines[a]))
                elif family in ("ottava", "trill", "text"):
                    for line in range(lines[a], lines[b] + 1):
                        row = self.systems[line].rows[p]
                        y = row.below_span_y if symbol.id == "line.8vb" else row.span_lanes.get(family, row.top + 8 * s)
                        x1 = self.part_centers[p][a] - 8 * s if line == lines[a] else self.line_rects[line].left() + 8 * s
                        x2 = self.part_centers[p][b] + 8 * s if line == lines[b] else self.line_rects[line].right() - 8 * s
                        self.line_spans[line].append(("line", p, symbol, x1, x2, y, line == lines[a], line == lines[b]))
            if p == self.score_model.part:
                self.score_model.links = links
                self.score_model.tuplets = tuplets
                self.score_model.hairpins = [(a, b, "crescendo" if "cresc" in sid else "diminuendo")
                                             for family, a, b, sid in pairs if family == "hairpin"]

    def _add_arcs(self, p, lines, a, b, tie):
        s = self.scale
        for line in range(lines[a], lines[b] + 1):
            frame = self.line_rects[line]
            row = self.systems[line].rows[p]
            x1 = self.part_centers[p][a] + (3 if tie else 0) * s if line == lines[a] else frame.left() + 10 * s
            x2 = self.part_centers[p][b] - (3 if tie else 0) * s if line == lines[b] else frame.right() - 10 * s
            if x2 <= x1:
                x2 = x1 + 12 * s
            y = row.arc_y
            from .glyphs import arc_path
            self.line_links[line].append(arc_path(x1, x2, y, (6 if tie else 9) * s))

    def _add_hairpin(self, p, lines, a, b, symbol):
        s = self.scale
        segments = []
        for line in range(lines[a], lines[b] + 1):
            frame = self.line_rects[line]
            x1 = self.part_centers[p][a] + 3 * s if line == lines[a] else frame.left() + 10 * s
            x2 = self.part_centers[p][b] - 3 * s if line == lines[b] else frame.right() - 10 * s
            segments.append((line, x1, max(x1 + 12 * s, x2)))
        if symbol.canvas.startswith("text:"):
            for line, x1, x2 in segments:
                row = self.systems[line].rows[p]
                self.line_spans[line].append(("textpin", p, symbol, x1, x2, row.dynamic_y, line == lines[a], line == lines[b]))
            return
        total = sum(x2 - x1 for _, x1, x2 in segments) or 1
        progress = 0
        for line, x1, x2 in segments:
            first_open, last_open = progress / total, (progress + x2 - x1) / total
            if "dim" in symbol.id:
                first_open, last_open = 1 - first_open, 1 - last_open
            row = self.systems[line].rows[p]
            path = glyphs.hairpin_path(x1, x2, row.hairpin_y, s, first_open, last_open)
            self.line_hairpins[line].append(path)
            progress += x2 - x1
