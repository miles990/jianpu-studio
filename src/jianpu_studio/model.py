"""Editable single-voice score. Fractions avoid rhythm rounding errors."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from fractions import Fraction
from pathlib import Path

KEYS = ("C", "G", "D", "A", "E", "B", "F#", "F", "Bb", "Eb", "Ab", "Db", "Gb")
METERS = ("4/4", "3/4", "2/4", "6/8", "3/8", "2/2", "9/8", "12/8")
DURATIONS = {1: "全音符 · 4 拍", 2: "二分 · 2 拍", 4: "四分 · 1 拍", 8: "八分 · ½ 拍", 16: "十六分 · ¼ 拍", 32: "三十二分 · ⅛ 拍"}
MAX_FILE_BYTES = 4 * 1024 * 1024


def text_value(value: object, name: str, limit: int = 200) -> str:
    if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 for c in value):
        raise ValueError(f"{name}須為單行文字，最多 {limit} 字。")
    return value


@dataclass
class Note:
    degree: int = 1
    octave: int = 0
    duration: int = 4
    dotted: bool = False
    accidental: str = ""

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

    @property
    def beats(self) -> Fraction:
        return Fraction(4, self.duration) * (Fraction(3, 2) if self.dotted else 1)

    def token(self) -> str:
        self.validate()
        pitch = self.accidental + str(self.degree) + ("'" * self.octave if self.octave >= 0 else "," * -self.octave)
        if self.duration <= 2:
            return " ".join([pitch] + ["-"] * (int(self.beats) - 1))
        return {4: "c", 8: "q", 16: "s", 32: "d"}[self.duration] + pitch + ("." if self.dotted else "")


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
        if type(self.with_staff) is not bool or len(self.notes) > 10000:
            raise ValueError("樂譜格式不正確或音符太多（上限 10,000）。")
        for note in self.notes:
            note.validate()

    def rhythm_error(self) -> str | None:
        position = Fraction(0)
        for i, note in enumerate(self.notes):
            offset = position % self.bar_length
            if offset + note.beats > self.bar_length:
                return f"第 {i + 1} 個音符超出第 {int(position // self.bar_length) + 1} 小節。請縮短時值；跨小節延音可使用進階原始碼。"
            position += note.beats
        if self.notes and position % self.bar_length:
            return f"最後一小節還缺 {self.bar_length - position % self.bar_length} 拍。按「補齊休止符」即可完成。"
        return None

    def fill_last_bar(self) -> None:
        position = sum((n.beats for n in self.notes), Fraction(0))
        remainder = (-position) % self.bar_length
        while remainder:
            for duration in DURATIONS:
                beats = Fraction(4, duration)
                if beats <= remainder:
                    self.notes.append(Note(0, duration=duration))
                    remainder -= beats
                    break
            else:
                raise ValueError("剩餘拍數無法用目前支援的休止符補齊，請調整附點三十二分音符。")

    def to_jianpu(self) -> str:
        self.validate()
        if not self.notes:
            raise ValueError("請先加入音符。")
        if error := self.rhythm_error():
            raise ValueError(error)
        # These text fields are literal, never raw LilyPond/Scheme.
        def literal(s: str) -> str:
            return s.replace('"', "' ").replace("\\", "/").replace("%", "％")
        lines = [f"title={literal(self.title)}", f"composer={literal(self.composer)}", "NoIndent"]
        if self.with_staff:
            lines.append("WithStaff")
        lines.append(f"1={self.key} {self.meter} 4={self.tempo}")
        bar, position = [], Fraction(0)
        for note in self.notes:
            bar.append(note.token())
            position += note.beats
            if position % self.bar_length == 0:
                lines.append(" ".join(bar))
                bar = []
        if self.lyrics.strip():
            lines.append("H: " + literal(self.lyrics))
        return "\n".join(lines) + "\n"


@dataclass
class Project:
    score: Score = field(default_factory=Score)
    mode: str = "visual"
    source: str = ""

    def to_dict(self) -> dict:
        return {"format": "jianpu-studio", "version": 1, **asdict(self)}

    @classmethod
    def from_dict(cls, data: dict) -> Project:
        if not isinstance(data, dict) or data.get("format") != "jianpu-studio" or data.get("version") != 1:
            raise ValueError("不是支援的簡譜工房專案（需要格式版本 1）。")
        try:
            raw = dict(data["score"])
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


def atomic_write(path: Path, text: str) -> None:
    """Replace only after flush succeeds; never truncate the user's old file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="." + path.name, suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
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
