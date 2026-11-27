"""Editing actions shared by the workspace, the palette, the inspector and keyboard commands.

Every change goes through ``mutate`` (one undo step, validated). Rows always refer to the
part being edited (``self.notes.part``); bar items always live on part 0.
"""
from __future__ import annotations

import json
from dataclasses import replace

from PySide6.QtCore import QMimeData, QPoint, Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from . import __version__, edit_guard, i18n, notation_edit, symbols
from .i18n import N_, tr
from .measures import clean_pasted, clip_notes
from .migrate import LEGACY_FIELDS
from .model import DURATIONS, MAX_PARTS, Note, pickup_choices
from .notation_actions import NotationActions
from .notation_rules import bar_label

NEW_SCORE_STAFF_SIZE = 24
PENDING_OCTAVES = {2: N_("高兩八度"), 1: N_("高八度"), -1: N_("低八度"), -2: N_("低兩八度")}

NOTE_MIME = "application/x-jianpu-studio-notes"
PICKUP_NAMES = {48 * 4: N_("全音符"), 96: N_("二分"), 144: N_("附點二分"), 48: N_("四分"), 72: N_("附點四分"),
                24: N_("八分"), 36: N_("附點八分"), 12: N_("十六分"), 18: N_("附點十六分"), 6: N_("三十二分"),
                9: N_("附點三十二分")}  # catalog sources; show tr(value)


class EditActions(NotationActions):
    # ---------------------------------------------------------------- parts
    @property
    def part(self) -> int:
        return self.notes.part

    def part_notes(self, project=None) -> list[Note]:
        project = project or self.project
        return project.score.parts[min(self.part, len(project.score.parts) - 1)].notes

    def snapshot(self):
        return self.project.snapshot(), tuple(self.notes.selected_rows()), self.notes.currentRow(), self.part

    def _restore(self, entry):
        project, rows, current = entry[:3]
        part = entry[3] if len(entry) > 3 else 0
        self.project = project
        self.notes.set_score(project.score, min(part, len(project.score.parts) - 1))
        return rows, current

    def mutate(self, operation, *, selection=None, current=None, explicit=None, declared=()):
        """Apply ``operation`` to a copy as one undo step. Whatever the edit would remove beyond
        ``explicit(item)`` (what the command removes on purpose) and ``declared`` (already
        named in an earlier question) is named first, and the user can cancel."""
        if self.loading or (self.task and self.task_blocks_edit):
            return False
        before = self.snapshot()
        project = self.project.snapshot()
        try:
            operation(project)
            if project.mode != "visual":
                raise ValueError(tr("本版只提供視覺編輯，未變更目前樂譜。"))
            project.score.validate()
            self.validate_tuplet_edit(project.score)
            # An unfinished repeat/jump is a legal step while building it (it is listed as a
            # problem); anything else an edit would break is refused.
            if self.current_edit_error() is None:
                if error := project.score.edit_error():
                    raise ValueError(error)
            totals = [sum(n.ticks for n in p.notes) for p in self.project.score.parts]
            after = [sum(n.ticks for n in p.notes) for p in project.score.parts]
            if len(totals) == len(after) > 1 and len(set(totals)) == 1 and len(set(after)) > 1:
                raise ValueError(tr("這項修改會讓各聲部的長度不同，所以沒有套用；請改在各聲部的同一位置操作。"))
            lost = edit_guard.subtract(edit_guard.losses(self.project.score, project.score, explicit), declared)
        except (ValueError, TypeError) as exc:
            self.error(tr("無法套用這項修改"), exc)
            self.sync(selection=before[1], current=before[2])
            return False
        if project == self.project:
            return False
        if lost and not self.confirm_losses(self.project.score, lost):
            self.sync(selection=before[1], current=before[2])
            return False
        self.history.append(before)
        del self.history[:-200]
        self.future.clear()
        self.project = project
        self.changed()
        self.sync(selection=before[1] if selection is None else selection,
                  current=before[2] if current is None else current)
        return True

    def current_notation_error(self):
        """notation_error() of the current project, computed once per project object and language."""
        cached = getattr(self, "_error_cache", None)
        key = (self.project, self.revision, i18n.current_language())
        if cached is None or cached[0] is not key[0] or cached[1:3] != key[1:]:
            cached = (*key, self.project.score.notation_error())
            self._error_cache = cached
        return cached[3]

    def current_edit_error(self):
        """edit_error() of the current project (cached like ``current_notation_error``)."""
        cached = getattr(self, "_edit_error_cache", None)
        key = (self.project, self.revision, i18n.current_language())
        if cached is None or cached[0] is not key[0] or cached[1:3] != key[1:]:
            cached = (*key, self.project.score.edit_error())
            self._edit_error_cache = cached
        return cached[3]

    def changed(self):
        self.revision += 1
        self.dirty = True
        self.render_cancelled = False
        self.play_after_render = False
        self.output_request = None
        self.stop_playback()
        self.preview_status.setText(tr("樂譜已修改，等待更新排版。"))
        if self.print_preview_active and self.auto_check.isChecked():
            self.autopreview.start()

    def undo(self):
        if not self.history or (self.task and self.task_blocks_edit):
            return
        self.capture_fields()
        if not self.history:
            return
        self.future.append(self.snapshot())
        rows, current = self._restore(self.history.pop())
        self.changed()
        self.mark_saved_state()
        self.sync(selection=rows, current=current)

    def mark_saved_state(self):
        """Undo/redo back to what was last saved (or opened) is not 「已修改」, and the recovery
        copy of the undone edits goes too (else a crash would bring them back)."""
        saved = getattr(self, "saved_project", None)
        if saved is not None and self.project == saved:
            self.dirty = False
            self.clear_recovery()

    def redo(self):
        if not self.future or (self.task and self.task_blocks_edit):
            return
        self.history.append(self.snapshot())
        rows, current = self._restore(self.future.pop())
        self.changed()
        self.mark_saved_state()
        self.sync(selection=rows, current=current)

    # ---------------------------------------------------------------- refresh
    def sync(self, *, selection=None, current=None):
        explicit = selection is not None
        if selection is None:
            selection, current = self.notes.selected_rows(), self.notes.currentRow()
        kind, bars, barline = self.notes.selection_kind, list(self.notes.selected_bars), self.notes.selected_barline
        self.loading = True
        try:
            score = self.project.score
            part = min(self.part, len(score.parts) - 1)
            for field, value in ((self.title_field, score.title), (self.composer_field, score.composer),
                                 (self.info_composer, score.composer), (self.lyrics, score.parts[part].lyrics)):
                if field.text() != value:
                    field.setText(value)
                    field.setCursorPosition(0)
            self.key_field.setCurrentIndex(self.key_field.findData(score.key))
            self.meter_field.setCurrentText(score.meter)
            self.tempo_field.setValue(score.tempo)
            self.staff.setChecked(score.with_staff)
            self._sync_pickup(score)
            self.notes.set_score(score, part)
            self.notes.select_rows(selection, current)
            if kind == "bars" and bars and not explicit:
                self.notes.select_bars(bars, current)
            elif kind == "barline" and barline >= 0 and not explicit:
                self.notes.select_barline(barline)
            self._sync_parts(score)
            self._sync_problems(score)
            total = sum(n.ticks for n in score.notes)
            count = score.bar_index(total - 1) + 1 if total else 0
            self.bar_jump.setMaximum(max(1, count))
            title = score.title or tr("未命名樂曲")
            self.setWindowTitle(tr("{mark}{title} — 簡譜工房 {version}").format(mark="● " if self.dirty else "",
                                                                                 title=title, version=__version__))
            self.file_label.setText(self.path.name if self.path else title)
            self.file_label.setToolTip(str(self.path) if self.path else title)
            self.dirty_label.setVisible(self.dirty)
            self.counts_label.setText(tr("{bars} 小節 · {parts} 個聲部").format(
                bars=max(0, count - bool(score.pickup)), parts=len(score.parts)) if len(score.parts) > 1
                else tr("{bars} 小節").format(bars=max(0, count - bool(score.pickup))))
            for control in (self.undo_action, self.undo_button):
                control.setEnabled(bool(self.history))
            for control in (self.redo_action, self.redo_button):
                control.setEnabled(bool(self.future))
        finally:
            self.loading = False
        self.update_selection()

    def _sync_pickup(self, score):
        choices = pickup_choices(score.bar_ticks)
        wanted = [0] + choices
        if [self.pickup_field.itemData(i) for i in range(self.pickup_field.count())] != wanted:
            self.pickup_field.clear()
            self.pickup_field.addItem(tr("無"), 0)
            for ticks in choices:
                self.pickup_field.addItem(tr(PICKUP_NAMES.get(ticks, "")) or str(ticks), ticks)
        self.pickup_field.setCurrentIndex(max(0, self.pickup_field.findData(score.pickup)))

    def _sync_parts(self, score):
        names = [p.name or tr("第 {n} 聲部").format(n=i + 1) for i, p in enumerate(score.parts)]
        if len(self.part_buttons) != len(names):
            for button in self.part_buttons:
                self.part_group.removeButton(button)
                self.part_row.removeWidget(button)
                button.hide()
                button.setParent(None)
                button.deleteLater()
            self.part_buttons = []
            for index, name in enumerate(names):
                from .widgets import tool
                button = tool(name, checkable=True, name=f"part_button_{index}")
                button.setProperty("chip", True)
                button.setProperty("part_name", name)
                button.setToolTip(tr("編輯「{name}」；再點一次可改名、改歌詞或刪除（Alt+↑／Alt+↓ 切換）").format(name=name))
                button.clicked.connect(lambda checked=False, i=index: self.part_chip_clicked(i))
                self.part_group.addButton(button)
                self.part_row.addWidget(button)
                self.part_buttons.append(button)
        for index, button in enumerate(self.part_buttons):
            button.setProperty("part_name", names[index])
            button.setToolTip(tr("編輯「{name}」；再點一次可改名、改歌詞或刪除（Alt+↑／Alt+↓ 切換）").format(name=names[index]))
            button.setChecked(index == self.part)
            button.setText(("● " if index == self.part and len(names) > 1 else "") + names[index])
        multi = len(score.parts) > 1
        self.part_host.setVisible(multi)
        self.parts_label.setVisible(multi and self.width() > 1300)
        self.add_part_button.setEnabled(len(score.parts) < MAX_PARTS)

    def _sync_problems(self, score):
        """Rhythm problems per part and bar (design §3.6), and the status-bar summary."""
        problems, bars_flagged, found = {}, set(), []
        for p, part in enumerate(score.parts):
            position = 0
            ranges = score.part_bars(p)
            over = False
            for bar, (a, b) in enumerate(ranges):
                length = sum(n.ticks for n in part.notes[a:b])
                expected = score.first_bar_ticks if bar == 0 else score.bar_ticks
                last = bar == len(ranges) - 1
                if length > expected:
                    problems[(p, bar)] = tr("超拍")
                    over = True
                elif length < expected and not last:
                    problems[(p, bar)] = tr("少 {beats} 拍").format(beats=_beats(expected - length))
                position += length
            if part.notes and not over:
                # A note across a bar line is not 'missing beats': filling would not fix it.
                target = score.final_bar_targets(position)[0]
                if target != position and ranges:
                    problems[(p, len(ranges) - 1)] = tr("少 {beats} 拍").format(beats=_beats(target - position))
        for (p, bar), text in sorted(problems.items(), key=lambda item: (item[0][1], item[0][0])):
            bars_flagged.add(bar)
            found.append((p, bar, text, None))
        rhythm = score.rhythm_error()
        notation = self.current_notation_error()
        error = rhythm or notation
        over = any(text == tr("超拍") for _, _, text, _ in found)
        from .notation_rules import structure_problems
        structure = structure_problems(score) if score.parts[0].notes else []
        barlines = set()
        for problem in structure:
            # Drawn on the bar of the sign: 「尚未完成」 for a structure being built, else 「結構有誤」.
            if problem.bar is not None and (0, problem.bar) not in problems:
                problems[(0, problem.bar)] = tr("尚未完成") if problem.incomplete else tr("反覆／跳躍有誤")
                if problem.row is not None and problem.row == score.part_bars(0)[problem.bar][1] - 1:
                    barlines.add(problem.bar)
        edit = self.current_edit_error()
        # Every problem is reachable with 「下一個問題」: the bar problems, then the located messages.
        # Each entry keeps the problem whose one-click fix it offers (the last bar: the rests).
        fill_problem = rhythm if not over and getattr(rhythm, "fix", None) == ("fill",) else None
        sources = [fill_problem if row is None and bar == len(score.part_bars(p)) - 1 else None
                   for p, bar, _, row in found]
        for message in (rhythm, edit, *structure):
            if not message or getattr(message, "fix", None) == ("fill",) or (message is rhythm and over):
                continue
            part = getattr(message, "part", 0) or 0
            row = getattr(message, "row", None)
            bar = getattr(message, "bar", None)
            if bar is None and row is not None and part < len(score.parts):
                bar = next((k for k, (a, b) in enumerate(score.part_bars(part)) if a <= row < b), None)
            found.append((part, bar, str(message), row))
            sources.append(message)
        shown = getattr(self, "problem_shown", None)
        self.problem_cursor = found.index(shown) if shown in found else -1
        if self.problem_cursor < 0:
            self.problem_shown = None
        self.problem_list, self.problem_sources = found, sources
        self._problem_context = (rhythm, notation, over)
        self.notes.set_problems(problems, bars_flagged, barlines)
        if error or found:
            first = found[0] if found else None
            if first and first[3] is None:
                name = score.parts[first[0]].name if len(score.parts) > 1 else ""
                text = tr("⚠ {count} 個問題：第 {bar} 小節{part}{what}").format(
                    count=max(1, len(found)), bar=notation_edit.bar_number(score, first[1]),
                    part=name, what=first[2])
            else:
                text = "⚠ " + error
            self.rhythm_label.setText(text)
            self.rhythm_label.setToolTip(error or text)
            self.rhythm_label.setProperty("role", "warn-text")
        else:
            self.rhythm_label.setText(tr("✓ 拍數完整"))
            self.rhythm_label.setToolTip("")
            self.rhythm_label.setProperty("role", "ok")
        self.rhythm_label.style().unpolish(self.rhythm_label)
        self.rhythm_label.style().polish(self.rhythm_label)
        self.problem_button.setVisible(bool(error or found))
        self._sync_fix_button()

    def shown_problem(self):
        """The problem whose fix the status bar offers: the one 「下一個問題」 last showed, else
        the first one (the last bar's rests before a sign problem)."""
        sources = getattr(self, "problem_sources", [])
        cursor = getattr(self, "problem_cursor", -1)
        if 0 <= cursor < len(sources):
            return sources[cursor]
        rhythm, notation, over = getattr(self, "_problem_context", (None, None, False))
        if rhythm is not None and not over and getattr(rhythm, "fix", None):
            return rhythm
        return notation

    def _sync_fix_button(self):
        score = self.project.score
        problem = self.shown_problem()
        fix = getattr(problem, "fix", None) if problem else None
        if fix and fix[0] == "add":
            name = symbols.SYMBOLS[fix[2]].display_name
            self.fill_button.setText(tr("加上「{name}」").format(name=name))
            self.fill_button.setToolTip(tr("把「{name}」加在{bar}（可復原）：{problem}").format(
                name=name, bar=bar_label(score, fix[1]), problem=problem))
        elif fix and fix[0] != "fill":
            self.fill_button.setText(tr("移除孤立記號"))
            self.fill_button.setToolTip(tr("移除這個沒有另一端的記號（可復原）：{problem}").format(problem=problem))
        else:
            self.fill_button.setText(tr("補齊休止符"))
            self.fill_button.setToolTip(tr("把最後一小節補滿休止符"))
        self.fill_button.setVisible(bool(fix))

    def capture_fields(self, *_):
        if self.loading or (self.task and self.task_blocks_edit):
            return
        part = min(self.part, len(self.project.score.parts) - 1)
        values = dict(title=self.title_field.text(), composer=self.composer_field.text(),
                      key=self.key_field.currentData() or self.project.score.key, meter=self.meter_field.currentText(),
                      tempo=self.tempo_field.value(), with_staff=self.staff.isChecked())
        lyrics = self.lyrics.text()
        score = self.project.score
        if all(getattr(score, name) == value for name, value in values.items()) and score.parts[part].lyrics == lyrics:
            return

        from .notation_rules import crossing
        was_whole = crossing(score) is None

        def update(project):
            for name, value in values.items():
                setattr(project.score, name, value)
            project.score.parts[part] = replace(project.score.parts[part], lyrics=lyrics)
            if project.score.pickup and project.score.pickup not in pickup_choices(project.score.bar_ticks):
                project.score.pickup = 0
            if was_whole and (problem := crossing(project.score)):
                raise ValueError(tr("改成 {meter} 拍後小節線會移到別的位置，{where}的音會跨過小節線，所以沒有修改。"
                                    "請先調整這些音的時值，或新增一份樂譜。").format(meter=project.score.meter, where=problem))
        if was_whole and values["meter"] != score.meter and any(p.notes for p in score.parts):
            # Written music: every meter change goes through the re-barring question, which
            # shows (and applies in the same undo step) the split notes and the rests that
            # complete the last bar; a change that only redraws the bar lines needs no question.
            trial = score.copy()
            for name, value in values.items():
                setattr(trial, name, value)
            if trial.pickup and trial.pickup not in pickup_choices(trial.bar_ticks):
                trial.pickup = 0
            self.move_bar_lines(values=values, meter=trial.meter, pickup=trial.pickup,
                                label=tr("改成 {meter} 拍").format(meter=trial.meter))
            return
        self.mutate(update)

    def move_bar_lines(self, *, meter, pickup, label, values=None, always_ask=False) -> bool:
        """A pickup or meter that moves the bar lines of written music: an empty (all-rest) score
        is simply redrawn, and so is music whose notes all fit the new bars; otherwise the user
        chooses how, after seeing what each way changes (one undo step). With ``always_ask`` (a
        pickup: every bar line and bar number after it moves) written music is never re-barred
        unasked, even when no note has to change."""
        from . import rebar
        from .rhythm_edit import Choice, RhythmRefused
        score = self.project.score
        options = rebar.options(score, meter=meter, pickup=pickup)
        usable = {key: (lab, result, view) for key, lab, result, view in options
                  if result is not None and view.problem is None}
        if "rests" in usable:
            key = "rests"
        elif "rebar" in usable and usable["rebar"][2].trivial and not always_ask:
            key = "rebar"
        else:
            if not usable:
                lines = [self._bar_line_reason(key, lab, view) for key, lab, _, view in options]
                self.error(tr("無法移動小節線"), tr("{label}會移動小節線。").format(label=label) + " ".join(lines))
                self.sync()
                return False

            def build():
                current = rebar.options(score, meter=meter, pickup=pickup)       # labels in the current language
                crossing = any(view.split for _, _, result, view in current if result is not None)
                message = (tr("{label}會移動後面所有的小節線，有音會跨過新的小節線。請選擇做法：") if crossing
                           else tr("{label}會移動後面所有的小節線。請選擇做法：")).format(label=label)
                choices, lines, losses = [], [], {}
                for key, lab, result, view in current:
                    if result is not None and view.problem is None:
                        choices.append(Choice(key, lab))
                        lines.append(rebar.describe(score, view, lab))
                        losses[key] = view.lost
                    else:
                        lines.append(self._bar_line_reason(key, lab, view))
                refused = RhythmRefused(message + "\n\n" + "\n".join(lines), choices=tuple(choices),
                                        title=tr("移動小節線"))
                refused.losses = losses
                refused.rebuild = build
                return refused
            key = self.choose_rhythm_option(build())
            if key not in usable:
                self.sync()
                return False
        _, result, view = usable[key]

        def update(project):
            if values:
                for name, value in values.items():
                    setattr(project.score, name, value)
            result.apply(project.score, meter=meter, pickup=pickup)
        if self.mutate(update, declared=view.lost):
            self.toast.show_text(tr("已移動小節線；可用復原還原。"), self.undo)
            return True
        self.sync()
        return False

    @staticmethod
    def _bar_line_reason(key, label, view):
        reason = view if isinstance(view, str) else view.problem
        if key == "rebar":
            return tr("不能整首重新分小節：{reason}").format(reason=reason)
        return tr("不能{choice}：{reason}").format(choice=label, reason=reason)

    def change_pickup(self, *_):
        if self.loading:
            return
        ticks = self.pickup_field.currentData()
        if ticks is None or ticks == self.project.score.pickup:
            return

        from .notation_rules import crossing
        was_whole = crossing(self.project.score) is None
        if was_whole and any(p.notes for p in self.project.score.parts):
            label = tr("設定 {beats} 拍弱起").format(beats=_beats(ticks)) if ticks else tr("取消弱起")
            self.move_bar_lines(meter=self.project.score.meter, pickup=ticks, label=label, always_ask=True)
            return

        def update(project):
            project.score.pickup = ticks
            if was_whole and (problem := crossing(project.score)):
                raise ValueError(tr("改變弱起長度會讓後面的小節線全部移動，{where}的音會跨過小節線，所以沒有修改。"
                                    "請先在曲首補上或刪去相同長度的音。").format(where=problem))
        if not self.mutate(update):
            self.sync()

    def select_note(self, *_):
        self.update_selection()

    def update_selection(self, *_):
        if self.loading:
            return
        if not hasattr(self, "next_note"):
            self.next_note = Note()
        if not hasattr(self, "degree"):
            return
        rows = self.notes.selected_rows()
        notes = self.part_notes()
        rows = [r for r in rows if r < len(notes)]
        mode = self.input_mode.currentData()
        editing = mode == "edit"
        self.notes.insertion_mode = mode if mode in ("edit", "fill") else self.insert_position.currentData()
        self.insert_position.setEnabled(mode == "insert")
        values = [notes[r] for r in rows] if editing else [self.next_note]
        self.inspector_loading = True
        try:
            for name in ("degree", "duration", "octave", "accidental"):
                widget = getattr(self, name)
                widget.setPlaceholderText(tr("未選取") if editing and not rows else tr("不同"))
                distinct = {getattr(note, name) for note in values}
                widget.setCurrentIndex(widget.findData(next(iter(distinct))) if len(distinct) == 1 else -1)
                widget.setEnabled(not editing or bool(rows))
            dotted = {note.dotted for note in values}
            self.dotted.setTristate(len(dotted) > 1)
            self.dotted.setCheckState(Qt.CheckState.PartiallyChecked if len(dotted) > 1 else
                                      Qt.CheckState.Checked if True in dotted else Qt.CheckState.Unchecked)
            self.dotted.setEnabled(not editing or bool(rows))
        finally:
            self.inspector_loading = False
        self._sync_note_bar(values, editing, rows)
        self.selection_label.setText(self.context_hint(mode, rows))
        self.selection_label.setToolTip(self.selection_label.text())
        if hasattr(self, "measure_controls"):
            measures = self.selected_measures()
            visual = self.project.mode == "visual"
            for control in self.measure_controls:
                control.setEnabled(visual and bool(measures))
            self.add_bar_button.setEnabled(visual)
        self.refresh_inspector()
        self.palette.invalidate()
        self.update_transport()
        if rows and hasattr(self, "guide"):
            self.guide.mark(1)
        self.notes.viewport().update()

    def _sync_note_bar(self, values, editing, rows):
        durations = {n.duration for n in values}
        for value, button in self.duration_buttons.items():
            button.setChecked(len(durations) == 1 and value in durations)
            button.setEnabled(not editing or bool(rows))
        # ↑ ↓ . ♯ ♭ ♮ act on the selected notes in every mode; with none to act on (新增／填寫)
        # they prepare the next note, and the buttons show that one-note state.
        notes = self.part_notes()
        dot_rows, pitch_rows = self.modifier_rows("dot"), self.modifier_rows("accidental")
        dotted = {notes[r].dotted for r in dot_rows} if dot_rows else {n.dotted for n in values}
        self.dot_button.setChecked(dotted == {True})
        accidentals = {notes[r].accidental for r in pitch_rows} if pitch_rows else {n.accidental for n in values}
        for value, button in self.accidental_buttons.items():
            button.setChecked(accidentals == {value})
        pending = self.next_note.octave if not editing and self.modifier_rows("octave") is None else 0
        self.octave_up_button.setChecked(pending > 0)
        self.octave_down_button.setChecked(pending < 0)
        for button in (self.dot_button, self.octave_up_button, self.octave_down_button, *self.accidental_buttons.values()):
            button.setEnabled(not editing or bool(rows))
        self.tie_button.setEnabled(bool(rows))

    def context_hint(self, mode, rows) -> str:
        """Status-bar middle: what the keys do right now (design §5)."""
        if self.palette.continuous:
            name = symbols.SYMBOLS[self.palette.continuous].display_name
            return tr("連續套用「{name}」：點音符加上，再點一次移除 · Esc 結束").format(name=name)
        if self.notes.selection_kind == "bars":
            return tr("Insert 前插 · Ctrl+D 重複 · Ctrl+Delete 刪除小節 · / 加記號")
        if self.notes.selection_kind == "barline":
            return tr("點右側的小節線、反覆或跳躍記號加到這條小節線")
        if mode == "edit":
            if rows:
                return tr("修改所選 {count} 個音符 · 數字鍵改音高 · / 加記號 · Delete 刪除").format(count=len(rows))
            return tr("修改模式 · 請先選取音符（Shift 連選、Ctrl 加選）")
        pending = self.pending_text()
        if mode == "fill":
            if pending:
                return tr("下一個音：{pending}（只加在一個音）· 填寫小節 · {beats} 拍").format(
                    pending=pending, beats=self.next_note.beats)
            return tr("填寫小節 · 下一個音 {beats} 拍 · 點休止符 0 後按數字鍵").format(beats=self.next_note.beats)
        note = self.next_note
        position = {"after": tr("所選之後"), "before": tr("所選之前"), "end": tr("曲尾")}[self.insert_position.currentData()]
        if pending:
            return tr("下一個音：{pending}（只加在一個音）· 新增模式 · {beats} 拍 · 插在{position}").format(
                pending=pending, beats=note.beats, position=position if rows else tr("曲尾"))
        pitch = tr("休止 0") if note.degree == 0 else str(note.degree)
        return tr("新增模式 · 下一個：{pitch} · {beats} 拍 · 插在{position} · Q–S 時值").format(
            pitch=pitch, beats=note.beats, position=position if rows else tr("曲尾"))

    def pending_text(self) -> str:
        """What ↑ ↓ . ♯ ♭ ♮ prepared for the next typed note ("" when nothing): shown in the
        status hint while it lasts, and used up by one note."""
        note = self.next_note
        words = []
        if note.octave:
            words.append(tr(PENDING_OCTAVES[note.octave]))
        if note.accidental:
            words.append({"#": "♯", "b": "♭", "n": "♮"}[note.accidental])
        if note.dotted:
            words.append(tr("附點"))
        return " ".join(words)

    def modifier_rows(self, kind):
        """The rows ↑ ↓ (``octave``), ``.`` (``dot``) and ♯ ♭ ♮ (``accidental``) change.

        修改: the selection. 新增／填寫: the selected notes too -- pitched ones for octave and
        accidental; for the dot every selected note, except that in 填寫 a selected rest is the
        place being filled (the dot is then for the note about to be typed). With none, ↑ ↓ change
        the note just typed (the caret has moved on to the next rest). ``None`` means there is
        nothing to act on: the command prepares the next note instead."""
        notes = self.part_notes()
        rows = [r for r in self.notes.selected_rows() if r < len(notes)]
        mode = self.input_mode.currentData()
        if mode == "edit":
            return rows
        pitched = [r for r in rows if notes[r].degree]
        chosen = rows if kind == "dot" and mode == "insert" else pitched
        if chosen:
            return chosen
        if kind == "octave" and (row := self.just_typed_row()) is not None:
            return [row]
        return None

    def just_typed_row(self):
        """The note the last digit wrote, while nothing else has happened since."""
        typed = getattr(self, "_typed", None)
        notes = self.part_notes()
        if (typed is None or typed[0] != self.revision or typed[1] != self.part or typed[2] >= len(notes)
                or typed[3] != tuple(self.notes.selected_rows()) or not notes[typed[2]].degree):
            return None
        return typed[2]

    def _remember_typed(self, row):
        self._typed = (self.revision, self.part, row, tuple(self.notes.selected_rows()))

    def clear_pending(self):
        """One typed note uses up the prepared octave, accidental and dot."""
        note = self.next_note
        if note.octave or note.accidental or note.dotted:
            self.next_note = replace(note, octave=0, accidental="", dotted=False)
            self.update_selection()

    @staticmethod
    def replace_note(note, changes):
        changes = dict(changes)
        if changes.get("degree", note.degree) == 0:
            # A rest keeps only marks that make sense on a rest (dynamics, fermata, breath...).
            changes.update(octave=0, accidental="", tie=False, grace_before="", grace_after="")
            note = replace(note, **changes)
            return replace(note, marks=tuple(m for m in note.marks if symbols.SYMBOLS[m].on_rest
                                             or m in symbols.LEGACY_REST_MARKS),
                           spans_start=tuple(s for s in note.spans_start if symbols.SYMBOLS[s].on_rest),
                           spans_end=tuple(s for s in note.spans_end if symbols.SYMBOLS[s].on_rest))
        return replace(note, **changes)

    def edit_property(self, name, value):
        if self.loading or self.inspector_loading or value is None:
            return
        if self.input_mode.currentData() != "edit":
            self.next_note = self.replace_note(self.next_note, {name: value})
            self.update_selection()
            return
        rows = self.notes.selected_rows()
        if rows and name in ("duration", "dotted"):
            self.rhythm_change(rows, lambda note: self.replace_note(note, {name: value}))
        elif rows:
            part = self.part

            def update(project):
                notes = project.score.parts[part].notes
                for row in rows:
                    notes[row] = self.replace_note(notes[row], {name: value})
            self.mutate(update, explicit=self.retyped(part, rows))

    # ---------------------------------------------------------------- local rhythm edits
    def choose_rhythm_option(self, refused):
        """Ask which way out of a refused rhythm edit (``refused.choices``); None = cancel.
        Tests replace this method."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        buttons = {}
        for choice in refused.choices:
            buttons[box.addButton(choice.label, QMessageBox.ButtonRole.AcceptRole)] = choice.key
        cancel = box.addButton(tr("取消"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel)
        self._choice_box = (box, refused, buttons, cancel)
        self._fill_choice_box(refused)
        try:
            box.exec()
        finally:
            self._choice_box = None
        return buttons.get(box.clickedButton())

    def _fill_choice_box(self, refused):
        box, _, buttons, cancel = self._choice_box
        box.setWindowTitle(refused.title or tr("放不下這個時值"))
        box.setText(str(refused))
        labels = {c.key: c.label for c in refused.choices}
        for button, key in buttons.items():
            if key in labels:
                button.setText(labels[key])
        cancel.setText(tr("取消"))

    def retranslate_choice(self):
        """An open rhythm/removal question is rebuilt in the new language (its texts are composed
        from several messages, so converting the shown text piece by piece is not enough)."""
        if getattr(self, "_choice_box", None) is None:
            return
        refused = self._choice_box[1]
        rebuild = getattr(refused, "rebuild", None)
        self._fill_choice_box(rebuild() if rebuild else refused)

    def confirm_losses(self, score, lost) -> bool:
        """Name what an edit would remove (count, kind, 小節/拍) and let the user cancel."""
        from .rhythm_edit import Choice, RhythmRefused

        def build():
            refused = RhythmRefused(edit_guard.message(score, lost), choices=(Choice("confirm", tr("移除並繼續")),),
                                    title=tr("這項修改會移除記號"))
            refused.losses = {"confirm": lost}
            refused.rebuild = build
            return refused
        return self.choose_rhythm_option(build()) == "confirm"

    def _trial(self, part, result):
        score = self.project.score.copy()
        score.parts[part].notes = list(result.notes)
        for other, notes in (result.others or {}).items():
            score.parts[other].notes = list(notes)
        return score

    def _describe_choices(self, refused, make, chosen, part):
        """Add to the question what each choice would remove, so choosing it is the confirmation."""
        from .rhythm_edit import RhythmRefused
        text = str(refused).split("\n\n")[0]
        for choice in refused.choices:
            try:
                result = make("+".join([*chosen, choice.key]))
            except RhythmRefused:
                continue
            lost = edit_guard.losses(self.project.score, self._trial(part, result), result.explicit)
            refused.losses[choice.key] = lost
            if lost:
                text += "\n\n" + tr("選「{choice}」會移除 {count} 項：{items}。").format(
                    choice=choice.label, count=len(lost), items=edit_guard.describe(self.project.score, lost))
        refused.args = (text,)

    def rhythm_refused(self, refused):
        """Say why, at the place: select the note and show the reason (no modal dialog)."""
        if refused.row is not None and refused.part == self.part and refused.row < len(self.part_notes()):
            self.notes.select_rows([refused.row], refused.row)
            self.notes.scrollTo(self.notes.currentIndex())
        self.statusBar().showMessage(str(refused), 12000)
        self.toast.show_text("⚠ " + str(refused))

    def run_rhythm_edit(self, make, *, keep_selection=False):
        """``make(choice)`` builds a ``RhythmEdit``; refusals offer their choices, then retry."""
        if self.loading or (self.task and self.task_blocks_edit):
            return False
        from .rhythm_edit import RhythmRefused
        part = self.part
        chosen, declared, result = [], [], None
        for _ in range(4):
            try:
                result = make("+".join(chosen))
                break
            except RhythmRefused as refused:
                if any(c.key not in chosen for c in refused.choices):
                    asked = list(chosen)
                    self._describe_choices(refused, make, asked, part)

                    def rebuild(asked=asked, refused=refused):
                        try:
                            make("+".join(asked))
                        except RhythmRefused as again:
                            self._describe_choices(again, make, asked, part)
                            return again
                        return refused
                    refused.rebuild = rebuild
                    key = self.choose_rhythm_option(refused)
                    if key:
                        chosen.append(key)
                        declared += refused.losses.get(key, [])
                        continue
                    self.update_selection()          # the note bar shows the note's real value again
                    self.notes.setFocus()
                    return False
                self.rhythm_refused(refused)
                self.update_selection()
                return False
        if result is None:
            return False

        def update(project):
            project.score.parts[part].notes = list(result.notes)
            for other, notes in (result.others or {}).items():
                project.score.parts[other].notes = list(notes)
        selection = None if keep_selection else result.selection
        current = None if keep_selection else result.current
        if not self.mutate(update, selection=selection, current=current, explicit=result.explicit,
                           declared=declared):
            return False
        if result.notice:
            self.toast.show_text(result.notice, self.undo)
        elif hasattr(self, "toast"):
            self.toast.dismiss()
        return True

    def rhythm_change(self, rows, change):
        """Change durations in place: only each note's own bar changes (rests fill or give way)."""
        from .rhythm_edit import change_notes
        part = self.part
        return self.run_rhythm_edit(lambda choice: change_notes(self.project.score, part, rows, change, choice=choice))

    def duration_clicked(self, value):
        self.duration.setCurrentIndex(self.duration.findData(value))
        self.flash_control(self.duration_buttons[value])
        self.notes.setFocus()

    def accidental_clicked(self, value):
        rows = self.modifier_rows("accidental")
        notes = self.part_notes()
        if rows is None:
            new = "" if self.next_note.accidental == value else value
            self.next_note = self.replace_note(self.next_note, {"accidental": new})
            self.update_selection()
        elif rows:
            new = "" if {notes[r].accidental for r in rows} == {value} else value
            part = self.part

            def update(project):
                written = project.score.parts[part].notes
                for row in rows:
                    if written[row].degree:
                        written[row] = self.replace_note(written[row], {"accidental": new})
            if not self.mutate(update, explicit=self.retyped(part, rows)):
                self.update_selection()
        self.notes.setFocus()

    def flash_control(self, control):
        control.setProperty("flash", True)
        control.style().unpolish(control)
        control.style().polish(control)

        def reset():
            try:
                control.setProperty("flash", False)
                control.style().unpolish(control)
                control.style().polish(control)
            except RuntimeError:
                pass
        QTimer.singleShot(120, reset)

    def insertion_index(self):
        rows = self.notes.selected_rows()
        mode = self.insert_position.currentData()
        if not rows or mode == "end":
            return len(self.part_notes())
        return min(rows) if mode == "before" else max(rows) + 1

    def insert_notes(self, notes):
        if not notes:
            return False
        if sum(len(p.notes) for p in self.project.score.parts) + len(notes) > 10000:
            self.error(tr("無法新增"), tr("每份樂譜最多支援 10,000 個音符。"))
            return False
        index = self.insertion_index()
        part = self.part
        from .rhythm_edit import insert_notes
        before = self.insert_position.currentData() == "before" and bool(self.notes.selected_rows())

        def make(choice):
            result = insert_notes(self.project.score, part, index, notes, choice=choice)
            if before:
                # Keep the caret before the same note, so typing 1 2 3 inserts in order.
                row = index + len(notes)
                result = replace(result, selection=(row,), current=row)
            return result
        if self.run_rhythm_edit(make):
            self.notes.scrollTo(self.notes.currentIndex())
            return True
        return False

    def input_pitch(self, degree):
        button = self.pitch_buttons[(1, 2, 3, 4, 5, 6, 7, 0).index(degree)]
        self.flash_control(button)
        if self.input_mode.currentData() == "edit":
            self.edit_property("degree", degree)
            if self.notes.selected_rows():
                self.guide.mark(2)
        else:
            note = self.replace_note(self.next_note, {"degree": degree})
            revision = self.revision
            self._typed_row = None
            self.fill_rest(note) if self.input_mode.currentData() == "fill" else self.insert_notes([note])
            self.guide.mark(2)
            if self.revision != revision:
                if self._typed_row is not None:
                    self._remember_typed(self._typed_row)
                dotted = self.next_note.dotted
                # A dot, an octave or an accidental prepared for the next note is for one note
                # (as on paper: 5· then 6): the note after it is plain again.
                self.clear_pending()
                if dotted:
                    self.statusBar().showMessage(tr("附點只加在這一個音；下一個音不帶附點（要再加按 .）。"), 5000)
        if self.notes.currentRow() >= 0:
            self.notes.flash(self.notes.currentRow())
        self.notes.setFocus()

    def delete_note(self):
        rows = self.notes.selected_rows()
        if not rows or not self.require_complete_tuplets(rows):
            return
        part = self.part
        from .rhythm_edit import delete_notes
        self.run_rhythm_edit(lambda choice: delete_notes(self.project.score, part, rows))

    def copy_notes(self):
        if not self.require_complete_tuplets(self.notes.selected_rows()):
            return False
        notes = clip_notes(self.project.score, self.part, self.notes.selected_rows())
        if not notes:
            return False
        mime = QMimeData()
        mime.setData(NOTE_MIME, json.dumps([n.to_dict() for n in notes]).encode("utf-8"))
        mime.setText(" ".join(n.notation_token() for n in notes))
        QApplication.clipboard().setMimeData(mime)
        return True

    def paste_notes(self):
        mime = QApplication.clipboard().mimeData()
        if not mime or not mime.hasFormat(NOTE_MIME):
            self.statusBar().showMessage(tr("請先在譜面選取並複製音符，再貼到指定位置。"), 5000)
            return
        try:
            payload = bytes(mime.data(NOTE_MIME))
            if len(payload) > 8 * 1024 * 1024:
                raise ValueError(tr("剪貼簿音符資料太大。"))
            raw = json.loads(payload)
            if not isinstance(raw, list) or len(raw) > 10000:
                raise ValueError(tr("剪貼簿音符格式不正確。"))
            notes = [Note.from_dict(item) if not set(item) & set(LEGACY_FIELDS) else Note(**item) for item in raw]
            for note in notes:
                note.validate()
        except (TypeError, ValueError, AttributeError) as exc:
            self.error(tr("無法貼上音符"), exc)
            return
        if self.paste_over_bars(notes):
            return
        revision = self.revision
        self.insert_clip(notes, notice=True)
        if self.revision != revision and not self.statusBar().currentMessage():
            self.toast.show_text(tr("已插入 {count} 個音，後面的音往後移（要取代整小節，請先點小節號選取小節再貼上）。").format(
                count=len(notes)), self.undo)

    def paste_over_bars(self, notes) -> bool:
        """Pasting whole copied bars onto selected bars asks: replace them, or insert before them."""
        from .rhythm_edit import Choice, RhythmRefused
        bars = sorted(self.notes.selected_bars) if self.notes.selection_kind == "bars" else []
        score = self.project.score
        ranges = score.part_bars(self.part)
        if not bars or bars[-1] >= len(ranges):
            return False
        a, b = ranges[bars[0]][0], ranges[bars[-1]][1]
        part_notes = score.parts[self.part].notes
        room = sum(n.ticks for n in part_notes[a:b])
        if sum(n.ticks for n in notes) != room:
            return False
        label = tr("第 {a}–{b} 小節").format(a=notation_edit.bar_number(score, bars[0]),
                                            b=notation_edit.bar_number(score, bars[-1])) if len(bars) > 1 else \
            bar_label(score, bars[0])
        refused = RhythmRefused(tr("要怎麼貼上這 {count} 個音？").format(count=len(notes)), choices=(
            Choice("replace", tr("取代{bars}的音").format(bars=label)),
            Choice("insert", tr("插入在{bars}前面").format(bars=label))), title=tr("貼上小節"))
        key = self.choose_rhythm_option(refused)
        if key == "insert":
            self.notes.select_rows(list(range(a, b)), a)
            position = self.insert_position.currentData()
            self.insert_position.setCurrentIndex(self.insert_position.findData("before"))
            self.insert_clip(notes, notice=True)
            self.insert_position.setCurrentIndex(self.insert_position.findData(position))
            return True
        if key != "replace":
            return True
        plain, _ = clean_pasted(notes)
        part = self.part
        # The bar lines (repeats, endings, jumps) of the selected bars stay where they are.
        positions, bounds, tick = [], {}, 0
        for row in range(a, b):
            positions.append(tick)
            tick += part_notes[row].ticks
        for row in range(a, b):
            bounds.setdefault(positions[row - a], [None, None])[0] = part_notes[row].bar_start
            end = positions[row - a] + part_notes[row].ticks
            bounds.setdefault(end, [None, None])[1] = part_notes[row].bar_end
        out, tick = [], 0
        for note in plain:
            start, end = tick, tick + note.ticks
            values = {}
            if start in bounds and bounds[start][0]:
                values["bar_start"] = bounds[start][0]
            if end in bounds and bounds[end][1]:
                values["bar_end"] = bounds[end][1]
            out.append(replace(note, **values) if values else note)
            tick = end

        def update(project):
            project.score.parts[part].notes[a:b] = out
        if self.mutate(update, selection=list(range(a, a + len(out))), current=a,
                       explicit=lambda item: item.part == part and a <= item.row < b
                       and item.kind not in edit_guard.BAR_KINDS):
            self.toast.show_text(tr("已取代{bars}的音。").format(bars=label), self.undo)
        return True

    def insert_clip(self, notes, *, notice=False):
        """Insert copied notes: complete repeats and bar lines come along when whole bars land on
        bar lines of the first part; otherwise bar items are left out (with a notice)."""
        plain, removed = clean_pasted(notes)
        chosen = plain
        if self.part == 0 and any(n.bar_start or n.bar_end for n in notes):
            with_bars, kept_removed = clean_pasted(notes, keep_bar_items=True)
            trial = self.project.score.copy()
            trial.parts[0].notes[self.insertion_index():self.insertion_index()] = with_bars
            if trial.edit_error() is None:
                chosen, removed = with_bars, kept_removed
        if self.insert_notes(chosen) and removed and notice:
            self.statusBar().showMessage(tr("已貼上；移除 {count} 個不完整的連線或小節記號。").format(count=removed), 8000)

    def note_command(self, command):
        if command == "toggle_mode":
            self.input_mode.setCurrentIndex(0 if self.input_mode.currentData() == "edit" else 1)
        elif command == "append_bar":
            self.insert_bars("end")
        elif command == "insert_bar_before":
            self.insert_bars("before")
        elif command == "insert_bars":
            self.insert_bars_dialog()
        elif command == "delete_bars":
            self.measure_action("delete")
        elif command == "play":
            self.play_midi()
        elif command == "search":
            self.palette.focus_search()
        elif command in ("part_up", "part_down"):
            self.switch_part(self.part + (-1 if command == "part_up" else 1), keep_time=True)
        elif command.startswith("favorite_"):
            ids = self.palette.section_ids("favorites")
            index = int(command.split("_")[1]) - 1
            if 0 <= index < len(ids):
                self.palette_apply(ids[index], "stay")
        elif command == "copy":
            self.copy_notes()
        elif command == "cut":
            if self.copy_notes():
                self.delete_note()
        elif command == "paste":
            self.paste_notes()
        elif command == "delete":
            self.delete_note()
        elif command == "duplicate":
            if self.notes.selection_kind == "bars":
                self.measure_action("duplicate")
                return
            if not self.require_complete_tuplets(self.notes.selected_rows()):
                return
            self.insert_clip(clip_notes(self.project.score, self.part, self.notes.selected_rows()))
        elif command == "clear":
            if self.palette.continuous:
                self.set_continuous("")
            else:
                self.notes.select_rows([])
        elif command in ("previous", "next"):
            self.bar_jump.setValue(max(1, self.current_bar() + (-1 if command == "previous" else 1)))
            self.jump_bar()
        elif command.startswith("duration_"):
            value = int(command.split("_")[1])
            self.flash_control(self.duration_buttons[value])
            self.edit_property("duration", value)
        elif command in ("raise", "lower", "shorter", "longer", "dot"):
            button = {"raise": self.octave_up_button, "lower": self.octave_down_button, "dot": self.dot_button}.get(command)
            if button is not None:
                self.flash_control(button)

            def change(note):
                if command in ("raise", "lower"):
                    return self.replace_note(note, {"octave": max(-2, min(2, note.octave + (1 if command == "raise" else -1)))})
                if command == "dot":
                    return replace(note, dotted=not note.dotted)
                values = list(DURATIONS)
                index = values.index(note.duration) + (1 if command == "shorter" else -1)
                return replace(note, duration=values[max(0, min(len(values) - 1, index))])
            kind = "octave" if command in ("raise", "lower") else "dot" if command == "dot" else "duration"
            if self.input_mode.currentData() == "edit":
                rows = self.notes.selected_rows()
            else:
                rows = None if kind == "duration" else self.modifier_rows(kind)
            if rows is None:
                self.next_note = change(self.next_note)
                self.update_selection()
            elif kind == "octave":
                part = self.part
                typed = self.just_typed_row()

                def update(project):
                    notes = project.score.parts[part].notes
                    for row in rows:
                        notes[row] = change(notes[row])
                if self.mutate(update):
                    if typed is not None and rows == [typed]:
                        self._remember_typed(typed)          # ↑ ↑ raises the same note twice
                        self.notes.flash(typed)
                else:
                    self.update_selection()
            elif rows:
                if not self.rhythm_change(rows, change):
                    self.update_selection()

    def ask_note_text(self, current):
        """The text to put above the note (None = cancel). Tests replace this method."""
        from PySide6.QtWidgets import QInputDialog
        value, ok = QInputDialog.getText(
            self, tr("音符上方文字"),
            tr("速度、表情或段落標題，例如：中速、深情地、引子。印在這個音的上方；留空即移除。"),
            QLineEdit.EchoMode.Normal, current)
        return value.strip() if ok else None

    def edit_note_text(self):
        """Free text above the current note: tempo words, expression, section headings."""
        rows = self.notes.selected_rows()
        if not rows:
            self.statusBar().showMessage(tr("請先點一個音符或休止符，再加上方文字。"), 6000)
            return False
        row = self.notes.currentRow() if self.notes.currentRow() in rows else rows[0]
        part = self.part
        value = self.ask_note_text(self.part_notes()[row].text)
        if value is None:
            return False

        def update(project):
            notes = project.score.parts[part].notes
            notes[row] = replace(notes[row], text=value)
        if self.mutate(update, selection=[row], current=row,
                       explicit=lambda item: item.part == part and item.row == row and item.kind == "text"):
            self.toast.show_text(tr("已加上「{text}」。").format(text=value) if value else tr("已移除上方文字。"), self.undo)
            return True
        return False

    def add_note_text(self, word) -> bool:
        """A ready-made word from the search (中速, 全弓, 15弦…) above the selected note; added
        after any text already there, so nothing is replaced."""
        rows = self.notes.selected_rows()
        if len(rows) != 1:
            message = tr("請先點一個音符或休止符，再加上文字「{word}」。").format(word=word) if not rows else \
                tr("上方文字一次加在一個音上：請只選一個音符或休止符。")
            self.statusBar().showMessage(message, 8000)
            self.toast.show_text("⚠ " + message)
            return False
        row, part = rows[0], self.part
        old = self.part_notes()[row].text
        from .text_words import contains_phrase
        value = (old + " " + word) if old and not contains_phrase(old, word) else (old or word)
        if value == old:
            self.toast.show_text(tr("這個音上已有「{word}」。").format(word=word))
            return False

        def update(project):
            notes = project.score.parts[part].notes
            notes[row] = replace(notes[row], text=value)
        if self.mutate(update, selection=[row], current=row,
                       explicit=lambda item: item.part == part and item.row == row and item.kind == "text"):
            self.palette.record_use("text.free")
            self.toast.show_text(tr("已加上「{text}」。").format(text=value), self.undo)
            return True
        return False

    def note_menu(self, position):
        menu = QMenu(self)
        bar = self.notes.measure_at(position)
        if bar is not None and bar not in self.selected_measures() and not self.notes.indexAt(position).isValid():
            self.notes.select_bar(bar)
        search = menu.addAction(tr("加記號…　/"), self.palette.focus_search)
        search.setEnabled(True)
        text = menu.addAction(tr("音符上方文字（中速、深情地、引子…）…"), self.edit_note_text)
        text.setEnabled(bool(self.notes.selected_rows()))
        if not text.isEnabled():
            text.setToolTip(tr("請先點一個音符或休止符"))
        for sid in self.palette.recents[:5]:
            symbol = symbols.SYMBOLS[sid]
            state = self.symbol_state(sid)
            action = menu.addAction(symbol.display_name, lambda s=sid: self.palette_apply(s, "click"))
            action.setEnabled(state.available)
        menu.addSeparator()
        self.add_measure_menu(menu)
        menu.addSeparator()
        for title, command in ((tr("剪下　Ctrl+X"), "cut"), (tr("複製　Ctrl+C"), "copy"), (tr("貼上　Ctrl+V"), "paste"),
                               (tr("重複　Ctrl+D"), "duplicate"), (tr("刪除　Delete"), "delete")):
            item = menu.addAction(title, lambda c=command: self.note_command(c))
            item.setEnabled(command == "paste" or bool(self.notes.selected_rows()))
        menu.addSeparator()
        marks = self.notation_menu()
        marks.setTitle(tr("基本記號 / 二胡"))
        menu.addMenu(marks)
        menu.exec(self.notes.viewport().mapToGlobal(position))

    def selected_measures(self):
        if self.notes.selection_kind == "bars" and self.notes.selected_bars:
            return list(self.notes.selected_bars)
        positions = self.notes.score_model.positions
        bar_of = self.notes.score_model.bar_of
        return sorted({bar_of(positions[row]) for row in self.notes.selected_rows() if row < len(positions)})

    def measure_ready(self):
        if self.project.mode != "visual":
            self.statusBar().showMessage(tr("目前內容不能使用小節工具，請先開啟可編輯的樂譜專案。"), 6000)
            return False
        return not (self.loading or (self.task and self.task_blocks_edit))

    def _part0_rows_to_active(self, result, rows):
        """Measure edits report part-0 rows; select the same bars in the part being edited."""
        if self.part == 0 or not rows:
            return rows, (rows[0] if rows else -1)
        from .model import Score
        notes0 = result.part_notes()[0]
        score = self.project.score
        positions, position = [], 0
        for note in notes0:
            positions.append(position)
            position += note.ticks
        bars = sorted({score.bar_index(positions[r]) for r in rows if r < len(positions)})
        trial = Score(meter=score.meter, pickup=score.pickup, parts=[score.parts[0]])
        part_notes = result.part_notes()[self.part] if self.part < len(result.part_notes()) else []
        trial.parts[0] = replace(trial.parts[0], notes=list(part_notes))
        ranges = trial.part_bars(0)
        out = [r for bar in bars if bar < len(ranges) for r in range(*ranges[bar])]
        return out, (out[0] if out else -1)

    @staticmethod
    def retyped(part, rows):
        """A note typed over (e.g. with 0) is replaced on purpose; its marks are not."""
        rows = set(rows)
        return lambda item: item.part == part and item.row in rows and item.kind == "note"

    def apply_measure_edit(self, result, *, fill=False, rows_are_active=False, explicit=None):
        selection, current = result.selection, result.current
        if not rows_are_active:
            selection, current = self._part0_rows_to_active(result, list(selection))
        if fill and selection:
            selection, current = (selection[0],), selection[0]
        changed = self.mutate(result.apply_to_project, selection=selection, current=current, explicit=explicit)
        if not changed:
            if [list(n) for n in result.part_notes()] != [p.notes for p in self.project.score.parts][:len(result.part_notes())]:
                return False
            self.notes.select_rows(selection, current)
        if fill:
            self.input_mode.setCurrentIndex(self.input_mode.findData("fill"))
        if result.notice:
            self.toast.show_text(result.notice, self.undo)
        self.measure_notice.hide()
        self.notes.scrollTo(self.notes.currentIndex())
        self.notes.setFocus()
        return True

    def insert_bars(self, position="after", count=1):
        if not self.measure_ready():
            return
        from .measures import insert_measures, measure_ranges
        try:
            self.capture_fields()
            ranges = measure_ranges(self.project.score)
            bars = self.selected_measures()
            if position not in ("before", "after", "end"):
                raise ValueError(tr("請選擇小節前、後或曲尾。"))
            at_bar = len(ranges) if position == "end" or not bars else bars[0] if position == "before" else bars[-1] + 1
            result = insert_measures(self.project.score, at_bar, count)
            self.apply_measure_edit(result, fill=True)
        except ValueError as exc:
            self.error(tr("無法新增小節"), exc)

    def measure_action(self, action):
        if not self.measure_ready():
            return
        bars = self.selected_measures()
        if not bars:
            self.statusBar().showMessage(tr("請先點小節號，或選取要處理的小節中的音符。"), 6000)
            return
        from .measures import clear_measures, delete_measures, duplicate_measures
        operations = {"clear": clear_measures, "delete": delete_measures, "duplicate": duplicate_measures}
        try:
            self.capture_fields()
            if action not in operations:
                raise ValueError(tr("不支援的小節操作。"))
            self.apply_measure_edit(operations[action](self.project.score, bars), fill=action == "clear",
                                    explicit=self.measure_explicit(action, bars))
        except ValueError as exc:
            self.error(tr("無法修改小節"), exc)

    def measure_explicit(self, action, bars):
        """What deleting or clearing bars removes on purpose: the content of those bars (clearing
        keeps their bar lines, repeats and jumps)."""
        score, chosen = self.project.score, set(bars)
        if action == "delete":
            return lambda item: score.bar_index(item.tick) in chosen
        if action == "clear":
            return lambda item: score.bar_index(item.tick) in chosen and item.kind not in edit_guard.BAR_KINDS
        return None

    def inspector_measure_action(self, key):
        if key in ("before", "after"):
            self.insert_bars(key)
        else:
            self.measure_action(key)

    def fill_rest(self, note):
        if not self.measure_ready():
            return
        from .measures import replace_rest_input
        rows = self.notes.selected_rows()
        if not rows:
            self.statusBar().showMessage(tr("請先點選要填寫的休止符 0，或按「＋新增小節」。"), 6000)
            return
        row = self.notes.currentRow() if self.notes.currentRow() in rows else rows[0]
        notes = self.part_notes()
        if all(notes[r].degree != 0 for r in rows):
            # 填寫 on written notes: a digit re-pitches them in place, keeping each duration,
            # so nothing in the bar moves; then go on to the next note, like filling rests.
            part, degree = self.part, note.degree

            def update(project):
                written = project.score.parts[part].notes
                for r in rows:
                    written[r] = self.replace_note(written[r], {"degree": degree})
            if self.mutate(update, explicit=self.retyped(part, rows)) and max(rows) + 1 < len(self.part_notes()):
                self.notes.select_rows([max(rows) + 1], max(rows) + 1)
                self.notes.scrollTo(self.notes.currentIndex())
            return
        try:
            result = replace_rest_input(self.project.score, row, note, self.part)
            if self.apply_measure_edit(result, rows_are_active=True):
                self._typed_row = result.current
                # Advance only to an immediately following rest. Never skip
                # existing melody or append time beyond the allocated measures.
                following = result.current + 1
                notes = self.part_notes()
                if following < len(notes) and notes[following].degree == 0:
                    self.notes.select_rows([following], following)
                    self.notes.scrollTo(self.notes.currentIndex())
        except ValueError as exc:
            self.statusBar().showMessage(str(exc), 10000)
            self.measure_notice.setText(str(exc))
            self.measure_notice.show()

    def insert_bars_dialog(self):
        if not self.measure_ready():
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(tr("新增小節"))
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        count = QSpinBox()
        count.setRange(1, 256)
        count.setObjectName("measureCount")
        position = QComboBox()
        position.setObjectName("measurePosition")
        for text, value in ((tr("所選小節之後"), "after"), (tr("所選小節之前"), "before"), (tr("曲尾"), "end")):
            position.addItem(text, value)
        form.addRow(tr("小節數"), count)
        form.addRow(tr("插入位置"), position)
        layout.addLayout(form)
        hint = QLabel(tr("新小節先填入足拍休止符；可用「填寫小節」直接輸入旋律。沒有選取時加到曲尾。"))
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.dialog_buttons(dialog, layout)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.insert_bars(position.currentData(), count.value())

    def add_measure_menu(self, menu):
        ready = self.project.mode == "visual"
        for text, fn in ((tr("前插一小節　Insert"), lambda: self.insert_bars("before")),
                         (tr("後插一小節"), lambda: self.insert_bars("after")),
                         (tr("曲尾加一小節　Ctrl+B"), lambda: self.insert_bars("end")),
                         (tr("新增多個小節…　Ctrl+Insert"), self.insert_bars_dialog)):
            menu.addAction(text, fn).setEnabled(ready)
        menu.addSeparator()
        selected = bool(self.selected_measures())
        for text, command in ((tr("重複所選小節"), "duplicate"), (tr("清空小節（保留拍數）"), "clear"),
                              (tr("刪除小節（後段前移）　Ctrl+Delete"), "delete")):
            action = menu.addAction(text, lambda a=command: self.measure_action(a))
            action.setEnabled(ready and selected)
            if not selected:
                action.setToolTip(tr("請先點小節號選取小節"))
        menu.setToolTipsVisible(True)

    def fill_note_more_menu(self):
        """≤ 1120 px: the note bar's less frequent tools live here (design §1.3)."""
        menu = self.note_more_menu
        menu.clear()
        for value, text in (("#", tr("升記號 ♯")), ("b", tr("降記號 ♭")), ("n", tr("還原記號 ♮"))):
            menu.addAction(text, lambda v=value: self.accidental_clicked(v))
        menu.addSeparator()
        menu.addAction(tr("延音線：連到下一個同音高的音"), lambda: self.palette_apply("line.tie", "click"))
        menu.addAction(tr("拆成三連音"), lambda: self.triplet_action())
        menu.addAction(tr("合併三連音"), lambda: self.triplet_action(True))
        menu.addSeparator()
        self.add_measure_menu(menu.addMenu(tr("小節")))

    def fill_bar(self):
        self.fix_problem()

    def current_bar(self):
        row = self.notes.currentRow()
        positions = self.notes.score_model.positions
        return self.notes.score_model.bar_of(positions[row]) + 1 if 0 <= row < len(positions) else 1

    def jump_bar(self):
        bar = self.bar_jump.value() - 1
        for number, rows in self.notes.score_model.bars:
            if number >= bar:
                self.notes.select_rows([rows[0]])
                self.notes.scrollTo(self.notes.currentIndex())
                self.notes.setFocus()
                return

    def goto_problem(self):
        """Visit the problems one after another (design §3.6)."""
        problems = getattr(self, "problem_list", [])
        if not problems:
            score = self.project.score
            message = score.rhythm_error() or self.current_notation_error()
            if message:
                self.goto_place(getattr(message, "part", 0), getattr(message, "row", None), getattr(message, "bar", None))
            return
        self.problem_cursor = (getattr(self, "problem_cursor", -1) + 1) % len(problems)
        self.problem_shown = problems[self.problem_cursor]
        part, bar, text, row = problems[self.problem_cursor]
        self._sync_fix_button()          # the fix offered is the fix of the problem shown
        if row is not None or bar is None:
            self.goto_place(part, row, bar)
            self.statusBar().showMessage(text, 10000)
            return
        self.switch_part(part)
        self.notes.select_bar(bar)
        self.notes.scrollTo(self.notes.currentIndex())
        self.notes.setFocus()

    def goto_place(self, part, row=None, bar=None):
        """Select the note (or the bar) a problem names and bring it into view."""
        score = self.project.score
        part = part if 0 <= (part or 0) < len(score.parts) else 0
        notes = score.parts[part].notes
        if part != self.part:
            self.switch_part(part)
        if row is not None and 0 <= row < len(notes):
            self.notes.select_rows([row], row)
        elif bar is not None and 0 <= bar < len(score.part_bars(part)):
            self.notes.select_bar(bar)
        elif notes:
            self.notes.select_rows([len(notes) - 1], len(notes) - 1)
        self.notes.scrollTo(self.notes.currentIndex())
        self.notes.setFocus()

    def fix_problem(self):
        """The one-click fix of the problem shown (``shown_problem``): fill the last bar, add the
        missing counterpart of a sign, or remove an orphan mark. Only fixes that create no new
        problem are offered (``notation_rules.fix_is_safe``)."""
        from .notation_rules import crossing
        score = self.project.score
        problem = self.shown_problem()
        fix = getattr(problem, "fix", None) if problem else None
        if not fix:
            rhythm = score.rhythm_error()
            if rhythm is not None and crossing(score) is not None:
                # A note across a bar line: rests cannot fix it.
                self.goto_place(rhythm.part, rhythm.row)
                self.statusBar().showMessage(str(rhythm), 10000)
                self.toast.show_text("⚠ " + str(rhythm))
            elif problem:
                self.goto_place(getattr(problem, "part", 0), getattr(problem, "row", None),
                                getattr(problem, "bar", None))
                self.statusBar().showMessage(str(problem), 10000)
            return
        part, row = problem.part, problem.row

        def update(project):
            notation_edit.apply_fix(project.score, fix, part, row)
        if fix[0] == "fill":
            if self.mutate(update):
                self.toast.show_text(tr("已補齊休止符。"), self.undo)
            return
        if fix[0] == "add":
            bar, item = fix[1], fix[2]

            def replaced(item_):
                return item_.part == 0 and item_.kind == "bar_end" and item_.ident in notation_edit.BAR_STYLE_SLOT
            if self.mutate(update, explicit=replaced):
                self.toast.show_text(tr("已在{bar}加上「{name}」，可用復原還原。").format(
                    bar=bar_label(self.project.score, bar), name=symbols.SYMBOLS[item].display_name), self.undo)
            return

        def explicit(item):
            return item.part == part and item.kind in ("tie", *edit_guard.LINE_KINDS, *edit_guard.BAR_KINDS)
        if self.mutate(update, explicit=explicit):
            self.toast.show_text(tr("已移除孤立的記號，可用復原還原。"), self.undo)

    def toggle_auto(self, checked):
        if checked:
            self.autopreview.start()
        else:
            self.autopreview.stop()
            if not self.render_pending_manual:
                self.render_pending = self.render_pending_pdf = False

    def change_workspace(self, index):
        self.print_preview_active = False
        self.autopreview.stop()
        self.workspace_view.blockSignals(True)
        self.workspace_view.setCurrentIndex(0)
        self.workspace_view.blockSignals(False)
        self.editor_container.show()
        self.side_panel.setVisible(not getattr(self, "panel_collapsed", False))
        self.preview_panel.hide()
        self.preview_tabs.setCurrentIndex(0)
        self.preview_heading.setText(tr("列印預覽"))
        self.auto_check.hide()
        self.preview_button.hide()
        self.preview_status.hide()

    def show_print_preview(self):
        self.print_preview_active = True
        self.editor_container.hide()
        self.side_panel.hide()
        self.preview_panel.show()
        self.preview_tabs.setCurrentIndex(0)
        self.preview_heading.setText(tr("列印預覽"))
        self.auto_check.show()
        self.preview_button.show()
        self.preview_status.show()
        self.capture_fields()
        self.guide.mark(5)
        if not self.result or self.rendered_revision != self.revision or not self.result.pdf.is_file():
            self.render_score(True)

    def show_work_log(self):
        dialog = QDialog(self)
        dialog.setWindowTitle(tr("工作紀錄"))
        dialog.resize(720, 460)
        layout = QVBoxLayout(dialog)
        text = QPlainTextEdit(self.log.toPlainText() or tr("目前沒有排版紀錄。"))
        text.setReadOnly(True)
        layout.addWidget(text)
        copy = QPushButton(tr("複製完整紀錄"))
        copy.clicked.connect(lambda: QApplication.clipboard().setText(text.toPlainText()))
        layout.addWidget(copy)
        self.dialog_buttons(dialog, layout)
        dialog.exec()

    def copy_log(self):
        QApplication.clipboard().setText(self.log.toPlainText())

    def zoom_photo(self, factor):
        if self.photo_pixmap is None or self.photo_pixmap.isNull():
            return
        max_scale = min(5, (40_000_000 / (self.photo_pixmap.width() * self.photo_pixmap.height())) ** .5)
        self.photo_scale = max(.1, min(max_scale, (max(100, self.photo_scroll.viewport().width() - 16) / self.photo_pixmap.width())
                                       if not factor else self.photo_scale * factor))
        pixmap = self.photo_pixmap.scaled(self.photo_pixmap.size() * self.photo_scale, Qt.AspectRatioMode.KeepAspectRatio,
                                          Qt.TransformationMode.SmoothTransformation)
        self.photo_label.setPixmap(pixmap)
        self.photo_label.resize(pixmap.size())

    def layout_settings(self):
        dialog = QDialog(self)
        dialog.setWindowTitle(tr("紙張與排版"))
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        paper = QComboBox()
        for title, value in (("A4", "a4"), ("Letter", "letter")):
            paper.addItem(title, value)
        paper.setCurrentIndex(paper.findData(self.project.score.paper_size))
        size = QSpinBox()
        size.setRange(16, 30)
        size.setValue(self.project.score.staff_size)
        bars = QSpinBox()
        bars.setRange(0, 8)
        bars.setSpecialValueText(tr("自動"))
        bars.setValue(self.project.score.bars_per_line)
        variant = QComboBox()
        variant.setObjectName("print_variant")
        variant.addItem(tr("繁體（內、頓弓）"), "tc")
        variant.addItem(tr("簡體（内、顿弓）"), "sc")
        variant.setCurrentIndex(variant.findData(self.project.score.print_variant))
        for title, control in ((tr("紙張"), paper), (tr("譜字大小"), size), (tr("每行小節數"), bars),
                               (tr("技法字印"), variant)):
            form.addRow(title, control)
        layout.addLayout(form)
        note = QLabel(tr("紙張與譜字大小用於正式 PDF；每行小節數用於視覺草稿轉出的簡譜。"))
        note.setWordWrap(True)
        layout.addWidget(note)
        self.dialog_buttons(dialog, layout)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            def update(project):
                project.score.paper_size = paper.currentData()
                project.score.staff_size = size.value()
                project.score.bars_per_line = bars.value()
                project.score.print_variant = variant.currentData()
            self.mutate(update)

    def show_score_info(self):
        self.info_popup.adjustSize()
        anchor = self.more_button.mapToGlobal(QPoint(self.more_button.width() - self.info_popup.width(),
                                                     self.more_button.height() + 4))
        self.info_popup.move(anchor)
        self.info_popup.show()
        self.lyrics.setFocus()

    @staticmethod
    def dialog_buttons(dialog, layout):
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(tr("確定"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("取消"))
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

    # ---------------------------------------------------------------- palette and inspector
    def current_target(self) -> notation_edit.Target:
        view = self.notes
        kind = view.selection_kind
        rows = tuple(r for r in view.selected_rows() if r < len(self.part_notes()))
        if kind == "barline" and view.selected_barline >= 0:
            return notation_edit.Target(notation_edit.SELECT_BARLINE, self.part, (), (), view.selected_barline)
        if kind == "bars" and view.selected_bars:
            return notation_edit.Target(notation_edit.SELECT_BARS, self.part, rows, tuple(view.selected_bars))
        if rows:
            return notation_edit.Target(notation_edit.SELECT_NOTES, self.part, rows)
        return notation_edit.Target(notation_edit.SELECT_NONE, self.part)

    def symbol_state(self, sid):
        key = (self.revision, self.current_target(), self.project.score.print_variant)
        if getattr(self, "_state_key", None) != key:
            self._state_key = key
            self._base_error = self.current_edit_error()
        return notation_edit.symbol_state(self.project.score, self.current_target(), sid, base_error=self._base_error)

    def symbol_where(self, sid):
        return notation_edit.target_where(self.project.score, self.current_target(), sid)

    def palette_apply(self, sid, how="click"):
        """A palette cell / search result / favourite was activated (design §2.6–§2.7)."""
        from .symbols_ui import text_word
        if word := text_word(sid):
            if self.add_note_text(word) and how == "click":
                self.notes.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        if how == "continuous":
            self.set_continuous("" if self.palette.continuous == sid else sid)
            if self.palette.continuous and self.current_target().kind != "none":
                self._apply_symbol(sid)
            return
        if self._apply_symbol(sid) and how == "click":
            self.notes.setFocus(Qt.FocusReason.OtherFocusReason)

    def _apply_symbol(self, sid) -> bool:
        target = self.current_target()
        symbol = symbols.SYMBOLS[sid]
        if sid == "tuplet.3":
            self.triplet_action()
            self.palette.record_use(sid)
            return True
        if sid == "text.free":
            if self.edit_note_text():
                self.palette.record_use(sid)
            return True
        if symbol.storage == "composite":
            self.notation_settings(2)
            return True
        state = notation_edit.symbol_state(self.project.score, target, sid)
        if not state.available:
            self.statusBar().showMessage(state.reason, 8000)
            self.toast.show_text("⚠ " + state.reason)
            return False
        notice = {}

        def update(project):
            notice["text"] = notation_edit.toggle_symbol(project.score, target, sid)
        if not self.mutate(update, selection=target.rows, explicit=self.symbol_explicit(sid)):
            return False
        if target.kind == "bars":
            self.notes.select_bars(list(target.bars))
        elif target.kind == "barline":
            self.notes.select_barline(target.barline)
        self.palette.record_use(sid)
        self.guide.mark(3)
        if target.rows:
            self.notes.flash(target.rows[0])          # landing feedback (colour only, design §4.5)
        text = notice.get("text", "")
        if symbol.storage == "bar":
            # Building a repeat or jump one sign at a time: say what it still needs.
            from .notation_rules import structure_problems
            pending = [p for p in structure_problems(self.project.score) if p.incomplete]
            if pending:
                text = (text + " " if text else "") + str(pending[0])
        if text:
            self.toast.show_text(text, self.undo)
        self.palette.invalidate()
        return True

    @staticmethod
    def symbol_explicit(sid):
        """Adding or removing a symbol replaces or removes symbols of its own kind (same slot,
        same line family, bar items) on purpose; the detail strip names them beforehand."""
        symbol = symbols.SYMBOLS[sid]

        def explicit(item):
            if symbol.storage == "marks":
                return item.kind == "mark" and (item.ident == sid or bool(symbol.slot)
                                                and symbols.SYMBOLS[item.ident].slot == symbol.slot)
            if symbol.storage == "spans":
                return item.kind in edit_guard.LINE_KINDS and item.ident == symbol.family
            if symbol.storage == "tie":
                return item.kind == "tie"
            return item.kind in edit_guard.BAR_KINDS
        return explicit

    def remove_mark(self, sid):
        """× on an inspector chip: remove the mark from the whole selection (one undo step)."""
        target = self.current_target()
        symbol = symbols.SYMBOLS[sid]
        part = self.part

        def update(project):
            score = project.score
            if symbol.kind == "bar":
                bars = sorted(self._bars_of_target(target))
                for bar in bars:
                    for edge in ("start", "end"):
                        if symbol.bar_edge == edge and notation_edit._bar_has(score, bar, sid):
                            if sid in notation_edit.VOLTAS or sid == "volta.final":
                                notation_edit._remove_volta(score, bar, sid)
                            else:
                                notation_edit.set_bar_item(score, bar, sid, present=False)
            elif symbol.storage == "spans":
                notation_edit.remove_lines(score, part, list(target.rows), symbol.family)
            else:
                notation_edit.remove_symbol(score, part, list(target.rows), sid)
        if self.mutate(update, selection=target.rows, explicit=self.symbol_explicit(sid)):
            if target.kind == "bars":
                self.notes.select_bars(list(target.bars))
            elif target.kind == "barline":
                self.notes.select_barline(target.barline)
            self.toast.show_text(tr("已移除「{name}」。").format(name=symbol.display_name), self.undo)

    def _bars_of_target(self, target):
        score = self.project.score
        if target.kind == "barline":
            return {b for b in (target.barline - 1, target.barline) if 0 <= b < len(score.part_bars(0))}
        if target.kind == "bars":
            return set(target.bars)
        return set(notation_edit._bar_of_rows(score, target.part, target.rows))

    def refresh_inspector(self):
        score = self.project.score
        target = self.current_target()
        notes = self.part_notes()
        part_name = score.parts[self.part].name or (tr("第 {n} 聲部").format(n=self.part + 1) if len(score.parts) > 1 else "")
        prefix = part_name + " · " if part_name else ""
        if target.kind == "none":
            total = sum(n.ticks for n in score.notes)
            bars = score.bar_index(total - 1) + 1 if total else 0
            self.inspector.show_content(tr("沒有選取"), "", [], empty_hint=tr(
                "點音符選取 · 點小節號選整小節 · 點小節線選邊界\n{bars} 小節 · {parts} 個聲部").format(
                bars=max(0, bars - bool(score.pickup)), parts=len(score.parts)))
            return
        if target.kind == "barline":
            k = target.barline
            title = tr("第 {a} 與第 {b} 小節之間").format(a=notation_edit.bar_number(score, k - 1),
                                                          b=notation_edit.bar_number(score, k))
            marks = []
            ranges = score.part_bars(0)
            if 0 <= k - 1 < len(ranges):
                marks += [(s, 1, 1) for s in score.parts[0].notes[ranges[k - 1][1] - 1].bar_end if s != "volta.end"]
            if 0 <= k < len(ranges):
                marks += [(s, 1, 1) for s in score.parts[0].notes[ranges[k][0]].bar_start]
            self.inspector.show_content(title, tr("小節線屬於所有聲部"), marks)
            return
        if target.kind == "bars":
            bars = sorted(target.bars)
            first, last = notation_edit.bar_number(score, bars[0]), notation_edit.bar_number(score, bars[-1])
            label = (tr("弱起小節") if score.pickup and bars[0] == 0 and len(bars) == 1 else
                     tr("第 {bar} 小節").format(bar=first) if len(bars) == 1 else tr("第 {a}–{b} 小節").format(a=first, b=last))
            title = tr("{bars}（所有聲部）").format(bars=label) if len(score.parts) > 1 else label
            items = self._bar_marks(score, bars)
            complete = all((p, b) not in self.notes.problems for p in range(len(score.parts)) for b in bars)
            props = "{} · {}".format(score.meter, tr("拍數完整") if complete else tr("拍數不完整"))
            self.inspector.show_content(title, props, items, measure=True)
            return
        rows = list(target.rows)
        if len(rows) == 1:
            row = rows[0]
            note = notes[row]
            place = notation_edit._note_place(score, self.part, row)
            marks = [(m, 1, 1) for m in note.marks]
            marks += [(s, 1, 1) for s in self._spans_through(rows)]
            if note.tie:
                marks.append(("line.tie", 1, 1))
            self.inspector.show_content(prefix + place, self.inspector.note_props(note), marks,
                                        grace=note.degree != 0)
            return
        bars = notation_edit._bar_of_rows(score, self.part, rows)
        span = tr("第 {a}–{b} 小節").format(a=notation_edit.bar_number(score, bars[0]),
                                            b=notation_edit.bar_number(score, bars[-1])) if len(bars) > 1 else \
            tr("第 {bar} 小節").format(bar=notation_edit.bar_number(score, bars[0]))
        counts = {}
        for r in rows:
            for m in notes[r].marks:
                counts[m] = counts.get(m, 0) + 1
        marks = [(m, c, len(rows)) for m, c in sorted(counts.items(), key=lambda item: symbols.ORDER[item[0]])]
        marks += [(s, 1, 1) for s in self._spans_through(rows)]
        values = {(n.degree, n.duration) for n in (notes[r] for r in rows)}
        props = tr("不同") if len(values) > 1 else self.inspector.note_props(notes[rows[0]])
        self.inspector.show_content(prefix + tr("已選 {count} 個音（{span}）").format(count=len(rows), span=span),
                                    props, marks)

    def _spans_through(self, rows):
        from .notation_rules import span_pairs
        found = []
        for family, a, b, sid in span_pairs(self.part_notes()):
            if any(a <= r <= b for r in rows) and sid not in found:
                found.append(sid)
        return found

    def _bar_marks(self, score, bars):
        ranges = score.part_bars(0)
        out = []
        for bar in bars:
            if bar >= len(ranges):
                continue
            a, b = ranges[bar]
            for item in (*score.parts[0].notes[a].bar_start, *score.parts[0].notes[b - 1].bar_end):
                if item != "volta.end" and item not in [m for m, _, _ in out]:
                    out.append((item, 1, 1))
        return out

    def palette_hover(self, sid):
        """Outline where a hovered cell would go (design §2.3)."""
        if not sid:
            self.notes.preview_target = None
        else:
            symbol = symbols.SYMBOLS[sid]
            target = self.current_target()
            state = self.palette.cached_state(sid)
            if state is not None and not state.available:
                self.notes.preview_target = None
            elif symbol.kind == "bar":
                bars, reason = notation_edit._bar_targets(self.project.score, target, symbol)
                if reason or not bars:
                    self.notes.preview_target = None
                elif target.kind == "barline":
                    self.notes.preview_target = ("boundary", target.barline)
                elif sid.startswith("volta."):
                    self.notes.preview_target = ("bars", bars)
                else:
                    edge = bars[0] if symbol.bar_edge == "start" else bars[-1] + 1
                    self.notes.preview_target = ("boundary", edge)
            elif target.rows:
                self.notes.preview_target = ("notes", list(target.rows))
            else:
                self.notes.preview_target = None
        self.notes.viewport().update()

    def set_continuous(self, sid):
        self.palette.set_continuous(sid)
        self.notes.stamp = sid
        if sid:
            name = symbols.SYMBOLS[sid].display_name
            self.stamp_bar.setText(tr("連續套用：{name} — 點音符加上，再點一次移除。Esc 結束").format(name=name))
            self.stamp_bar.show()
            self.notes.viewport().setCursor(Qt.CursorShape.CrossCursor)
        else:
            self.stamp_bar.hide()
            self.notes.viewport().unsetCursor()
        self.update_selection()

    def stamp_clicked(self, row):
        if self.palette.continuous:
            self._apply_symbol(self.palette.continuous)

    # ---------------------------------------------------------------- parts
    def switch_part(self, index, keep_time=False, row=None):
        score = self.project.score
        if not 0 <= index < len(score.parts) or index == self.part and row is None:
            return
        position = None
        if keep_time and self.notes.currentRow() >= 0:
            position = self.notes.score_model.positions[self.notes.currentRow()]
        self.capture_fields()
        self.notes.set_score(score, index)
        target = row
        if position is not None and target is None:
            positions, at = [], 0
            for note in score.parts[index].notes:
                positions.append(at)
                at += note.ticks
            candidates = [r for r, p in enumerate(positions) if p <= position]
            target = candidates[-1] if candidates else (0 if positions else None)
        self.sync(selection=[target] if target is not None else [], current=target if target is not None else -1)
        if target is not None:
            self.notes.scrollTo(self.notes.currentIndex())

    def part_clicked(self, part, row):
        self.switch_part(part, row=row if row >= 0 else None)

    def part_chip_clicked(self, index):
        if index == self.part:
            self.part_settings(index)
        else:
            self.switch_part(index, keep_time=True)
        self.part_buttons[min(self.part, len(self.part_buttons) - 1)].setChecked(True)

    def add_part(self):
        score = self.project.score
        if len(score.parts) >= MAX_PARTS:
            return
        name = tr("二胡二") if len(score.parts) == 1 else tr("第 {n} 聲部").format(n=len(score.parts) + 1)

        def update(project):
            if not project.score.parts[0].name and len(project.score.parts) == 1:
                project.score.parts[0] = replace(project.score.parts[0], name=tr("二胡一"))
            notation_edit.add_part(project.score, name)
        if self.mutate(update, selection=[], current=-1):
            self.switch_part(len(self.project.score.parts) - 1)
            self.toast.show_text(tr("已新增「{name}」，內容是與第一聲部等長的休止符。").format(name=name), self.undo)

    def part_settings(self, index):
        score = self.project.score
        part = score.parts[index]
        dialog = QDialog(self)
        dialog.setObjectName("part_dialog")
        dialog.setWindowTitle(tr("聲部設定"))
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        name = QLineEdit(part.name)
        name.setMaxLength(40)
        lyrics = QLineEdit(part.lyrics)
        lyrics.setPlaceholderText(tr("歌詞（選填；純器樂可留白）"))
        form.addRow(tr("名稱"), name)
        form.addRow(tr("歌詞"), lyrics)
        layout.addLayout(form)
        moves = QDialogButtonBox()
        up = moves.addButton(tr("上移"), QDialogButtonBox.ButtonRole.ActionRole)
        down = moves.addButton(tr("下移"), QDialogButtonBox.ButtonRole.ActionRole)
        delete = moves.addButton(tr("刪除聲部"), QDialogButtonBox.ButtonRole.DestructiveRole)
        up.setEnabled(index > 1)
        down.setEnabled(0 < index < len(score.parts) - 1)
        delete.setEnabled(index > 0)
        if index == 0:
            delete.setToolTip(tr("第一聲部含小節線與反覆記號，不能刪除；可刪除其他聲部。"))
        layout.addWidget(moves)
        choice = {}
        up.clicked.connect(lambda: (choice.update(action="up"), dialog.accept()))
        down.clicked.connect(lambda: (choice.update(action="down"), dialog.accept()))
        delete.clicked.connect(lambda: (choice.update(action="delete"), dialog.accept()))
        self.dialog_buttons(dialog, layout)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        action = choice.get("action")
        target = index

        def update(project):
            nonlocal target
            parts = project.score.parts
            parts[index] = replace(parts[index], name=name.text().strip(), lyrics=lyrics.text())
            if action == "delete":
                notation_edit.remove_part(project.score, index)
                target = max(0, index - 1)
            elif action in ("up", "down"):
                other = index - 1 if action == "up" else index + 1
                parts[index], parts[other] = parts[other], parts[index]
                target = other
        if self.mutate(update, selection=[], current=-1, explicit=lambda item: action == "delete"):
            self.switch_part(target, row=None) if target != self.part else self.sync()

    def new_from_template(self, kind):
        from .model import Part, Project, Score
        if not self.guard_unsaved():
            return
        # New scores print at staff size 24 (a book-like page); files keep the size they were saved with.
        score = Score(title=tr("未命名樂曲"), key="D", meter="4/4", tempo=72, staff_size=NEW_SCORE_STAFF_SIZE)
        bars = score.rests_between(0, 8 * score.bar_ticks)
        if kind == "duet":
            score.parts = [Part(tr("二胡一"), list(bars)), Part(tr("二胡二"), list(bars))]
        else:
            score.parts = [Part(tr("二胡") if kind == "solo" else "", list(bars))]
        self.reset(Project(score))
        self.input_mode.setCurrentIndex(self.input_mode.findData("fill"))
        self.notes.select_rows([0], 0)
        self.notes.setFocus()

    def open_example(self, key):
        if key == "twinkle":
            self.load_demo()
            return
        from .engine import app_root
        path = app_root() / "examples" / "duet.jps"
        self.open_file(path=path)


def _beats(ticks):
    from fractions import Fraction
    value = Fraction(ticks, 48)
    return str(value)
