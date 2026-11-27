"""Painting the canvas from the cached layout; only visible systems are visited."""
from __future__ import annotations

from bisect import bisect_right

from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QFont, QPainter, QPainterPath, QPen, QPolygonF

from . import glyphs, symbols
from .i18n import tr, ui_font_family
from .score_layout import _marks_by_place, beams, long_beats


class PaintMixin:
    def _visible_systems(self):
        first = bisect_right(self.line_bottoms, self.verticalOffset() - 1)
        bottom = self.verticalOffset() + self.viewport().height()
        for number in range(first, len(self.systems)):
            if self.systems[number].rect.top() > bottom:
                break
            yield number

    def paintEvent(self, event):
        c = self.colors
        p = QPainter(self.viewport())
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.viewport().rect(), c.desk)
        p.translate(-self.horizontalOffset(), -self.verticalOffset())
        paper = QRectF(self.paper_rect)
        # The only shadow (design §4.2): three translucent rectangles, no graphics effect.
        p.setPen(Qt.PenStyle.NoPen)
        for grow, color in ((4, c.shadow_far), (2, c.shadow_far), (1, c.shadow_near)):
            p.setBrush(color)
            p.drawRoundedRect(paper.adjusted(-grow + 1, -grow + 2, grow - 1, grow + 1), 3 + grow, 3 + grow)
        p.setBrush(c.paper)
        p.setPen(QPen(c.paper_edge, 1) if c.dark else Qt.PenStyle.NoPen)
        p.drawRoundedRect(paper, 2, 2)
        if self.header_rect.bottom() >= self.verticalOffset():
            p.setPen(c.ink)
            for rect, text, font, flags in self.header_blocks:
                p.setFont(font)
                p.drawText(rect, flags, text)
        score = self.score_model.score
        if not any(part.notes for part in score.parts):
            self._paint_empty(p)
            p.end()
            return
        for number in self._visible_systems():
            self._paint_system(p, number)
        for number in self._visible_systems():
            self._draw_system_marks(p, number)
        self._paint_caret(p)
        self._paint_playback(p)
        p.end()

    def _paint_empty(self, p):
        c, s = self.colors, self.scale
        p.setBrush(c.accentsoft)
        p.setPen(QPen(c.accent, max(1, s)))
        p.drawRoundedRect(self.empty_button_rect, 6 * s, 6 * s)
        p.setPen(c.accenttext)
        font = QFont(ui_font_family())
        font.setPixelSize(max(9, int(14 * s)))
        p.setFont(font)
        p.drawText(self.empty_button_rect, Qt.AlignmentFlag.AlignCenter, tr("＋ 新增小節"))
        p.setPen(c.text3)
        font.setPixelSize(max(9, int(13 * s)))
        p.setFont(font)
        hint = QRect(self.empty_bar_rect.left(), self.empty_button_rect.bottom() + int(14 * s),
                     self.empty_bar_rect.width(), int(48 * s))
        p.drawText(hint, Qt.AlignmentFlag.AlignHCenter | Qt.TextFlag.TextWordWrap,
                   tr("按數字鍵 1–7 輸入音符，0 是休止符；Q W E R A S 換時值"))

    # ---- one system
    def _paint_system(self, p, number):
        c, s = self.colors, self.scale
        system = self.systems[number]
        score = self.score_model.score
        active = self.score_model.part
        multi = len(score.parts) > 1
        rect = system.rect
        rows = system.rows
        first_row, last_row = rows[0], rows[len(score.parts) - 1]
        staff_top = first_row.baseline - 20 * s
        staff_bottom = last_row.baseline + 22 * s
        if multi:
            row = rows[active]
            band = QRectF(rect.left(), row.top + 10 * s, rect.width(), row.bottom - row.top - 12 * s)
            p.fillRect(band, c.part_tint)
            p.fillRect(QRectF(self.paper_rect.left(), row.top + 10 * s, 3, row.bottom - row.top - 12 * s), c.accent)
            self._paint_brace(p, rect.left() - 8 * s, staff_top, staff_bottom)
            font = QFont(ui_font_family())
            font.setPixelSize(max(8, int(12 * s)))
            for part, row in rows.items():
                name = score.parts[part].name or tr("第 {n} 聲部").format(n=part + 1)
                font.setWeight(QFont.Weight.Bold if part == active else QFont.Weight.Normal)
                p.setFont(font)
                p.setPen(c.accenttext if part == active else c.text2)
                box = QRectF(self.names_x, row.baseline - 10 * s, rect.left() - self.names_x - 14 * s, 20 * s)
                p.drawText(box, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                           p.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, int(box.width())))
        # selected bars / barline
        view = self
        for bar in system.bars:
            frame = self.bar_frames[bar]
            if view.selection_kind == "bars" and bar in view.selected_bars:
                p.setBrush(c.sel_measure)
                p.setPen(QPen(c.accent, 1.5))
                p.drawRoundedRect(QRectF(frame.left() + 2, rows[0].top + 14 * s, frame.width() - 4,
                                         frame.bottom() - rows[0].top - 18 * s), 6, 6)
        # bars: numbers, lines, items
        items = self.bar_items_cache
        last_bar = len(self.part_ranges[0]) - 1 if self.part_ranges else -1
        number_top = rows[0].top
        for index, bar in enumerate(system.bars):
            frame = self.bar_frames[bar]
            starts, ends = items[bar]
            self._paint_bar_number(p, bar, frame, number_top)
            if index == 0 and bar == 0 and "bar.repeat_start" not in starts:
                self._barline(p, "single", frame.left(), staff_top, staff_bottom, rows, bar, -1)
            elif index == 0:
                previous = items[bar - 1][1] if bar else ()
                style = "repeat_start" if "bar.repeat_start" in starts or "bar.repeat_both" in previous else "single"
                self._barline(p, style, frame.left() + (2 * s if style != "single" else 0), staff_top, staff_bottom,
                              rows, bar, -1)
            nxt = items[bar + 1][0] if bar + 1 < len(items) else ()
            style = self._end_style(bar, starts, ends, nxt, bar == last_bar)
            right = frame.right() + (0 if style == "single" else 0)
            self._barline(p, style, right, staff_top, staff_bottom, rows, bar, bar)
            if "bar.repeat_start" in nxt and style not in ("repeat_end", "repeat_both") and bar + 1 < len(items) \
                    and index + 1 < len(system.bars):
                self._barline(p, "repeat_start", right + 2 * s, staff_top, staff_bottom, rows, bar + 1, -1)
            self._paint_bar_marks(p, system, bar, frame, starts, ends)
        if self.selection_kind == "barline":
            x = self._boundary_x(self.selected_barline)
            if x is not None and system.rect.left() - 2 <= x <= system.rect.right() + 2:
                p.setPen(QPen(c.halo, 6 * s))
                p.drawLine(QPointF(x, staff_top - 4 * s), QPointF(x, staff_bottom + 4 * s))
                p.setPen(QPen(c.accent, 2))
                p.drawLine(QPointF(x, staff_top - 4 * s), QPointF(x, staff_bottom + 4 * s))
        self._paint_preview_target(p, system, staff_top, staff_bottom)
        # notes
        for part, row in rows.items():
            notes = score.parts[part].notes
            ranges = self.part_ranges[part]
            for bar in system.bars:
                if bar >= len(ranges):
                    continue
                a, b = ranges[bar]
                for r in range(a, b):
                    self._paint_note(p, part, r, notes, row)
            self._paint_beams(p, part, system, row)
            self._paint_problems(p, part, system, row)

    def _paint_brace(self, p, x, top, bottom):
        c, s = self.colors, self.scale
        mid = (top + bottom) / 2
        w = 7 * s
        path = QPainterPath(QPointF(x, top))
        path.cubicTo(x - w, top + 4 * s, x, mid - 12 * s, x - w, mid)
        path.cubicTo(x, mid + 12 * s, x - w, bottom - 4 * s, x, bottom)
        p.setPen(QPen(c.ink, 1.8 * s))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)

    def _paint_bar_number(self, p, bar, frame, top=None):
        c, s = self.colors, self.scale
        top = frame.top() if top is None else top
        score = self.score_model.score
        text = tr("弱起") if score.pickup and bar == 0 else str(bar + (0 if score.pickup else 1))
        if bar in self.problem_bars:
            text += " ⚠"
        font = QFont(ui_font_family())
        font.setPixelSize(max(7, int(11 * s)))
        p.setFont(font)
        box = QRectF(frame.left() + 3 * s, top + 1 * s, 60 * s, 14 * s)
        selected = self.selection_kind == "bars" and bar in self.selected_bars
        if selected:
            width = p.fontMetrics().horizontalAdvance(text) + 10 * s
            pill = QRectF(box.left() - 2 * s, box.top(), width, 14 * s)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(c.accent)
            p.drawRoundedRect(pill, 7 * s, 7 * s)
            p.setPen(c.onaccent)
            p.drawText(pill, Qt.AlignmentFlag.AlignCenter, text)
            return
        p.setPen(c.warn if bar in self.problem_bars else c.text3)
        p.drawText(box, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)

    def _end_style(self, bar, starts, ends, nxt, last):
        # The first ending's :‖ prints at its end (the stored repeat end is the body's end).
        if "bar.repeat_both" in ends:
            return "repeat_both"
        if "bar.repeat_end" in ends and not any(v in nxt for v in ("volta.1",)):
            return "repeat_end"
        if self._prints_repeat_end(bar):
            return "repeat_end"
        for style in ("final", "double", "dashed"):
            if f"bar.{style}" in ends:
                return style
        if last:
            return "final"
        from .engraving import end_barline
        return end_barline(self.bar_items_cache, bar, last) or "single"

    def _prints_repeat_end(self, bar):
        """True at the end of every ending except the last one of a volta group."""
        items = self.bar_items_cache
        if bar + 1 >= len(items):
            return False
        nxt = items[bar + 1][0]
        return any(v in nxt for v in ("volta.2", "volta.3", "volta.4"))

    def _barline(self, p, style, x, top, bottom, rows, bar, end_bar):
        c = self.colors
        color = c.warn if end_bar >= 0 and end_bar in self.problem_barlines else c.ink
        glyphs.draw_barline(p, style, x, top, bottom, self.scale, color,
                            dot_rows=[row.baseline for row in rows.values()])

    def _paint_bar_marks(self, p, system, bar, frame, starts, ends):
        c, s = self.colors, self.scale
        variant = self.score_model.score.print_variant
        voltas = [i for i in starts if i.startswith("volta.") and i not in ("volta.end", "volta.final")]
        if voltas:
            number = voltas[0].split(".")[1]
            # The print replaces the whole label with 「結」 (\jsVoltaJie): the same here.
            label = glyphs.tr_jie(variant) if "volta.final" in starts else number + "."
            end_bar = self._volta_end(bar)
            x1 = frame.left() + 2 * s
            right_frame = self.bar_frames.get(end_bar, frame)
            same_system = right_frame.top() == frame.top()
            x2 = (right_frame.right() if same_system else system.rect.right()) - 3 * s
            y = system.volta_y + 1 * s
            p.setPen(QPen(c.ink, max(0.8, 1.1 * s)))
            p.drawLine(QPointF(x1, y + 14 * s), QPointF(x1, y))
            p.drawLine(QPointF(x1, y), QPointF(x2, y))
            # As LilyPond: the bracket closes when its bar line is a repeat/double/final line,
            # and stays open after the last ending that runs on into a plain bar line.
            items = self.bar_items_cache
            end_items = items[end_bar] if end_bar < len(items) else ((), ())
            nxt = items[end_bar + 1][0] if end_bar + 1 < len(items) else ()
            closed = self._end_style(end_bar, end_items[0], end_items[1], nxt,
                                     end_bar == len(self.part_ranges[0]) - 1) != "single"
            if closed and same_system:
                p.drawLine(QPointF(x2, y), QPointF(x2, y + 14 * s))
            font = glyphs.font("sans", 11 * s)
            p.setFont(font)
            p.drawText(QPointF(x1 + 4 * s, y + 12 * s), label)
        from .engraving import coda_start, jump_text
        y = system.nav_y + 6 * s
        if coda_start(self.bar_items_cache) == bar:
            # "⊕ Coda" where the coda starts, as the print labels it.
            glyphs.draw_coda(p, frame.left() + 10 * s, y, s, c.ink)
            glyphs.draw_nav_text(p, "Coda", frame.left() + 20 * s, y, s, c.ink, "left")
        for item in (*starts, *ends):
            symbol = symbols.SYMBOLS[item]
            if symbol.category != "navigation":
                continue
            at_start = symbol.bar_edge == "start"
            if at_start:
                glyphs.draw_nav(p, symbol, frame.left() + 30 * s, y, s, c.ink, "left" if not symbol.canvas.startswith(
                    "shape:") else "center")
            elif item in ("nav.ds", "nav.dc"):
                glyphs.draw_nav_text(p, jump_text(self.bar_items_cache), frame.right() - 4 * s, y, s, c.ink, "right")
            else:
                glyphs.draw_nav(p, symbol, frame.right() - 4 * s, y, s, c.ink, "right")

    def _volta_end(self, bar):
        items = self.bar_items_cache
        for b in range(bar, len(items)):
            if b > bar and any(i in items[b][0] for i in ("volta.1", "volta.2", "volta.3", "volta.4")):
                return b - 1
            if "volta.end" in items[b][1]:
                return b
        return len(items) - 1

    def _is_last_volta(self, bar):
        items = self.bar_items_cache
        end = self._volta_end(bar)
        return "volta.end" in items[end][1]

    def _boundary_x(self, k):
        if k is None or k < 0:
            return None
        if k in self.bar_frames:
            return self.bar_frames[k].left()
        if k - 1 in self.bar_frames:
            return self.bar_frames[k - 1].right()
        return None

    # ---- notes
    def _paint_note(self, p, part, r, notes, row):
        c, s = self.colors, self.scale
        note = notes[r]
        cx = self.part_centers[part][r]
        cy = row.baseline
        active = part == self.score_model.part
        selected = active and self.selection_kind != "barline" and self.selectionModel().isSelected(self.model().index(r, 0))
        if active and r == self.hover_row and not selected:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(c.hover)
            p.drawRoundedRect(QRectF(cx - 11 * s, cy - 16 * s, 22 * s, 31 * s), 5 * s, 5 * s)
        if selected:
            p.setBrush(c.sel)
            p.setPen(QPen(c.accent, 1.5))
            p.drawRoundedRect(QRectF(cx - 11 * s, cy - 16 * s, 22 * s, 31 * s), 5 * s, 5 * s)
        playing = self.play_note == (part, r)
        color = c.accent if playing else (c.ink if not (active and self.flash_row == r) else c.accenttext)
        p.setPen(color)
        p.setFont(glyphs.font("number", 21 * s))
        p.drawText(QRectF(cx - 14 * s, cy - 16 * s, 28 * s, 32 * s), Qt.AlignmentFlag.AlignCenter, str(note.degree))
        accidental = self.part_accidentals[part][r] if r < len(self.part_accidentals[part]) else note.accidental
        if accidental:          # the same decision as the print (engraving.shown_accidentals)
            glyphs.draw_accidental(p, accidental, cx - 11 * s, cy - 5 * s, s * 0.9, color)
        p.setBrush(color)
        p.setPen(Qt.PenStyle.NoPen)
        if note.dotted and note.duration > 2:
            p.drawEllipse(QPointF(cx + 10 * s, cy + 1 * s), 1.8 * s, 1.8 * s)
        for octave in range(abs(note.octave)):
            y = cy - (20 + 6 * octave) * s if note.octave > 0 else cy + (15 + 4 * beams(note) + 4 + 6 * octave) * s
            p.drawEllipse(QPointF(cx, y), 1.8 * s, 1.8 * s)
        lb = long_beats(note)
        if lb > 1 and note.degree == 0:
            # A long rest is one 0 per beat (jianpu never extends a rest with dashes); the
            # export writes the same zeros.
            p.setPen(color)
            for beat in range(1, lb):
                x = self.beat_x(part, r, beat)
                p.drawText(QRectF(x - 14 * s, cy - 16 * s, 28 * s, 32 * s), Qt.AlignmentFlag.AlignCenter, "0")
        elif lb > 1:
            pen = QPen(color, 1.7 * s)
            p.setPen(pen)
            for beat in range(1, lb):
                x = self.beat_x(part, r, beat)      # on the shared time grid: aligned across parts
                p.drawLine(QPointF(x - 6 * s, cy), QPointF(x + 6 * s, cy))
        self._draw_marks(p, part, r, note, cx, cy, row, color if playing else c.ink)
        syllable = self.part_lyrics[part].get(r) if part < len(self.part_lyrics) else None
        if syllable and row.lyric_y:
            # Lyrics under the notes, one syllable per sung note (engraving.lyric_map = the print).
            p.setPen(c.ink)
            p.setFont(glyphs.font("cjk", 14 * s, self.score_model.score.print_variant))
            p.drawText(QRectF(cx - 20 * s, row.lyric_y - 10 * s, 40 * s, 20 * s), Qt.AlignmentFlag.AlignCenter, syllable)
        if note.text and row.text_y:
            # Free text above the note (the print writes ^"text" there, upright, left-aligned).
            p.setPen(c.ink)
            p.setFont(glyphs.font("cjk", 13 * s, self.score_model.score.print_variant))
            p.drawText(QPointF(cx - 7 * s, row.text_y + 5 * s), note.text)
        if note.grace_before or note.grace_after:
            self._draw_graces(p, part, r, cy, c.ink)

    def _draw_marks(self, p, part, row_index, note, cx, cy, row, color):
        if not note.marks:
            return
        s = self.scale
        variant = self.score_model.score.print_variant
        above, left, right, below = _marks_by_place(note)
        under_arc = self._under_arc(part, row_index)
        under_tuplet = note.tuplet != 0
        y = cy - (22 + 6 * max(0, note.octave)) * s
        if under_arc:
            y = min(y, row.arc_y - 4 * s) - 8 * s
        if under_tuplet:
            y = min(y, row.tuplet_y - 6 * s)
        for symbol in above:
            h = glyphs.mark_height(symbol) * s
            self._draw_symbol(p, symbol, cx, y - h / 2, color, variant)
            y -= h + 2 * s
        for index, symbol in enumerate(left):
            self._draw_symbol(p, symbol, cx - (16 + 10 * index) * s - (6 * s if note.accidental else 0), cy - 2 * s,
                              color, variant)
        for index, symbol in enumerate(right):
            self._draw_symbol(p, symbol, cx + (14 + 10 * index) * s, cy - 8 * s, color, variant)
        for symbol in below:
            if glyphs.is_dynamic(symbol):
                y_mark = row.dynamic_y
            elif symbol.category == "pedal":
                y_mark = row.pedal_y
            else:
                y_mark = row.trem_y or cy + 24 * s
            self._draw_symbol(p, symbol, cx, y_mark, color, variant)

    def _draw_symbol(self, p, symbol, x, y, color, variant):
        glyphs.draw_mark(p, symbol, x, y, self.scale, color, variant)

    def _under_arc(self, part, row):
        cache = self._arc_rows.get(part)
        if cache is None:
            cache = self._covered(self.score_model.score.parts[part].notes, "arc")
            self._arc_rows[part] = cache
        return row in cache

    def _draw_graces(self, p, part, row, cy, color):
        s = self.scale
        rect = self.part_rects[part][row]
        before, after = self.part_graces[part][row]
        p.setPen(QPen(color, max(1, s)))
        p.setBrush(color)
        for group, start in ((before, rect.left()), (after, rect.right() - (14 * len(after) + 16) * s + 12 * s)):
            if not group:
                continue
            centers = []
            for number, (accidental, degree, octave) in enumerate(group):
                x, y = start + (7 + 14 * number) * s, cy - 7 * s
                centers.append(x)
                p.setPen(color)
                p.setFont(glyphs.font("number", 11 * s))
                p.drawText(QRectF(x - 5 * s, y - 10 * s, 10 * s, 20 * s), Qt.AlignmentFlag.AlignCenter, degree)
                if accidental:
                    glyphs.draw_accidental(p, accidental, x - 7 * s, y - 2 * s, s * 0.5, color)
                for dot in range(abs(octave)):
                    dot_y = y - (11 + 4 * dot) * s if octave > 0 else y + (13 + 4 * dot) * s
                    p.setPen(Qt.PenStyle.NoPen)
                    p.drawEllipse(QPointF(x, dot_y), max(.7, s), max(.7, s))
            p.setPen(QPen(color, max(1, s)))
            p.drawLine(QPointF(centers[0] - 4 * s, cy + 4 * s), QPointF(centers[-1] + 4 * s, cy + 4 * s))

    def _paint_beams(self, p, part, system, row):
        """Underlines (減時線) joined within one beat (design §3.1)."""
        from .engraving import beam_runs
        c, s = self.colors, self.scale
        score = self.score_model.score
        ranges = self.part_ranges[part]
        p.setPen(QPen(c.ink, max(0.8, 1.3 * s)))
        for bar in system.bars:
            if bar >= len(ranges):
                continue
            for level, run in beam_runs(score, part, *ranges[bar]):     # the same groups jianpu-ly prints
                self._beam_run(p, part, run, row, level)

    def _beam_run(self, p, part, run, row, level):
        s = self.scale
        y = row.baseline + (15 + level * 4) * s
        x1 = self.part_centers[part][run[0]] - 7 * s
        x2 = self.part_centers[part][run[-1]] + 7 * s
        p.drawLine(QPointF(x1, y), QPointF(x2, y))

    def _paint_problems(self, p, part, system, row):
        c, s = self.colors, self.scale
        for bar in system.bars:
            info = self.problems.get((part, bar))
            if not info:
                continue
            frame = self.bar_frames[bar]
            y = row.bottom - 2 * s
            pen = QPen(c.warn, 1.2 * s, Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.drawLine(QPointF(frame.left() + 6 * s, y), QPointF(frame.right() - 6 * s, y))
            font = QFont(ui_font_family())
            font.setPixelSize(max(8, int(11 * s)))
            p.setFont(font)
            text = "⚠ " + info
            width = p.fontMetrics().horizontalAdvance(text) + 12 * s
            pill = QRectF(frame.right() - width - 6 * s, y - 9 * s, width, 17 * s)
            p.setPen(QPen(c.warn, 1))
            p.setBrush(c.warnsoft)
            p.drawRoundedRect(pill, 8 * s, 8 * s)
            p.setPen(c.warn)
            p.drawText(pill, Qt.AlignmentFlag.AlignCenter, text)

    def _paint_preview_target(self, p, system, top, bottom):
        target = self.preview_target
        if not target:
            return
        c, s = self.colors, self.scale
        pen = QPen(c.accent, 1.5, Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        kind = target[0]
        if kind == "notes":
            part = self.score_model.part
            row = system.rows.get(part)
            for r in target[1]:
                if 0 <= r < len(self.part_rects[part]) and self.note_line(part, r) == system.index:
                    cx = self.part_centers[part][r]
                    p.drawRoundedRect(QRectF(cx - 13 * s, row.baseline - 18 * s, 26 * s, 35 * s), 6 * s, 6 * s)
        elif kind == "boundary":
            x = self._boundary_x(target[1])
            if x is not None and system.rect.left() - 2 <= x <= system.rect.right() + 2:
                p.drawRoundedRect(QRectF(x - 5 * s, top - 6 * s, 10 * s, bottom - top + 12 * s), 4 * s, 4 * s)
        elif kind == "bars":
            for bar in target[1]:
                frame = self.bar_frames.get(bar)
                if frame is not None and bar in system.bars:
                    p.drawRoundedRect(QRectF(frame).adjusted(3, 14 * s, -3, -4 * s), 6, 6)

    def _draw_system_marks(self, painter, line):
        c, s = self.colors, self.scale
        painter.setPen(QPen(c.ink, max(1, 1.2 * s)))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for path in self.line_links[line]:
            painter.drawPath(path)
        for path in self.line_hairpins[line]:
            painter.drawPath(path)
        painter.setFont(glyphs.font("italic", 12 * s))
        for path, rect, number in self.line_tuplets[line]:
            painter.setPen(QPen(c.ink, max(1, 1.1 * s)))
            painter.drawPath(path)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, number)
        variant = self.score_model.score.print_variant
        for entry in self.line_spans[line]:
            kind, symbol = entry[0], entry[2]
            if kind == "gliss":
                _, _, _, x1, x2, y, first = entry
                painter.setPen(QPen(c.ink, 1.3 * s))
                painter.drawLine(QPointF(x1, y + 4 * s), QPointF(x2, y - 4 * s))
                continue
            _, _, _, x1, x2, y, first, last = entry
            start = x1
            if first:
                start = x1 + glyphs.draw_span_label(painter, symbol, x1, y, s, c.ink, variant) + 2 * s
            if kind == "textpin":
                if not first:
                    continue
                pen = QPen(c.ink, max(0.8, 1.0 * s))
                pen.setDashPattern([3, 4])
                painter.setPen(pen)
                if start < x2:
                    painter.drawLine(QPointF(start, y), QPointF(x2, y))
                continue
            if symbol.family == "trill" or symbol.id == "line.wavy":
                painter.setPen(QPen(c.ink, 1.1 * s))
                painter.drawPath(glyphs.wave_path(start if symbol.id != "line.wavy" else x1, max(start + 6 * s, x2), y, s))
            else:
                pen = QPen(c.ink, max(0.8, 1.0 * s))
                pen.setDashPattern([3, 3])
                painter.setPen(pen)
                painter.drawLine(QPointF(start, y), QPointF(x2, y))
                if last and symbol.family == "ottava":
                    painter.setPen(QPen(c.ink, max(0.8, 1.0 * s)))
                    hook = 5 * s if symbol.id == "line.8va" else -5 * s
                    painter.drawLine(QPointF(x2, y), QPointF(x2, y + hook))

    def _paint_caret(self, p):
        """Insertion caret (新增模式) or the next empty slot (填寫模式)."""
        c, s = self.colors, self.scale
        mode = self.insertion_mode
        selection = self.selected_rows()
        if mode == "edit" or not selection or self.selection_kind == "barline":
            return
        part = self.score_model.part
        rects = self.part_rects[part]
        if mode == "fill":
            row = self.currentRow()
            notes = self.score_model.score.parts[part].notes
            if row < 0 or row >= len(notes) or notes[row].degree != 0:
                return
            cx = self.part_centers[part][row]
            line_row = self.row_of(part, row)
            pen = QPen(c.accent, 1.5, Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(cx - 13 * s, line_row.baseline - 18 * s, 26 * s, 35 * s), 5 * s, 5 * s)
            return
        if mode == "end":
            row = len(rects) - 1
            x = rects[row].right()
        elif mode == "before":
            row = min(selection)
            x = rects[row].left()
        else:
            row = max(selection)
            x = rects[row].right()
        line_row = self.row_of(part, row)
        if line_row is None:
            return
        top, bottom = line_row.baseline - 18 * s, line_row.baseline + 18 * s
        p.setPen(QPen(c.accent, 2))
        p.drawLine(QPointF(x, top), QPointF(x, bottom))
        p.setBrush(c.accent)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPolygon(QPolygonF([QPointF(x - 4 * s, top - 4 * s), QPointF(x + 4 * s, top - 4 * s), QPointF(x, top + 1)]))

    def _paint_playback(self, p):
        if self.play_note is None:
            return
        part, row = self.play_note
        c, s = self.colors, self.scale
        number = self.note_line(part, row)
        if number is None:
            return
        system = self.systems[number]
        x = self.part_centers[part][row] - 14 * s
        p.setPen(QPen(c.playline, 2))
        p.drawLine(QPointF(x, system.rect.top() + 14 * s), QPointF(x, system.rect.bottom() - 4 * s))

