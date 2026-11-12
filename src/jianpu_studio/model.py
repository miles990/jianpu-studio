"""Editable single-voice score. Fractions avoid rhythm rounding errors."""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field, replace
from fractions import Fraction
from pathlib import Path

KEYS = ("C", "G", "D", "A", "E", "B", "F#", "F", "Bb", "Eb", "Ab", "Db", "Gb")
METERS = ("4/4", "3/4", "2/4", "6/8", "3/8", "2/2", "9/8", "12/8")
DURATIONS = {1: "全音符 · 4 拍", 2: "二分 · 2 拍", 4: "四分 · 1 拍", 8: "八分 · ½ 拍", 16: "十六分 · ¼ 拍", 32: "三十二分 · ⅛ 拍"}
MAX_FILE_BYTES = 4 * 1024 * 1024
TICKS_PER_QUARTER = 48
GRACE_PATTERN = r"(?:[#b]?[1-7](?:'{1,2}|,{1,2})?)"


def _grace_source(value: str) -> str:
    """Translate the UI's octave suffixes to unambiguous upstream prefixes.

    Upstream's suffix normalization can move the next note's accidental to
    the previous pitch (e.g. 1#2'). Prefixes with OctavesBefore avoid that path.
    """
    return "".join(accidental + octave + degree for accidental, degree, octave
                   in re.findall(r"([#b]?)([1-7])('{1,2}|,{1,2})?", value))


def text_value(value: object, name: str, limit: int = 200) -> str:
    if (not isinstance(value, str) or len(value) > limit
            or any(ord(c) < 32 or c in "\x85\u2028\u2029" for c in value)):
        raise ValueError(f"{name}須為單行文字，最多 {limit} 字。")
    return value


@dataclass(frozen=True)
class Note:
    degree: int = 1
    octave: int = 0
    duration: int = 4
    dotted: bool = False
    accidental: str = ""
    finger: int = -1
    bow: str = ""
    dynamic: str = ""
    articulation: str = ""
    slide: str = ""
    slur_start: bool = False
    slur_end: bool = False
    tie: bool = False
    repeat_start: bool = False
    repeat_end: bool = False
    tuplet: int = 0
    tuplet_start: bool = False
    tuplet_end: bool = False
    hairpin: str = ""
    hairpin_end: bool = False
    grace_before: str = ""
    grace_after: str = ""

    def validate(self) -> None:
        if type(self.degree) is not int or not 0 <= self.degree <= 7:
            raise ValueError("音高必須是 0（休止）到 7。")
        if type(self.octave) is not int or self.octave not in range(-2, 3):
            raise ValueError("八度範圍為低兩個到高兩個八度。")
        if type(self.duration) is not int or self.duration not in DURATIONS:
            raise ValueError("不支援這個音符時值。")
        if type(self.dotted) is not bool or self.accidental not in ("", "#", "b", "n"):
            raise ValueError("無效的附點或升降記號。")
        if self.degree == 0 and (self.octave or self.accidental):
            raise ValueError("休止符沒有八度或升降記號。")
        if type(self.finger) is not int or self.finger not in range(-1, 5):
            raise ValueError("二胡指法須為空弦 0 到 4 指。")
        if self.bow not in ("", "upbow", "downbow") or self.slide not in ("", "up", "down"):
            raise ValueError("不支援的弓法或滑音。")
        if self.dynamic not in ("", "ppp", "pp", "p", "mp", "mf", "f", "ff", "fff", "sfz"):
            raise ValueError("不支援的力度。")
        if self.articulation not in ("", "staccato", "tenuto", "accent", "fermata", "trill", "mordent",
                                     "breathe", "turn", "marcato", "staccatissimo", "harmonic"):
            raise ValueError("不支援的演奏記號。")
        if any(type(getattr(self, name)) is not bool for name in ("slur_start", "slur_end", "tie", "repeat_start", "repeat_end",
                                                                 "tuplet_start", "tuplet_end", "hairpin_end")):
            raise ValueError("連線或反覆格式不正確。")
        if type(self.tuplet) is not int or self.tuplet not in (0, 3):
            raise ValueError("目前支援三連音，每組三個等時值音符。")
        if self.tuplet and (self.dotted or self.duration not in (4, 8, 16, 32)):
            raise ValueError("三連音支援未附點的四分、八分、十六分或三十二分音符。")
        if not self.tuplet and (self.tuplet_start or self.tuplet_end):
            raise ValueError("三連音起終點須屬於三連音群組。")
        if self.hairpin not in ("", "crescendo", "diminuendo"):
            raise ValueError("不支援的漸強或漸弱記號。")
        for grace in (self.grace_before, self.grace_after):
            if not isinstance(grace, str) or (grace and re.fullmatch(GRACE_PATTERN + "{1,8}", grace) is None):
                raise ValueError("倚音須為 1–8 個音（1–7），可加 #、b 及最多兩個八度後綴，例如 23 或 #4'5。")
        if self.degree == 0 and (self.finger >= 0 or self.bow or self.slide or self.slur_start or self.slur_end or self.tie):
            raise ValueError("休止符不能加指法、弓法、滑音或連線。")
        if self.degree == 0 and (self.grace_before or self.grace_after):
            raise ValueError("休止符不能加倚音。")
        if self.grace_after and (self.finger >= 0 or self.bow or self.dynamic
                                 or self.articulation not in ("", "breathe")
                                 or self.slur_start or self.slur_end or self.tie or self.hairpin or self.hairpin_end):
            # Upstream places these post-events outside afterGrace's main
            # note, or moves a tie onto the last ornament. Refuse that precise
            # combination instead of printing detached marks or wrong MIDI.
            raise ValueError("後倚音目前不能與同一主音的指法、弓法、力度、演奏記號或連線端點並用；"
                             "這個組合尚不支援，請分開設定或保留原譜。換氣記號可並用。")

    @property
    def beats(self) -> Fraction:
        return Fraction(self.ticks, TICKS_PER_QUARTER)

    @property
    def ticks(self) -> int:
        """Exact units for both dotted 32nds and undotted 3:2 tuplets."""
        ticks = (4 * TICKS_PER_QUARTER // self.duration) * (3 if self.dotted else 2) // 2
        return ticks * 2 // 3 if self.tuplet == 3 else ticks

    def token(self) -> str:
        self.validate()
        # Upstream's unaltered degree cancels a previous accidental in the same
        # measure and lets LilyPond print a natural where needed. It has no 'n'
        # token for a redundant cautionary natural on an already natural note.
        accidental = "" if self.accidental == "n" else self.accidental
        pitch = accidental + str(self.degree) + ("'" * self.octave if self.octave >= 0 else "," * -self.octave)
        if self.duration <= 2:
            nominal_beats = Fraction(4, self.duration) * (Fraction(3, 2) if self.dotted else 1)
            return " ".join([pitch] + ["-"] * (int(nominal_beats) - 1))
        return {4: "c", 8: "q", 16: "s", 32: "d"}[self.duration] + pitch + ("." if self.dotted else "")

    def notation_token(self) -> str:
        parts = []
        if self.repeat_start:
            parts.append("R{")
        if self.tuplet_start:
            parts.append("3[")
        if self.grace_before:
            parts.append("g[" + _grace_source(self.grace_before) + "]")
        if self.slide:
            parts.append("slideUp" if self.slide == "up" else "slideDown")
        # A long note is a numbered head followed by quarter-note dashes.
        # Attach expressive marks and slurs to its head, not its last dash.
        head, *continuation = self.token().split()
        parts.append(head)
        if self.finger >= 0:
            parts.append(f"Fr={self.finger}")
        articulation = "flageolet" if self.articulation == "harmonic" else self.articulation
        for mark in (self.bow, self.dynamic, "" if articulation == "breathe" else articulation):
            if mark:
                parts.append("\\" + mark)
        if self.hairpin_end:
            parts.append(r"\!")
        if self.hairpin:
            parts.append(r"\<" if self.hairpin == "crescendo" else r"\>")
        if self.slur_end:
            parts.append(")")
        if self.slur_start:
            parts.append("(")
        parts.extend(continuation)
        if self.grace_after:
            parts.append("[" + _grace_source(self.grace_after) + "]g")
        if self.tie:
            parts.append("~")
        if articulation == "breathe":
            parts.append(r"\breathe")
        if self.tuplet_end:
            parts.append("]")
        if self.repeat_end:
            parts.append("}")
        return " ".join(parts)


def tuplet_ranges(notes: list[Note]) -> list[tuple[int, int]]:
    """Validate 3:2 groups and return zero-based, end-exclusive note ranges.

    This helper checks group structure independently of meter. A caller can
    use the ranges to reject copy/delete/duration edits that split a group.
    """
    ranges, row = [], 0
    while row < len(notes):
        note = notes[row]
        if not note.tuplet:
            if note.tuplet_start or note.tuplet_end:
                raise ValueError(f"第 {row+1} 個音符的三連音端點沒有群組。")
            row += 1
            continue
        group = notes[row:row+3]
        if (len(group) != 3 or not note.tuplet_start
                or any(n.tuplet != 3 or n.dotted or n.duration not in (4, 8, 16, 32) for n in group)
                or len({n.duration for n in group}) != 1
                or any(n.tuplet_start != (i == 0) or n.tuplet_end != (i == 2) for i, n in enumerate(group))):
            raise ValueError(f"第 {row+1} 個音符的三連音不完整；請選取完整的三個等時值、未附點音符。")
        ranges.append((row, row+3))
        row += 3
    return ranges


@dataclass
class Score:
    title: str = "未命名樂曲"
    composer: str = ""
    key: str = "C"
    meter: str = "4/4"
    tempo: int = 96
    lyrics: str = ""
    with_staff: bool = False
    notes: list[Note] = field(default_factory=list)
    paper_size: str = "a4"
    staff_size: int = 20
    bars_per_line: int = 0

    @property
    def bar_length(self) -> Fraction:
        numerator, denominator = self.meter.split("/")
        return Fraction(int(numerator) * 4, int(denominator))

    def validate(self) -> None:
        text_value(self.title, "曲名")
        text_value(self.composer, "作曲者")
        text_value(self.lyrics, "歌詞", 20000)
        if self.key not in KEYS or self.meter not in METERS:
            raise ValueError("不支援此調號或拍號。")
        if type(self.tempo) is not int or not 20 <= self.tempo <= 300:
            raise ValueError("速度須介於每分鐘 20 到 300 拍。")
        if type(self.with_staff) is not bool or not isinstance(self.notes, list) or len(self.notes) > 10000:
            raise ValueError("樂譜格式不正確或音符太多（上限 10,000）。")
        if self.paper_size not in ("a4", "letter") or type(self.staff_size) is not int or not 16 <= self.staff_size <= 30:
            raise ValueError("紙張須為 A4 / Letter，譜字大小須為 16–30。")
        if type(self.bars_per_line) is not int or not 0 <= self.bars_per_line <= 8:
            raise ValueError("每行小節數須為自動（0）到 8。")
        for note in self.notes:
            if not isinstance(note, Note):
                raise ValueError("樂譜音符格式不正確。")
            note.validate()
        tuplet_ranges(self.notes)

    def rhythm_error(self) -> str | None:
        position = 0
        bar_ticks = int(self.bar_length * TICKS_PER_QUARTER)
        for i, note in enumerate(self.notes):
            offset = position % bar_ticks
            if offset + note.ticks > bar_ticks:
                return f"第 {i + 1} 個音符超出第 {position // bar_ticks + 1} 小節。請縮短時值，跨小節長音可拆成音符再加延音線。"
            position += note.ticks
        if self.notes and position % bar_ticks:
            return f"最後一小節還缺 {Fraction(bar_ticks - position % bar_ticks, TICKS_PER_QUARTER)} 拍。按「補齊休止符」即可完成。"
        return None

    def notation_error(self) -> str | None:
        slur = repeat = hairpin = False
        position = 0
        bar_ticks = int(self.bar_length * TICKS_PER_QUARTER)
        try:
            groups = {start: end for start, end in tuplet_ranges(self.notes)}
        except ValueError as exc:
            return str(exc)
        for i, note in enumerate(self.notes):
            if i in groups and position % bar_ticks + sum(n.ticks for n in self.notes[i:groups[i]]) > bar_ticks:
                return f"第 {i+1} 個音符的三連音跨越小節；請將整組移到同一小節內。"
            if note.hairpin_end:
                if not hairpin:
                    return f"第 {i+1} 個音符的漸強或漸弱線缺少起點。"
                hairpin = False
            elif hairpin and note.dynamic:
                return f"第 {i+1} 個音符的力度會中斷漸強或漸弱線；請把力度放在起終點，或拆成兩段。"
            if note.hairpin:
                if hairpin:
                    return f"第 {i+1} 個音符有重疊的漸強或漸弱線，請先結束前一段。"
                hairpin = True
            if note.slur_end:
                if not slur:
                    return f"第 {i+1} 個音符的連弓線缺少起點。"
                slur = False
            if note.slur_start:
                if slur:
                    return f"第 {i+1} 個音符有重疊連弓線，請先移除舊連線。"
                slur = True
            if note.tie and (not note.degree or i+1 == len(self.notes) or
                             (note.degree, note.octave, "" if note.accidental == "n" else note.accidental) !=
                             (self.notes[i+1].degree, self.notes[i+1].octave,
                              "" if self.notes[i+1].accidental == "n" else self.notes[i+1].accidental)):
                return f"第 {i+1} 個音符的延音線須接到下一個相同音高的音符。"
            if note.repeat_start:
                if repeat or position % bar_ticks:
                    return f"第 {i+1} 個音符的反覆起點須在小節開頭，且不能重疊。"
                repeat = True
            position += note.ticks
            if note.repeat_end:
                if not repeat or position % bar_ticks:
                    return f"第 {i+1} 個音符的反覆終點須在完整小節末，且需要起點。"
                repeat = False
        if slur or repeat:
            return "連弓線或反覆段落缺少終點。請選取完整範圍重新設定，或移除記號。"
        if hairpin:
            return "漸強或漸弱線缺少終點；請選取完整範圍重新設定，或移除記號。"
        return None

    def fill_last_bar(self) -> None:
        tuplet_ranges(self.notes)
        remainder = (-sum(n.ticks for n in self.notes)) % int(self.bar_length * TICKS_PER_QUARTER)
        choices = [Note(0, duration=d, dotted=dot) for d in DURATIONS for dot in (True, False)]
        # A greedy decomposition can leave a single unrepresentable tick after
        # dotted 32nds. Find the shortest exact fill before mutating the score.
        fills: list[list[Note] | None] = [[]] + [None] * remainder
        for ticks in range(1, remainder + 1):
            candidates = [fills[ticks - n.ticks] + [n] for n in choices
                          if n.ticks <= ticks and fills[ticks - n.ticks] is not None]
            if candidates:
                fills[ticks] = min(candidates, key=len)
        if fills[remainder] is None:
            raise ValueError("剩餘拍數無法用目前支援的休止符補齊，請調整附點三十二分音符。")
        if len(self.notes) + len(fills[remainder]) > 10000:
            raise ValueError("音符太多（上限 10,000）。")
        self.notes.extend(reversed(fills[remainder]))

    def to_jianpu(self) -> str:
        self.validate()
        if not self.notes:
            raise ValueError("請先加入音符。")
        if error := self.rhythm_error():
            raise ValueError(error)
        if error := self.notation_error():
            raise ValueError(error)
        # These text fields are literal, never raw LilyPond/Scheme.
        def literal(s: str) -> str:
            return s.replace('"', "' ").replace("\\", "/").replace("%", "％").replace("{", "｛").replace("}", "｝")
        lines = [f"title={literal(self.title)}", f"composer={literal(self.composer)}", "NoIndent"]
        if any(note.grace_before or note.grace_after for note in self.notes):
            # Only grace/chord parsing consults this upstream option; ordinary
            # single notes keep their existing octave suffix syntax unchanged.
            lines.append("OctavesBefore")
        if self.with_staff:
            lines.append("WithStaff")
        lines.append(f"1={self.key} {self.meter} 4={self.tempo}")
        bar, position = [], 0
        bar_ticks = int(self.bar_length * TICKS_PER_QUARTER)
        for note in self.notes:
            bar.append(note.notation_token())
            position += note.ticks
            if position % bar_ticks == 0:
                lines.append(" ".join(bar))
                if self.bars_per_line and position // bar_ticks % self.bars_per_line == 0:
                    lines.append("\\break")
                bar = []
        if self.lyrics.strip():
            lines.append("H: " + literal(self.lyrics))
        return "\n".join(lines) + "\n"


@dataclass
class Project:
    score: Score = field(default_factory=Score)
    mode: str = "visual"
    source: str = ""

    def snapshot(self) -> Project:
        # Notes are immutable: history shares notes, never mutable score lists.
        return replace(self, score=replace(self.score, notes=list(self.score.notes)))

    def to_dict(self) -> dict:
        return {"format": "jianpu-studio", "version": 1, **asdict(self)}

    @classmethod
    def from_dict(cls, data: dict) -> Project:
        if (not isinstance(data, dict) or data.get("format") != "jianpu-studio"
                or type(data.get("version")) is not int or data.get("version") != 1):
            raise ValueError("不是支援的簡譜工房專案（需要格式版本 1）。")
        try:
            raw = dict(data["score"])
            if not isinstance(raw.get("notes"), list) or len(raw["notes"]) > 10000:
                raise ValueError("樂譜格式不正確或音符太多（上限 10,000）。")
            raw["notes"] = [Note(**n) for n in raw["notes"]]
            score = Score(**raw)
            score.validate()
            mode, source = data.get("mode", "visual"), data.get("source", "")
            if mode not in ("visual", "source") or not isinstance(source, str) or len(source) > MAX_FILE_BYTES:
                raise ValueError("專案模式或原始碼不正確。")
            return cls(score, mode, source)
        except (KeyError, TypeError) as exc:
            raise ValueError("專案資料不完整或欄位格式錯誤。") from exc

    def notation(self) -> str:
        return self.score.to_jianpu() if self.mode == "visual" else self.source


def atomic_write(path: Path, text: str | bytes) -> None:
    """Replace only after flush succeeds; never truncate the user's old file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="." + path.name, suffix=".tmp", dir=path.parent)
    try:
        options = {} if isinstance(text, bytes) else {"encoding": "utf-8", "newline": "\n"}
        with os.fdopen(fd, "wb" if isinstance(text, bytes) else "w", **options) as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def save_project(path: Path, project: Project) -> None:
    atomic_write(path, json.dumps(project.to_dict(), ensure_ascii=False, indent=2) + "\n")


def load_project(path: Path) -> Project:
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("專案檔太大（上限 4 MB）。")
    return Project.from_dict(json.loads(path.read_text(encoding="utf-8-sig")))


def demo_project() -> Project:
    notes = []
    for line in ("1 1 5 5 6 6 5-", "4 4 3 3 2 2 1-", "5 5 4 4 3 3 2-", "5 5 4 4 3 3 2-", "1 1 5 5 6 6 5-", "4 4 3 3 2 2 1-"):
        notes.extend(Note(int(token[0]), duration=2 if token.endswith("-") else 4) for token in line.split())
    return Project(Score(title="小星星", composer="法國傳統旋律", notes=notes))
