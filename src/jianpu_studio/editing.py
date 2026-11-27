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
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from . import __version__, i18n, notation_edit, symbols
from .i18n import N_, tr
from .measures import clean_pasted, clip_notes
from .migrate import LEGACY_FIELDS
from .model import DURATIONS, MAX_PARTS, Note, pickup_choices
from .notation_actions import NotationActions

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

    def mutate(self, operation, *, selection=None, current=None):
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
            if self.current_notation_error() is None:
                if error := project.score.notation_error():
                    raise ValueError(error)
        except (ValueError, TypeError) as exc:
            self.error(tr("無法套用這項修改"), exc)
            self.sync(selection=before[1], current=before[2])
            return False
        if project == self.project:
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
        self.sync(selection=rows, current=current)

    def redo(self):
        if not self.future or (self.task and self.task_blocks_edit):
            return
        self.history.append(self.snapshot())
        rows, current = self._restore(self.future.pop())
        self.changed()
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
            self.key_field.setCurrentText(score.key)
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
            for bar, (a, b) in enumerate(ranges):
                length = sum(n.ticks for n in part.notes[a:b])
                expected = score.first_bar_ticks if bar == 0 else score.bar_ticks
                last = bar == len(ranges) - 1
                if length > expected:
                    problems[(p, bar)] = tr("超拍")
                elif length < expected and not last:
                    problems[(p, bar)] = tr("少 {beats} 拍").format(beats=_beats(expected - length))
                position += length
            if part.notes:
                target = score.final_bar_targets(position)[0]
                if target != position and ranges:
                    problems[(p, len(ranges) - 1)] = tr("少 {beats} 拍").format(beats=_beats(target - position))
        for (p, bar), text in sorted(problems.items(), key=lambda item: (item[0][1], item[0][0])):
            bars_flagged.add(bar)
            found.append((p, bar, text))
        error = score.rhythm_error() or self.current_notation_error()
        self.problem_list = found
        self.notes.set_problems(problems, bars_flagged, set())
        if error or found:
            first = found[0] if found else None
            if first:
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
        self.fill_button.setVisible(bool(found) and any(bar == len(score.part_bars(p)) - 1 for p, bar, _ in found))

    def capture_fields(self, *_):
        if self.loading or (self.task and self.task_blocks_edit):
            return
        part = min(self.part, len(self.project.score.parts) - 1)
        values = dict(title=self.title_field.text(), composer=self.composer_field.text(),
                      key=self.key_field.currentText(), meter=self.meter_field.currentText(),
                      tempo=self.tempo_field.value(), with_staff=self.staff.isChecked())
        lyrics = self.lyrics.text()
        score = self.project.score
        if all(getattr(score, name) == value for name, value in values.items()) and score.parts[part].lyrics == lyrics:
            return

        def update(project):
            for name, value in values.items():
                setattr(project.score, name, value)
            project.score.parts[part] = replace(project.score.parts[part], lyrics=lyrics)
            if project.score.pickup and project.score.pickup not in pickup_choices(project.score.bar_ticks):
                project.score.pickup = 0
        self.mutate(update)

    def change_pickup(self, *_):
        if self.loading:
            return
        ticks = self.pickup_field.currentData()
        if ticks is None or ticks == self.project.score.pickup:
            return

        def update(project):
            project.score.pickup = ticks
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
        dotted = {n.dotted for n in values}
        self.dot_button.setChecked(dotted == {True})
        accidentals = {n.accidental for n in values}
        for value, button in self.accidental_buttons.items():
            button.setChecked(accidentals == {value})
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
        if mode == "fill":
            return tr("填寫小節 · 下一個音 {beats} 拍 · 點休止符 0 後按數字鍵").format(beats=self.next_note.beats)
        note = self.next_note
        pitch = tr("休止 0") if note.degree == 0 else {"": "", "#": "♯", "b": "♭", "n": "♮"}[note.accidental] + str(note.degree)
        position = {"after": tr("所選之後"), "before": tr("所選之前"), "end": tr("曲尾")}[self.insert_position.currentData()]
        return tr("新增模式 · 下一個：{pitch} · {beats} 拍 · 插在{position} · Q–S 時值").format(
            pitch=pitch, beats=note.beats, position=position if rows else tr("曲尾"))

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
        if rows:
            part = self.part

            def update(project):
                notes = project.score.parts[part].notes
                for row in rows:
                    notes[row] = self.replace_note(notes[row], {name: value})
            self.mutate(update)

    def duration_clicked(self, value):
        self.duration.setCurrentIndex(self.duration.findData(value))
        self.flash_control(self.duration_buttons[value])
        self.notes.setFocus()

    def accidental_clicked(self, value):
        rows = self.notes.selected_rows()
        notes = self.part_notes()
        values = [notes[r] for r in rows] if self.input_mode.currentData() == "edit" else [self.next_note]
        current = {n.accidental for n in values}
        self.accidental.setCurrentIndex(self.accidental.findData("" if current == {value} else value))
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
            return
        if sum(len(p.notes) for p in self.project.score.parts) + len(notes) > 10000:
            self.error(tr("無法新增"), tr("每份樂譜最多支援 10,000 個音符。"))
            return
        index = self.insertion_index()
        previous_rows, previous_current = self.notes.selected_rows(), self.notes.currentRow()
        part = self.part

        def insert(project):
            project.score.parts[part].notes[index:index] = notes
        before = self.insert_position.currentData() == "before"
        selection = [r + len(notes) for r in previous_rows] if before else range(index, index + len(notes))
        current = previous_current + len(notes) if before and previous_current >= 0 else -1 if before else index + len(notes) - 1
        if self.mutate(insert, selection=selection, current=current):
            self.notes.scrollTo(self.notes.currentIndex())

    def input_pitch(self, degree):
        button = self.pitch_buttons[(1, 2, 3, 4, 5, 6, 7, 0).index(degree)]
        self.flash_control(button)
        if self.input_mode.currentData() == "edit":
            self.edit_property("degree", degree)
            if self.notes.selected_rows():
                self.guide.mark(2)
        elif self.input_mode.currentData() == "fill":
            self.fill_rest(self.replace_note(self.next_note, {"degree": degree}))
            self.guide.mark(2)
        else:
            self.insert_notes([self.replace_note(self.next_note, {"degree": degree})])
            self.guide.mark(2)
        if self.notes.currentRow() >= 0:
            self.notes.flash(self.notes.currentRow())
        self.notes.setFocus()

    def delete_note(self):
        rows = self.notes.selected_rows()
        if not rows or not self.require_complete_tuplets(rows):
            return
        part = self.part
        next_row = min(rows[0], len(self.part_notes()) - len(rows) - 1)

        def delete(project):
            selected = set(rows)
            project.score.parts[part].notes = [n for i, n in enumerate(project.score.parts[part].notes) if i not in selected]
        self.mutate(delete, selection=[next_row] if next_row >= 0 else [], current=next_row)

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
        self.insert_clip(notes, notice=True)

    def insert_clip(self, notes, *, notice=False):
        """Insert copied notes: complete repeats and bar lines come along when whole bars land on
        bar lines of the first part; otherwise bar items are left out (with a notice)."""
        plain, removed = clean_pasted(notes)
        chosen = plain
        if self.part == 0 and any(n.bar_start or n.bar_end for n in notes):
            with_bars, kept_removed = clean_pasted(notes, keep_bar_items=True)
            trial = self.project.score.copy()
            trial.parts[0].notes[self.insertion_index():self.insertion_index()] = with_bars
            if trial.notation_error() is None:
                chosen, removed = with_bars, kept_removed
        self.insert_notes(chosen)
        if removed and notice:
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
            if self.input_mode.currentData() != "edit":
                self.next_note = change(self.next_note)
                self.update_selection()
            else:
                rows = self.notes.selected_rows()
                part = self.part

                def update(project):
                    notes = project.score.parts[part].notes
                    for row in rows:
                        notes[row] = change(notes[row])
                self.mutate(update)

    def note_menu(self, position):
        menu = QMenu(self)
        bar = self.notes.measure_at(position)
        if bar is not None and bar not in self.selected_measures() and not self.notes.indexAt(position).isValid():
            self.notes.select_bar(bar)
        search = menu.addAction(tr("加記號…　/"), self.palette.focus_search)
        search.setEnabled(True)
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

    def apply_measure_edit(self, result, *, fill=False, rows_are_active=False):
        selection, current = result.selection, result.current
        if not rows_are_active:
            selection, current = self._part0_rows_to_active(result, list(selection))
        if fill and selection:
            selection, current = (selection[0],), selection[0]
        changed = self.mutate(result.apply_to_project, selection=selection, current=current)
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
            self.apply_measure_edit(operations[action](self.project.score, bars), fill=action == "clear")
        except ValueError as exc:
            self.error(tr("無法修改小節"), exc)

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
        try:
            result = replace_rest_input(self.project.score, row, note, self.part)
            if self.apply_measure_edit(result, rows_are_active=True):
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
        if any(p.notes for p in self.project.score.parts):
            self.mutate(lambda p: p.score.fill_last_bar())

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
            import re
            message = score.rhythm_error() or score.notation_error() or ""
            match = re.search(r"第\s*(\d+)\s*[個个]音符", message)
            row = int(match[1]) - 1 if match else len(score.notes) - 1
            if row >= 0:
                self.switch_part(0)
                self.notes.select_rows([min(row, len(score.notes) - 1)])
                self.notes.scrollTo(self.notes.currentIndex())
                self.notes.setFocus()
            return
        self.problem_cursor = (getattr(self, "problem_cursor", -1) + 1) % len(problems)
        part, bar, _ = problems[self.problem_cursor]
        self.switch_part(part)
        self.notes.select_bar(bar)
        self.notes.scrollTo(self.notes.currentIndex())
        self.notes.setFocus()

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
        dialog.setWindowTitle(tr("作業訊息"))
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
            self._base_error = self.current_notation_error()
        return notation_edit.symbol_state(self.project.score, self.current_target(), sid, base_error=self._base_error)

    def symbol_where(self, sid):
        return notation_edit.target_where(self.project.score, self.current_target(), sid)

    def palette_apply(self, sid, how="click"):
        """A palette cell / search result / favourite was activated (design §2.6–§2.7)."""
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
        if not self.mutate(update, selection=target.rows):
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
        if text:
            self.toast.show_text(text, self.undo)
        self.palette.invalidate()
        return True

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
        if self.mutate(update, selection=target.rows):
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
        if self.mutate(update, selection=[], current=-1):
            self.switch_part(target, row=None) if target != self.part else self.sync()

    def new_from_template(self, kind):
        from .model import Part, Project, Score
        if not self.guard_unsaved():
            return
        score = Score(title=tr("未命名樂曲"), key="D", meter="4/4", tempo=72)
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
