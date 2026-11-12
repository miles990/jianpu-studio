"""Score-first controls for common notation, with atomic range edits."""
from dataclasses import replace

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .model import TICKS_PER_QUARTER, tuplet_ranges


class NotationActions:
    def require_complete_tuplets(self, rows):
        selected = set(rows)
        for start, end in tuplet_ranges(self.project.score.notes):
            members = set(range(start, end))
            if selected & members and not members <= selected:
                self.error("請選取整組三連音", "複製、刪除或重複時，請選取完整的三個音符。音高及演奏記號仍可逐個修改。")
                return False
        return True

    def triplet_action(self, collapse=False):
        if not self.measure_ready():
            return
        rows = self.notes.selected_rows()
        if not rows:
            self.statusBar().showMessage("請先點選要拆成三連音的音符或休止符。", 6000)
            return
        try:
            self.capture_fields()
            notes = self.project.score.notes
            if collapse:
                groups = [(a, b) for a, b in tuplet_ranges(notes) if any(a <= row < b for row in rows)]
                if len(groups) != 1 or any(not groups[0][0] <= row < groups[0][1] for row in rows):
                    raise ValueError("請選取同一組三連音中的音符。合併會保留首音音高及整組總拍數。")
                start, end = groups[0]
                first, middle, last = notes[start:end]
                if first.grace_after:
                    raise ValueError("首音有獨立後倚音，請先移除或移到整組終點，再合併三連音。")
                if (middle.slur_start or middle.slur_end or middle.hairpin or middle.hairpin_end
                        or first.slur_end or first.hairpin_end or last.slur_start or last.hairpin):
                    raise ValueError("三連音內有連線或漸強弱端點，請先移除這些範圍記號再合併。")
                # Do not silently drop a separately marked inner note.
                for note in (middle, last):
                    if (note.finger >= 0 or note.bow or note.dynamic or note.articulation
                            or note.slide or note.grace_before or (note is middle and note.grace_after)):
                        raise ValueError("組內後兩音有獨立演奏記號，請先移除這些記號再合併；合併會保留首音音高。")
                merged = replace(first, duration=first.duration // 2, tuplet=0, tuplet_start=False,
                                 tuplet_end=False, slur_end=last.slur_end, tie=last.tie,
                                 repeat_end=last.repeat_end, hairpin_end=last.hairpin_end,
                                 grace_after=last.grace_after)
                replacement = [merged]
            else:
                if len(rows) != 1:
                    raise ValueError("請只選一個音符或休止符。程式會將它原來的拍數平均分成三份，後面的小節不會移動。")
                start, end = rows[0], rows[0] + 1
                original = notes[start]
                if original.tuplet or original.dotted or original.duration not in (2, 4, 8, 16):
                    raise ValueError("請選未附點的二分、四分、八分或十六分音符／休止符；已是三連音可先合併。")
                replacement = []
                for part in range(3):
                    replacement.append(replace(
                        original, duration=original.duration * 2, tuplet=3,
                        tuplet_start=part == 0, tuplet_end=part == 2,
                        finger=original.finger if part == 0 else -1,
                        bow=original.bow if part == 0 else "",
                        dynamic=original.dynamic if part == 0 else "",
                        articulation=original.articulation if part == 0 else "",
                        slide=original.slide if part == 0 else "",
                        slur_start=original.slur_start and part == 0,
                        slur_end=original.slur_end and part == 2,
                        tie=original.tie and part == 2,
                        repeat_start=original.repeat_start and part == 0,
                        repeat_end=original.repeat_end and part == 2,
                        hairpin=original.hairpin if part == 0 else "",
                        hairpin_end=original.hairpin_end and part == 2,
                        grace_before=original.grace_before if part == 0 else "",
                        grace_after=original.grace_after if part == 2 else "",
                    ))
            if sum(n.ticks for n in replacement) != sum(n.ticks for n in notes[start:end]):
                raise ValueError("三連音的總拍數不一致，未修改原譜。")
            def update(project):
                project.score.notes[start:end] = replacement
                if error := project.score.notation_error():
                    raise ValueError(error)
            if self.mutate(update, selection=range(start, start + len(replacement)), current=start):
                self.input_mode.setCurrentIndex(self.input_mode.findData("edit"))
                self.notes.setFocus()
                self.statusBar().showMessage("已合併為首音，總拍數不變。" if collapse else "已拆成三連音，總拍數不變；點選各音符即可修改音高。", 8000)
        except ValueError as exc:
            self.error("無法修改三連音", exc)

    @staticmethod
    def clear_range_marks(notes, rows, name, *, keep_adjacent=False):
        """Remove a whole intersecting connection, including its far endpoint."""
        selected = set(rows)
        start_field, end_field = {"slur": ("slur_start", "slur_end"),
                                  "repeat": ("repeat_start", "repeat_end"),
                                  "hairpin": ("hairpin", "hairpin_end")}.get(name, (None, None))
        if name == "tie":
            for i, note in enumerate(notes):
                if note.tie and (i in selected or i + 1 in selected):
                    if keep_adjacent and (i + 1 == rows[0] or i == rows[-1]):
                        continue
                    notes[i] = replace(note, tie=False)
            return
        spans, start = [], None
        for i, note in enumerate(notes):
            # Slurs/hairpins can stop and start again on the same note.
            if name != "repeat" and getattr(note, end_field):
                spans.append((start if start is not None else i, i))
                start = None
            if getattr(note, start_field):
                if start is not None:
                    spans.append((start, start))
                start = i
            if name == "repeat" and getattr(note, end_field):
                spans.append((start if start is not None else i, i))
                start = None
        if start is not None:
            spans.append((start, start))
        for a, b in spans:
            if keep_adjacent and ((b == rows[0] and a < b) or (a == rows[-1] and b > a)):
                continue
            if any(a <= i <= b for i in selected):
                notes[a] = replace(notes[a], **{start_field: "" if name == "hairpin" else False})
                notes[b] = replace(notes[b], **{end_field: False})

    def apply_notation_changes(self, changes, ranges=None):
        ranges = ranges or {}
        if self.project.mode != "visual":
            return False
        if self.input_mode.currentData() != "edit":
            try:
                note = self.replace_note(self.next_note, changes)
                note.validate()
                self.next_note = note
                self.update_selection()
                return True
            except ValueError as exc:
                self.error("無法設定記號", exc)
                return False
        rows = self.notes.selected_rows()
        if not rows:
            self.statusBar().showMessage("請先選取要標記的音符。", 5000)
            return False
        def update(project):
            for row in rows:
                project.score.notes[row] = self.replace_note(project.score.notes[row], changes)
            for name, action in ranges.items():
                if action == "keep":
                    continue
                if action != "remove" and (rows != list(range(rows[0], rows[-1] + 1))
                                           or (name != "repeat" and len(rows) < 2)):
                    raise ValueError("連音、延音與漸強弱需選至少兩個連續音符；反覆需選完整的連續小節。")
                self.clear_range_marks(project.score.notes, rows, name, keep_adjacent=action != "remove")
                if action == "remove":
                    continue
                if name == "tie":
                    for row in rows[:-1]:
                        project.score.notes[row] = replace(project.score.notes[row], tie=True)
                else:
                    start_field, end_field = {"slur": ("slur_start", "slur_end"),
                                              "repeat": ("repeat_start", "repeat_end"),
                                              "hairpin": ("hairpin", "hairpin_end")}[name]
                    project.score.notes[rows[0]] = replace(project.score.notes[rows[0]], **{start_field: action if name == "hairpin" else True})
                    project.score.notes[rows[-1]] = replace(project.score.notes[rows[-1]], **{end_field: True})
            if any(action not in ("keep", "remove") for action in ranges.values()):
                if error := project.score.notation_error():
                    raise ValueError(error)
        return self.mutate(update)

    def quick_notation(self, changes=None, ranges=None):
        if not self.notes.selected_rows():
            self.statusBar().showMessage("請先選取要加記號的音符；連線與漸強弱需連續選取兩個以上。", 6000)
            return
        self.input_mode.setCurrentIndex(self.input_mode.findData("edit"))
        self.apply_notation_changes(changes or {}, ranges)

    def notation_menu(self):
        menu = QMenu(self)
        for title, fn in (
            ("拆成三連音（總拍數不變）", self.triplet_action),
            ("合併三連音為首音", lambda: self.triplet_action(True)),
            ("連音線／連弓線", lambda: self.quick_notation(ranges={"slur": "add"})),
            ("延音線（相同音高）", lambda: self.quick_notation(ranges={"tie": "add"})),
            ("延長記號 𝄐", lambda: self.quick_notation({"articulation": "fermata"})),
            ("漸強 ＜", lambda: self.quick_notation(ranges={"hairpin": "crescendo"})),
            ("漸弱 ＞", lambda: self.quick_notation(ranges={"hairpin": "diminuendo"})),
        ):
            menu.addAction(title, fn)
        menu.addSeparator()
        for index, title in enumerate(("力度 / 演奏…", "連線 / 反覆…", "倚音 / 二胡…")):
            menu.addAction(title, lambda checked=False, tab=index: self.notation_settings(tab))
        return menu

    def notation_settings(self, tab_index=0):
        if self.project.mode != "visual":
            return
        rows = self.notes.selected_rows()
        editing = self.input_mode.currentData() == "edit"
        if editing and not rows:
            self.statusBar().showMessage("請先選取要標記的音符。", 5000)
            return
        dialog = QDialog(self)
        dialog.setObjectName("notation_dialog")
        dialog.setWindowTitle("基本記號 / 二胡")
        dialog.setMinimumWidth(470)
        layout = QVBoxLayout(dialog)
        hint = QLabel(f"套用到所選 {len(rows)} 個音符；保持原樣會保留各音符差異。" if editing else "設定下一個新增音符的記號。連線請切到修改模式後選取音符。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        tabs = QTabWidget()
        tabs.setObjectName("notation_tabs")
        layout.addWidget(tabs)
        forms = []
        for title in ("力度／演奏", "連線／反覆", "倚音／二胡"):
            page = QWidget()
            form = QFormLayout(page)
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            tabs.addTab(page, title)
            forms.append(form)
        tabs.setCurrentIndex(tab_index if tab_index in (0, 1, 2) else 0)
        controls = {}
        options = {
            "dynamic": (0, "力度", [("無", "")] + [(s, s) for s in ("ppp", "pp", "p", "mp", "mf", "f", "ff", "fff", "sfz")]),
            "articulation": (0, "演奏記號", [("無", ""), ("跳音 ·", "staccato"), ("保持音 —", "tenuto"), ("重音 >", "accent"),
                ("延長記號 𝄐", "fermata"), ("顫音 tr", "trill"), ("波音", "mordent"), ("換氣", "breathe"),
                ("迴音", "turn"), ("強重音 ∧", "marcato"), ("極短奏", "staccatissimo"), ("泛音 ○", "harmonic")]),
            "finger": (2, "指法", [("無", -1), ("空弦 0", 0)] + [(f"{i} 指", i) for i in range(1, 5)]),
            "bow": (2, "弓法", [("無", ""), ("推弓 ∨", "upbow"), ("拉弓 Π", "downbow")]),
            "slide": (2, "滑音", [("無", ""), ("上滑音", "up"), ("下滑音", "down")]),
        }
        for name, (tab, title, choices) in options.items():
            control = QComboBox()
            control.setObjectName("notation_" + name)
            control.addItem("保持原樣", None)
            for text, value in choices:
                control.addItem(text, value)
            if not editing:
                control.setCurrentIndex(control.findData(getattr(self.next_note, name)))
            forms[tab].addRow(title, control)
            controls[name] = control
        explanation = QLabel("延長記號表示自由延長；同音延音線合併拍數；三連音則把原拍數平均分成三份。\n三連音請在譜面選一音，再用音符工具的「三連音」操作。")
        explanation.setWordWrap(True)
        forms[0].addRow(explanation)
        range_controls = {}
        for name, title in (("slur", "連音／連弓線"), ("tie", "同音延音線"), ("hairpin", "漸強／漸弱"), ("repeat", "反覆段落")):
            control = QComboBox()
            control.setObjectName("notation_" + name)
            choices = [("保持原樣", "keep")]
            choices += [("漸強 ＜", "crescendo"), ("漸弱 ＞", "diminuendo")] if name == "hairpin" else [("加入所選範圍", "add")]
            choices.append(("移除相交的整條記號", "remove"))
            for text, value in choices:
                control.addItem(text, value)
            control.setEnabled(editing)
            forms[1].addRow(title, control)
            range_controls[name] = control
        range_hint = QLabel("Shift 連續選取。延音線兩端音高須相同；反覆需完整小節。移除會同時清除該條記號的起點及終點，可復原。")
        range_hint.setWordWrap(True)
        forms[1].addRow(range_hint)
        grace_controls = {}
        for name, title in (("grace_before", "前倚音"), ("grace_after", "後倚音")):
            control = QLineEdit()
            control.setObjectName("notation_" + name)
            control.setPlaceholderText("留空＝保持原樣；輸入 - 移除；例：23 或 #4'5")
            if not editing:
                control.setText(getattr(self.next_note, name))
            control.setMaxLength(40)
            forms[2].addRow(title, control)
            grace_controls[name] = control
        grace_hint = QLabel("倚音可填 1–8 個 1–7 音高；# 升、b 降、' 高八度、, 低八度（最多兩八度）。倚音不另外增加小節拍數。\n\n後倚音目前不能與同一主音的力度、指法／弓法、連線端點或演奏記號（換氣除外）並用；程式會提示並保留原譜。")
        grace_hint.setWordWrap(True)
        forms[2].addRow(grace_hint)
        self.dialog_buttons(dialog, layout)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        changes = {name: c.currentData() for name, c in controls.items() if c.currentData() is not None}
        for name, control in grace_controls.items():
            value = control.text().strip()
            if value or not editing:
                changes[name] = "" if value == "-" else value
        ranges = {name: c.currentData() for name, c in range_controls.items()} if editing else {}
        self.apply_notation_changes(changes, ranges)

    @staticmethod
    def validate_tuplet_edit(score):
        groups = tuplet_ranges(score.notes)
        if not groups:
            return
        starts = {start: end for start, end in groups}
        position = 0
        bar_ticks = int(score.bar_length * TICKS_PER_QUARTER)
        for row, note in enumerate(score.notes):
            if row in starts and position % bar_ticks + sum(n.ticks for n in score.notes[row:starts[row]]) > bar_ticks:
                raise ValueError("修改會讓三連音跨越小節。請改用整小節操作，或先合併三連音再調整。")
            position += note.ticks
