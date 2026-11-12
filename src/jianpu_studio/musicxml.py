"""Bounded MusicXML reading; lossless advanced fallback for complex music."""

from __future__ import annotations

import zipfile
from fractions import Fraction
from pathlib import Path, PurePosixPath

from defusedxml import ElementTree as ET

from .model import KEYS, METERS, Note, Project, Score

MAX_XML = 16 * 1024 * 1024


def read_xml(path: Path) -> str:
    if path.stat().st_size > MAX_XML:
        raise ValueError("MusicXML 檔案太大（上限 16 MB）。")
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            if len(archive.infolist()) > 2000:
                raise ValueError("MXL 封裝含太多檔案。")
            def read_member(name: str) -> bytes:
                p = PurePosixPath(name)
                if p.is_absolute() or ".." in p.parts or archive.getinfo(name).file_size > MAX_XML:
                    raise ValueError("MXL 封裝含不安全路徑或過大的 XML。")
                return archive.read(name)
            container = ET.fromstring(read_member("META-INF/container.xml"))
            files = [e for e in container.iter() if e.tag.split("}")[-1] == "rootfile"]
            if not files:
                raise ValueError("MXL 找不到樂譜主檔。")
            data = read_member(files[0].attrib["full-path"])
    else:
        data = path.read_bytes()
    root = ET.fromstring(data)
    # Strip XML namespaces for upstream's expat parser and the visual importer.
    for element in root.iter():
        element.tag = element.tag.split("}")[-1]
    if root.tag != "score-partwise":
        raise ValueError("目前支援 MusicXML score-partwise 格式。")
    return ET.tostring(root, encoding="unicode")


def visual_import(xml: str) -> tuple[Project | None, str]:
    """Only accept a precisely representable subset; never silently drop voices."""
    root = ET.fromstring(xml)
    parts = root.findall("part")
    fallback = "此樂譜包含多聲部、和弦、連結線、反覆、弱起或特殊節奏；已保留為進階簡譜，避免遺失資訊。"
    if len(parts) != 1 or any(root.find(f".//{tag}") is not None for tag in
                              ("backup", "forward", "chord", "tie", "notations", "grace", "time-modification", "repeat", "ending", "transpose", "unpitched", "harmony")):
        return None, fallback
    for direction in root.findall(".//direction-type"):
        if any(child.tag != "metronome" for child in direction):
            return None, fallback
    score = Score(title=root.findtext("work/work-title") or root.findtext("movement-title") or "匯入樂譜")
    score.composer = root.findtext("identification/creator") or ""
    fifths_to_key = {-6: "Gb", -5: "Db", -4: "Ab", -3: "Eb", -2: "Bb", -1: "F", 0: "C", 1: "G", 2: "D", 3: "A", 4: "E", 5: "B", 6: "F#"}
    divisions, fifths, seen, voices = 1, 0, False, set()
    scale = [0, 2, 4, 5, 7, 9, 11]
    key_pc = {"C": 0, "G": 7, "D": 2, "A": 9, "E": 4, "B": 11, "F#": 6, "F": 5, "Bb": 10, "Eb": 3, "Ab": 8, "Db": 1, "Gb": 6}
    try:
        for measure in parts[0].findall("measure"):
            if measure.attrib.get("implicit") == "yes":
                return None, fallback
            attributes = measure.find("attributes")
            if attributes is not None:
                if seen and (attributes.find("key") is not None or attributes.find("time") is not None):
                    return None, fallback
                divisions = int(attributes.findtext("divisions", str(divisions)))
                fifths = int(attributes.findtext("key/fifths", str(fifths)))
                if fifths not in fifths_to_key or attributes.findtext("key/mode", "major") not in ("major", ""):
                    return None, fallback
                score.key = fifths_to_key[fifths]
                time = attributes.find("time")
                if time is not None:
                    score.meter = f"{time.findtext('beats')}/{time.findtext('beat-type')}"
                    if score.meter not in METERS:
                        return None, fallback
            for sound in measure.findall(".//sound"):
                if "tempo" in sound.attrib:
                    if seen:
                        return None, fallback
                    score.tempo = round(float(sound.attrib["tempo"]))
            measure_beats = Fraction(0)
            for element in measure.findall("note"):
                if element.find("lyric") is not None:
                    return None, "歌詞資訊已保留在進階簡譜模式。"
                voices.add(element.findtext("voice", "1"))
                if len(voices) > 1:
                    return None, fallback
                beats = Fraction(int(element.findtext("duration", "0")), divisions)
                duration, dotted = next(((d, dot) for d in (1, 2, 4, 8, 16, 32) for dot in (False, True)
                                         if Fraction(4, d) * (Fraction(3, 2) if dot else 1) == beats), (None, None))
                if duration is None:
                    return None, fallback
                if element.find("rest") is not None:
                    note = Note(0, duration=duration, dotted=dotted)
                else:
                    step = element.findtext("pitch/step")
                    octave = int(element.findtext("pitch/octave", "4"))
                    alter = int(element.findtext("pitch/alter", "0"))
                    # Preserve diatonic spelling relative to tonic, including B#/Cb.
                    letters = "CDEFGAB"
                    tonic_index = letters.index(score.key[0])
                    relative_step = letters.index(step) + (octave - 4) * 7 - tonic_index
                    relative_octave, degree_index = divmod(relative_step, 7)
                    actual = scale[letters.index(step)] + (octave - 4) * 12 + alter
                    expected = key_pc[score.key] + relative_octave * 12 + scale[degree_index]
                    accidental = actual - expected
                    if accidental not in (-1, 0, 1):
                        return None, fallback
                    note = Note(degree_index + 1, relative_octave, duration, dotted, {-1: "b", 0: "", 1: "#"}[accidental])
                note.validate()
                score.notes.append(note)
                measure_beats += beats
                seen = True
            if measure_beats != score.bar_length:
                return None, fallback
        if score.key not in KEYS or not score.notes:
            return None, fallback
        score.validate()
        if score.rhythm_error():
            return None, fallback
        return Project(score), "已匯入可點選編輯的單聲部樂譜。請核對音高、節奏與調號。"
    except (ValueError, TypeError, StopIteration):
        return None, fallback
