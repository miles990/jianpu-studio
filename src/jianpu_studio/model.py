"""Editable single-voice score. Fractions avoid rhythm rounding errors."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field, replace
from fractions import Fraction
from pathlib import Path

KEYS = ("C", "G", "D", "A", "E", "B", "F#", "F", "Bb", "Eb", "Ab", "Db", "Gb")
METERS = ("4/4", "3/4", "2/4", "6/8", "3/8", "2/2", "9/8", "12/8")
DURATIONS = {1: "全音符 · 4 拍", 2: "二分 · 2 拍", 4: "四分 · 1 拍", 8: "八分 · ½ 拍", 16: "十六分 · ¼ 拍", 32: "三十二分 · ⅛ 拍"}
MAX_FILE_BYTES = 4 * 1024 * 1024


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

    def validate(self) -> None:
        if type(self.degree) is not int or not 0 <= self.degree <= 7:
            raise ValueError("音高必須是 0（休止）到 7。")
        if type(self.octave) is not int or self.octave not in range(-2, 3):
            raise ValueError("八度範圍為低兩個到高兩個八度。")
        if type(self.duration) is not int or self.duration not in DURATIONS:
            raise ValueError("不支援這個音符時值。")
        if type(self.dotted) is not bool or self.accidental not in ("", "#", "b"):
            raise ValueError("無效的附點或升降記號。")
        if self.degree == 0 and (self.octave or self.accidental):
            raise ValueError("休止符沒有八度或升降記號。")
        if type(self.finger) is not int or self.finger not in range(-1, 5):
            raise ValueError("二胡指法須為空弦 0 到 4 指。")
        if self.bow not in ("", "upbow", "downbow") or self.slide not in ("", "up", "down"):
            raise ValueError("不支援的弓法或滑音。")
        if self.dynamic not in ("", "pp", "p", "mp", "mf", "f", "ff", "sfz"):
            raise ValueError("不支援的力度。")
        if self.articulation not in ("", "staccato", "tenuto", "accent", "fermata", "trill", "mordent"):
            raise ValueError("不支援的演奏記號。")
        if any(type(getattr(self, name)) is not bool for name in ("slur_start", "slur_end", "tie", "repeat_start", "repeat_end")):
            raise ValueError("連線或反覆格式不正確。")
        if self.degree == 0 and (self.finger >= 0 or self.bow or self.slide or self.slur_start or self.slur_end or self.tie):
            raise ValueError("休止符不能加指法、弓法、滑音或連線。")

    @property
    def beats(self) -> Fraction:
        return Fraction(self.ticks, 16)

    @property
    def ticks(self) -> int:
        """Sixteenth-of-a-quarter units, including dotted 32nd notes."""
        return (64 // self.duration) * (3 if self.dotted else 2) // 2

    def token(self) -> str:
        self.validate()
        pitch = self.accidental + str(self.degree) + ("'" * self.octave if self.octave >= 0 else "," * -self.octave)
        if self.duration <= 2:
            return " ".join([pitch] + ["-"] * (int(self.beats) - 1))
        return {4: "c", 8: "q", 16: "s", 32: "d"}[self.duration] + pitch + ("." if self.dotted else "")

    def notation_token(self) -> str:
        parts = []
        if self.repeat_start:
            parts.append("R{")
        if self.slide:
            parts.append("slideUp" if self.slide == "up" else "slideDown")
        # A long note is a numbered head followed by quarter-note dashes.
        # Attach expressive marks and slurs to its head, not its last dash.
        head, *continuation = self.token().split()
        parts.append(head)
        if self.finger >= 0:
            parts.append(f"Fr={self.finger}")
        for mark in (self.bow, self.dynamic, self.articulation):
            if mark:
                parts.append("\\" + mark)
        if self.slur_end:
            parts.append(")")
        if self.slur_start:
            parts.append("(")
        parts.extend(continuation)
        if self.tie:
            parts.append("~")
        if self.repeat_end:
            parts.append("}")
        return " ".join(parts)


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

    def rhythm_error(self) -> str | None:
        position = 0
        bar_ticks = int(self.bar_length * 16)
        for i, note in enumerate(self.notes):
            offset = position % bar_ticks
            if offset + note.ticks > bar_ticks:
                return f"第 {i + 1} 個音符超出第 {position // bar_ticks + 1} 小節。請縮短時值，跨小節長音可拆成音符再加延音線。"
            position += note.ticks
        if self.notes and position % bar_ticks:
            return f"最後一小節還缺 {Fraction(bar_ticks - position % bar_ticks, 16)} 拍。按「補齊休止符」即可完成。"
        return None

    def notation_error(self) -> str | None:
        slur = repeat = False
        position = 0
        bar_ticks = int(self.bar_length * 16)
        for i, note in enumerate(self.notes):
            if note.slur_end:
                if not slur:
                    return f"第 {i+1} 個音符的連弓線缺少起點。"
                slur = False
            if note.slur_start:
                if slur:
                    return f"第 {i+1} 個音符有重疊連弓線，請先移除舊連線。"
                slur = True
            if note.tie and (not note.degree or i+1 == len(self.notes) or
                             (note.degree, note.octave, note.accidental) !=
                             (self.notes[i+1].degree, self.notes[i+1].octave, self.notes[i+1].accidental)):
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
        return None

    def fill_last_bar(self) -> None:
        remainder = (-sum(n.ticks for n in self.notes)) % int(self.bar_length * 16)
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
        if self.with_staff:
            lines.append("WithStaff")
        lines.append(f"1={self.key} {self.meter} 4={self.tempo}")
        bar, position = [], 0
        bar_ticks = int(self.bar_length * 16)
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
