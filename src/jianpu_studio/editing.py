"""Editing actions shared by the compact workspace and its keyboard commands."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, replace

from PySide6.QtCore import QEvent, QMimeData, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from .model import DURATIONS, MAX_FILE_BYTES, Note

NOTE_MIME = "application/x-jianpu-studio-notes"


class EditActions:
    def eventFilter(self, watched, event):
        if watched is getattr(self, "source", None) and event.type() in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress):
            for key, action in ((QKeySequence.StandardKey.Undo, self.undo), (QKeySequence.StandardKey.Redo, self.redo)):
                if event.matches(key):
                    if event.type() == QEvent.Type.KeyPress:
                        action()
                    event.accept()
                    return True
        return super().eventFilter(watched, event)

    def source_menu(self, position):
        menu = QMenu(self)
        menu.addAction(self.undo_action)
        menu.addAction(self.redo_action)
        menu.addSeparator()
        for title, action in (("剪下", self.source.cut), ("複製", self.source.copy), ("貼上", self.source.paste), ("全選", self.source.selectAll)):
            menu.addAction(title, action)
        menu.exec(self.source.mapToGlobal(position))

    def snapshot(self):
        return self.project.snapshot(), tuple(self.notes.selected_rows()), self.notes.currentRow()

    def mutate(self, operation, *, selection=None, current=None):
        if self.loading or (self.task and self.task_blocks_edit):
            return False
        before = self.snapshot()
        project = self.project.snapshot()
        try:
            operation(project)
            project.score.validate()
        except (ValueError, TypeError) as exc:
            self.error("無法套用這項修改", exc)
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

    def changed(self):
        self.revision += 1
        self.dirty = True
        self.render_cancelled = False
        self.play_after_render = False
        self.output_request = None
        self.player.stop()
        self.preview_status.setText("樂譜已修改，等待更新排版。")
        if self.print_preview_active and self.auto_check.isChecked():
            self.autopreview.start()

    def undo(self):
        if not self.history or (self.task and self.task_blocks_edit):
            return
        self.capture_fields()
        self.future.append(self.snapshot())
        self.project, rows, current = self.history.pop()
        self.changed()
        self.sync(selection=rows, current=current)

    def redo(self):
        if not self.future or (self.task and self.task_blocks_edit):
            return
        self.history.append(self.snapshot())
        self.project, rows, current = self.future.pop()
        self.changed()
        self.sync(selection=rows, current=current)

    def sync(self, *, selection=None, current=None):
        if selection is None:
            selection, current = self.notes.selected_rows(), self.notes.currentRow()
        self.loading = True
        try:
            score = self.project.score
            for field, value in ((self.title_field, score.title), (self.composer_field, score.composer), (self.lyrics, score.lyrics)):
                if field.text() != value:
                    field.setText(value)
            self.key_field.setCurrentText(score.key)
            self.meter_field.setCurrentText(score.meter)
            self.tempo_field.setValue(score.tempo)
            self.staff.setChecked(score.with_staff)
            if self.source.toPlainText() != self.project.source:
                cursor = self.source.textCursor()
                position = cursor.position()
                self.source.setPlainText(self.project.source)
                cursor = self.source.textCursor()
                cursor.setPosition(min(position, len(self.project.source)))
                self.source.setTextCursor(cursor)
            self.editor_stack.setCurrentIndex(0 if self.project.mode == "visual" else 1)
            self.mode_button.setText("進階語法" if self.project.mode == "visual" else "返回視覺草稿")
            self.notes.set_score(score)
            self.notes.select_rows(selection, current)
            error = score.rhythm_error() or score.notation_error()
            bars = max(1, (sum(n.ticks for n in score.notes) + int(score.bar_length*16)-1) // int(score.bar_length*16))
            self.bar_jump.setMaximum(bars)
            self.rhythm_label.setText(error or f"{len(score.notes)} 個音符 · {bars} 小節 · 拍數完整")
            self.problem_button.setVisible(bool(error))
            title = score.title or "未命名樂曲"
            self.setWindowTitle(f"{'● ' if self.dirty else ''}{title} — 簡譜工房")
            for control in (self.undo_action, self.undo_button):
                control.setEnabled(bool(self.history))
            for control in (self.redo_action, self.redo_button):
                control.setEnabled(bool(self.future))
        finally:
            self.loading = False
        self.update_selection()

    def capture_fields(self, *_):
        if self.loading or (self.task and self.task_blocks_edit):
            return
        values = dict(title=self.title_field.text(), composer=self.composer_field.text(),
                      key=self.key_field.currentText(), meter=self.meter_field.currentText(),
                      tempo=self.tempo_field.value(), lyrics=self.lyrics.text(), with_staff=self.staff.isChecked())
        if all(getattr(self.project.score, name) == value for name, value in values.items()):
            return
        def update(project):
            for name, value in values.items():
                setattr(project.score, name, value)
        self.mutate(update)

    def source_changed(self):
        if self.loading:
            return
        source = self.source.toPlainText()
        if len(source.encode("utf-8")) > MAX_FILE_BYTES:
            self.error("原始碼太大", "原始碼上限為 4 MB。")
            self.sync()
            return
        self.mutate(lambda p: setattr(p, "source", source))

    def select_note(self, *_):
        self.update_selection()

    def update_selection(self, *_):
        if self.loading:
            return
        if not hasattr(self, "next_note"):
            self.next_note = Note()
        # During construction the mode controls precede the inspector.
        if not hasattr(self, "degree"):
            return
        rows = self.notes.selected_rows()
        mode = self.input_mode.currentData()
        editing = mode == "edit"
        self.notes.insertion_mode = mode if mode in ("edit", "fill") else self.insert_position.currentData()
        self.insert_position.setEnabled(mode == "insert")
        values = [self.project.score.notes[r] for r in rows] if editing else [self.next_note]
        self.inspector_loading = True
        try:
            for name in ("degree", "duration", "octave", "accidental"):
                widget = getattr(self, name)
                distinct = {getattr(note, name) for note in values}
                widget.setCurrentIndex(widget.findData(next(iter(distinct))) if len(distinct) == 1 else -1)
                widget.setEnabled(not editing or bool(rows))
            dotted = {note.dotted for note in values}
            self.dotted.setTristate(len(dotted) != 1)
            self.dotted.setCheckState(Qt.CheckState.PartiallyChecked if len(dotted) != 1 else Qt.CheckState.Checked if True in dotted else Qt.CheckState.Unchecked)
            self.dotted.setEnabled(not editing or bool(rows))
        finally:
            self.inspector_loading = False
        for button in self.selection_buttons:
            button.setEnabled(bool(rows))
        if editing:
            text = f"修改所選 {len(rows)} 音符 · 只改動您調整的屬性；「不同」表示混合值" if rows else "修改模式 · 請先選取音符（Shift 連選、Ctrl 加選）"
        elif mode == "fill":
            text = f"填寫小節 · 下一音符 {self.next_note.beats} 拍 · 點選休止符 0 後輸入數字，保留後續小節位置"
        else:
            position = {"after": "所選之後", "before": "所選之前", "end": "曲尾"}[self.insert_position.currentData()]
            note = self.next_note
            pitch = ("休止 0" if note.degree == 0 else {"": "", "#": "♯", "b": "♭"}[note.accidental] + str(note.degree))
            octave = {-2: "低兩八度", -1: "低八度", 0: "", 1: "高八度", 2: "高兩八度"}[note.octave]
            text = f"新增模式 · 下一個：{pitch}{'（'+octave+'）' if octave else ''} · {note.beats} 拍 · 插在{position if rows else '曲尾'}"
        self.selection_label.setText(text)
        if hasattr(self, "measure_selection"):
            bars = self.selected_measures()
            names = "、".join(str(n + 1) for n in bars[:4]) + ("…" if len(bars) > 4 else "")
            self.measure_selection.setText(f"第 {names} 小節" if bars else "點小節號選取整小節")
            visual = self.project.mode == "visual"
            for control in self.measure_controls:
                control.setEnabled(visual and (control is self.add_bar_button or bool(bars)))
        self.notes.viewport().update()

    @staticmethod
    def replace_note(note, changes):
        changes = dict(changes)
        if changes.get("degree", note.degree) == 0:
            changes.update(octave=0, accidental="", finger=-1, bow="", slide="", slur_start=False, slur_end=False, tie=False)
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
            def update(project):
                for row in rows:
                    project.score.notes[row] = self.replace_note(project.score.notes[row], {name: value})
            self.mutate(update)

    def insertion_index(self):
        rows = self.notes.selected_rows()
        mode = self.insert_position.currentData()
        if not rows or mode == "end":
            return len(self.project.score.notes)
        return min(rows) if mode == "before" else max(rows) + 1

    def insert_notes(self, notes):
        if not notes:
            return
        if len(self.project.score.notes) + len(notes) > 10000:
            self.error("無法新增", "每份樂譜最多支援 10,000 個音符。")
            return
        index = self.insertion_index()
        previous_rows, previous_current = self.notes.selected_rows(), self.notes.currentRow()
        def insert(project):
            project.score.notes[index:index] = notes
        before = self.insert_position.currentData() == "before"
        selection = [r + len(notes) for r in previous_rows] if before else range(index, index + len(notes))
        current = previous_current + len(notes) if before and previous_current >= 0 else -1 if before else index + len(notes)-1
        if self.mutate(insert, selection=selection, current=current):
            self.notes.scrollTo(self.notes.currentIndex())

    def input_pitch(self, degree):
        if self.input_mode.currentData() == "edit":
            self.edit_property("degree", degree)
        elif self.input_mode.currentData() == "fill":
            self.fill_rest(self.replace_note(self.next_note, {"degree": degree}))
        else:
            self.insert_notes([self.replace_note(self.next_note, {"degree": degree})])
        self.notes.setFocus()

    def delete_note(self):
        rows = self.notes.selected_rows()
        if not rows:
            return
        next_row = min(rows[0], len(self.project.score.notes)-len(rows)-1)
        def delete(project):
            selected = set(rows)
            project.score.notes = [n for i, n in enumerate(project.score.notes) if i not in selected]
        self.mutate(delete, selection=[next_row] if next_row >= 0 else [], current=next_row)

    def copy_notes(self):
        notes = [self.project.score.notes[r] for r in self.notes.selected_rows()]
        if not notes:
            return False
        mime = QMimeData()
        mime.setData(NOTE_MIME, json.dumps([asdict(n) for n in notes]).encode("utf-8"))
        mime.setText(" ".join(n.notation_token() for n in notes))
        QApplication.clipboard().setMimeData(mime)
        return True

    def paste_notes(self):
        mime = QApplication.clipboard().mimeData()
        if not mime or not mime.hasFormat(NOTE_MIME):
            self.statusBar().showMessage("請先複製本程式的音符；簡譜文字可貼入進階語法。", 5000)
            return
        try:
            payload = bytes(mime.data(NOTE_MIME))
            if len(payload) > 8*1024*1024:
                raise ValueError("剪貼簿音符資料太大。")
            raw = json.loads(payload)
            if not isinstance(raw, list) or len(raw) > 10000:
                raise ValueError("剪貼簿音符格式不正確。")
            notes = [Note(**item) for item in raw]
            for note in notes:
                note.validate()
        except (TypeError, ValueError) as exc:
            self.error("無法貼上音符", exc)
            return
        self.insert_notes(notes)

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
            self.insert_notes([self.project.score.notes[r] for r in self.notes.selected_rows()])
        elif command == "clear":
            self.notes.select_rows([])
        elif command in ("previous", "next"):
            self.bar_jump.setValue(max(1, self.current_bar() + (-1 if command == "previous" else 1)))
            self.jump_bar()
        elif command.startswith("duration_"):
            self.edit_property("duration", int(command.split("_")[1]))
        elif command in ("raise", "lower", "shorter", "longer", "dot"):
            def change(note):
                if command in ("raise", "lower"):
                    return self.replace_note(note, {"octave": max(-2, min(2, note.octave + (1 if command == "raise" else -1)))})
                if command == "dot":
                    return replace(note, dotted=not note.dotted)
                values = list(DURATIONS)
                index = values.index(note.duration) + (1 if command == "shorter" else -1)
                return replace(note, duration=values[max(0, min(len(values)-1, index))])
            if self.input_mode.currentData() != "edit":
                self.next_note = change(self.next_note)
                self.update_selection()
            else:
                rows = self.notes.selected_rows()
                def update(project):
                    for row in rows:
                        project.score.notes[row] = change(project.score.notes[row])
                self.mutate(update)

    def note_menu(self, position):
        menu = QMenu(self)
        bar = self.notes.measure_at(position)
        if bar is not None and bar not in self.selected_measures():
            self.notes.select_bar(bar)
        self.add_measure_menu(menu)
        menu.addSeparator()
        for title, command in (("剪下　Ctrl+X", "cut"), ("複製　Ctrl+C", "copy"), ("貼上　Ctrl+V", "paste"), ("重複　Ctrl+D", "duplicate"), ("刪除　Delete", "delete")):
            item = menu.addAction(title, lambda c=command: self.note_command(c))
            item.setEnabled(command == "paste" or bool(self.notes.selected_rows()))
        menu.addSeparator()
        menu.addAction("二胡 / 記號…", self.notation_settings)
        menu.exec(self.notes.viewport().mapToGlobal(position))

    def selected_measures(self):
        positions = self.notes.score_model.positions
        ticks = self.notes.score_model.bar_ticks
        return sorted({positions[row] // ticks for row in self.notes.selected_rows() if row < len(positions)})

    def measure_ready(self):
        if self.project.mode != "visual":
            self.statusBar().showMessage("小節工具用於可編輯譜面；進階語法請直接編輯原始碼。", 6000)
            return False
        return not (self.loading or (self.task and self.task_blocks_edit))

    def apply_measure_edit(self, result, *, fill=False):
        selection, current = result.selection, result.current
        if fill and selection:
            selection, current = (selection[0],), selection[0]
        changed = self.mutate(lambda project: setattr(project.score, "notes", result.notes), selection=selection, current=current)
        if not changed:
            if result.notes != self.project.score.notes:
                return False
            self.notes.select_rows(selection, current)
        if fill:
            self.input_mode.setCurrentIndex(self.input_mode.findData("fill"))
        self.measure_notice.setText(result.notice)
        self.measure_notice.setVisible(bool(result.notice))
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
                raise ValueError("請選擇小節前、後或曲尾。")
            at_bar = len(ranges) if position == "end" or not bars else bars[0] if position == "before" else bars[-1] + 1
            result = insert_measures(self.project.score, at_bar, count)
            self.apply_measure_edit(result, fill=True)
        except ValueError as exc:
            self.error("無法新增小節", exc)

    def measure_action(self, action):
        if not self.measure_ready():
            return
        bars = self.selected_measures()
        if not bars:
            self.statusBar().showMessage("請先點小節號，或選取要處理的小節中的音符。", 6000)
            return
        from .measures import clear_measures, delete_measures, duplicate_measures
        operations = {"clear": clear_measures, "delete": delete_measures, "duplicate": duplicate_measures}
        try:
            self.capture_fields()
            if action not in operations:
                raise ValueError("不支援的小節操作。")
            self.apply_measure_edit(operations[action](self.project.score, bars), fill=action == "clear")
        except ValueError as exc:
            self.error("無法修改小節", exc)

    def fill_rest(self, note):
        if not self.measure_ready():
            return
        from .measures import replace_rest_input
        rows = self.notes.selected_rows()
        if not rows:
            self.statusBar().showMessage("請先點選要填寫的休止符 0，或按「＋新增小節」。", 6000)
            return
        row = self.notes.currentRow() if self.notes.currentRow() in rows else rows[0]
        try:
            result = replace_rest_input(self.project.score, row, note)
            if self.apply_measure_edit(result):
                # Advance only to an immediately following rest. Never skip
                # existing melody or append time beyond the allocated measures.
                following = result.current + 1
                if following < len(result.notes) and result.notes[following].degree == 0:
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
        dialog.setWindowTitle("新增小節")
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        count = QSpinBox()
        count.setRange(1, 256)
        count.setObjectName("measureCount")
        position = QComboBox()
        position.setObjectName("measurePosition")
        for text, value in (("所選小節之後", "after"), ("所選小節之前", "before"), ("曲尾", "end")):
            position.addItem(text, value)
        form.addRow("小節數", count)
        form.addRow("插入位置", position)
        layout.addLayout(form)
        hint = QLabel("新小節先填入足拍休止符；可用「填寫小節」直接輸入旋律。沒有選取時加到曲尾。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.dialog_buttons(dialog, layout)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.insert_bars(position.currentData(), count.value())

    def add_measure_menu(self, menu):
        ready = self.project.mode == "visual"
        for text, fn in (("前插一小節　Insert", lambda: self.insert_bars("before")),
                         ("後插一小節", lambda: self.insert_bars("after")),
                         ("曲尾加一小節　Ctrl+B", lambda: self.insert_bars("end")),
                         ("新增多個小節…　Ctrl+Insert", self.insert_bars_dialog)):
            menu.addAction(text, fn).setEnabled(ready)
        menu.addSeparator()
        for text, command in (("重複所選小節", "duplicate"), ("清空小節（保留拍數）", "clear"), ("刪除小節（後段前移）　Ctrl+Delete", "delete")):
            menu.addAction(text, lambda a=command: self.measure_action(a)).setEnabled(ready and bool(self.selected_measures()))

    def fill_bar(self):
        if self.project.score.notes:
            self.mutate(lambda p: p.score.fill_last_bar())

    def current_bar(self):
        row = self.notes.currentRow()
        positions = self.notes.score_model.positions
        return positions[row] // self.notes.score_model.bar_ticks + 1 if 0 <= row < len(positions) else 1

    def jump_bar(self):
        bar = self.bar_jump.value()-1
        for number, rows in self.notes.score_model.bars:
            if number >= bar:
                self.notes.select_rows([rows[0]])
                self.notes.scrollTo(self.notes.currentIndex())
                self.notes.setFocus()
                return

    def goto_problem(self):
        score = self.project.score
        message = score.rhythm_error() or score.notation_error() or ""
        match = re.search(r"第\s*(\d+)\s*個音符", message)
        row = int(match[1])-1 if match else len(score.notes)-1
        if row >= 0:
            self.notes.select_rows([row])
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
        self.metadata_button.setEnabled(True)
        self.autopreview.stop()
        self.workspace_view.blockSignals(True)
        self.workspace_view.setCurrentIndex(index)
        self.workspace_view.blockSignals(False)
        photo = self.workspace_view.currentData() == "photo"
        self.editor_container.show()
        self.preview_panel.setVisible(photo)
        self.preview_tabs.setCurrentIndex(1)
        self.preview_heading.setText("原圖對照")
        self.auto_check.hide()
        self.preview_button.hide()
        self.preview_status.hide()
        if photo:
            self.split.setSizes([800, 550])

    def show_print_preview(self):
        self.print_preview_active = True
        self.metadata_button.setEnabled(False)
        self.editor_container.hide()
        self.preview_panel.show()
        self.preview_tabs.setCurrentIndex(0)
        self.preview_heading.setText("列印預覽")
        self.auto_check.show()
        self.preview_button.show()
        self.preview_status.show()
        self.capture_fields()
        if not self.result or self.rendered_revision != self.revision or not self.result.pdf.is_file():
            self.render_score(True)

    def show_work_log(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("作業訊息")
        dialog.resize(720, 460)
        layout = QVBoxLayout(dialog)
        text = QPlainTextEdit(self.log.toPlainText() or "目前沒有排版或辨識紀錄。")
        text.setReadOnly(True)
        layout.addWidget(text)
        copy = QPushButton("複製完整紀錄")
        copy.clicked.connect(lambda: QApplication.clipboard().setText(text.toPlainText()))
        layout.addWidget(copy)
        self.dialog_buttons(dialog, layout)
        dialog.exec()

    def copy_log(self):
        QApplication.clipboard().setText(self.log.toPlainText())

    def zoom_photo(self, factor):
        if self.photo_pixmap is None or self.photo_pixmap.isNull():
            return
        max_scale = min(5, (40_000_000/(self.photo_pixmap.width()*self.photo_pixmap.height()))**.5)
        self.photo_scale = max(.1, min(max_scale, (max(100, self.photo_scroll.viewport().width()-16) / self.photo_pixmap.width()) if not factor else self.photo_scale*factor))
        pixmap = self.photo_pixmap.scaled(self.photo_pixmap.size()*self.photo_scale, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self.photo_label.setPixmap(pixmap)
        self.photo_label.resize(pixmap.size())

    def layout_settings(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("紙張與排版")
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
        bars.setSpecialValueText("自動")
        bars.setValue(self.project.score.bars_per_line)
        for title, control in (("紙張", paper), ("譜字大小", size), ("每行小節數", bars)):
            form.addRow(title, control)
        layout.addLayout(form)
        note = QLabel("紙張與譜字大小用於正式 PDF；每行小節數用於視覺草稿轉出的簡譜。")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.dialog_buttons(dialog, layout)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            def update(project):
                project.score.paper_size = paper.currentData()
                project.score.staff_size = size.value()
                project.score.bars_per_line = bars.value()
            self.mutate(update)

    @staticmethod
    def dialog_buttons(dialog, layout):
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

    def notation_settings(self):
        rows = self.notes.selected_rows()
        editing = self.input_mode.currentData() == "edit"
        if editing and not rows:
            self.statusBar().showMessage("請先選取要標記的音符。", 5000)
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("二胡 / 演奏記號")
        layout = QVBoxLayout(dialog)
        hint = QLabel(f"套用到所選 {len(rows)} 個音符；保持原樣會保留各音符差異。" if editing else "設定下一個新增音符的記號。範圍連線請切到修改模式後選取音符。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        form, controls = QFormLayout(), {}
        options = {
            "finger": ("指法", [("無", -1), ("空弦 0", 0)] + [(f"{i} 指", i) for i in range(1, 5)]),
            "bow": ("弓法", [("無", ""), ("推弓 ∨", "upbow"), ("拉弓 Π", "downbow")]),
            "dynamic": ("力度", [("無", "")] + [(s, s) for s in ("pp", "p", "mp", "mf", "f", "ff", "sfz")]),
            "articulation": ("演奏法", [("無", ""), ("跳音", "staccato"), ("保持音", "tenuto"), ("重音", "accent"), ("延長", "fermata"), ("顫音", "trill"), ("波音", "mordent")]),
            "slide": ("滑音", [("無", ""), ("上滑音", "up"), ("下滑音", "down")]),
        }
        for name, (title, choices) in options.items():
            control = QComboBox()
            control.addItem("保持原樣", None)
            for text, value in choices:
                control.addItem(text, value)
            if not editing:
                control.setCurrentIndex(control.findData(getattr(self.next_note, name)))
            form.addRow(title, control)
            controls[name] = control
        range_controls = {}
        if editing:
            for name, title in (("slur", "連弓線"), ("tie", "延音線"), ("repeat", "反覆段落")):
                control = QComboBox()
                for text, value in (("保持原樣", "keep"), ("加入所選範圍", "add"), ("移除所選記號", "remove")):
                    control.addItem(text, value)
                form.addRow(title, control)
                range_controls[name] = control
        layout.addLayout(form)
        self.dialog_buttons(dialog, layout)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        changes = {name: control.currentData() for name, control in controls.items() if control.currentData() is not None}
        if not editing:
            self.next_note = self.replace_note(self.next_note, changes)
            self.update_selection()
            return
        ranges = {name: control.currentData() for name, control in range_controls.items()}
        def update(project):
            for row in rows:
                project.score.notes[row] = self.replace_note(project.score.notes[row], changes)
            for name, action in ranges.items():
                if action == "keep":
                    continue
                if action == "add" and (rows != list(range(rows[0], rows[-1]+1)) or (name != "repeat" and len(rows) < 2)):
                    raise ValueError("連弓與延音需選取至少兩個連續音符；反覆需選取連續的完整小節。")
                for row in rows:
                    marks = {"slur_start": False, "slur_end": False} if name == "slur" else {"tie": False} if name == "tie" else {"repeat_start": False, "repeat_end": False}
                    if action == "add":
                        if name == "slur":
                            marks = {"slur_start": row == rows[0], "slur_end": row == rows[-1]}
                        elif name == "tie":
                            marks = {"tie": row != rows[-1]}
                        else:
                            marks = {"repeat_start": row == rows[0], "repeat_end": row == rows[-1]}
                    project.score.notes[row] = replace(project.score.notes[row], **marks)
            if any(action == "add" for action in ranges.values()):
                if error := project.score.notation_error():
                    raise ValueError(error)
        self.mutate(update)

    def source_snippets(self):
        menu = QMenu(self)
        snippets = (("三連音（3 個八分音符）", "3[ q1 q2 q3 ] "),
                    ("前倚音", "g[23] c1 "), ("後倚音", "c1 [23]g "),
                    ("弱起拍號（取代原拍號）", "4/4,4 "),
                    ("反覆段落", "R{ c1 c2 c3 c4 } "),
                    ("連弓線", "c1 ( c2 c3 c4 ) "),
                    ("換行", "\\break\n"), ("另一聲部（需補齊同長度）", "\nNextPart\n1=C 4/4\nc1 c2 c3 c4\n"))
        for title, snippet in snippets:
            menu.addAction(title, lambda s=snippet: self.source.insertPlainText(s))
        self.statusBar().showMessage("進階語法會插入游標處；弱起拍號須取代原拍號，請自行核對小節拍數。", 8000)
        menu.exec(self.source.mapToGlobal(self.source.rect().topLeft()))
