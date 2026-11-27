"""Render the palette's 「印出來長這樣」 previews with the real LilyPond (build time only).

Every registry symbol is put on a note 5 (lines on 5 6, bar items in the smallest valid
repeat/jump context), exported through the app's own pipeline (Score.to_jianpu -> worker
-> LilyPond 2.26), rendered by LilyPond (PostScript backend; the cairo backend drops PostScript
marks such as tremolo strokes), cropped to the ink and turned into a black-on-transparent
PNG (alpha = darkness) in src/jianpu_studio/assets/symbols/<id>.png. The app recolours it
for dark mode. Output is deterministic for one LilyPond version; re-run after changing
the registry:

    JIANPU_LILYPOND=/path/to/lilypond .venv/bin/python scripts/render_symbol_previews.py
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from jianpu_studio.storage import isolate_for_script  # noqa: E402

# A temporary settings file and data folder unless JIANPU_SETTINGS_DIR / JIANPU_DATA_DIR are set:
# never the user's real settings, recent files or recovery copy.
isolate_for_script()

from PySide6.QtGui import QColor, QGuiApplication, QImage  # noqa: E402

from jianpu_studio import symbols  # noqa: E402
from jianpu_studio.engine import find_tool, lilypond_env, render  # noqa: E402
from jianpu_studio.model import Note, Project, Score  # noqa: E402

OUT = ROOT / "src" / "jianpu_studio" / "assets" / "symbols"
HIDE = (r"\override Staff.TimeSignature.stencil = ##f \override Score.MetronomeMark.stencil = ##f "
        r"\override Score.BarNumber.stencil = ##f ")


def bars(*bar_notes):
    notes = []
    for bar in bar_notes:
        notes.extend(bar)
    return notes


def context(symbol) -> tuple[list[Note], str]:
    """Notes of a minimal valid score showing ``symbol`` and the meter to print it in."""
    sid = symbol.id
    five, six = Note(5), Note(6)
    if sid == "line.tie":
        return [Note(5, tie=True), Note(5)], "2/4"
    if sid == "tuplet.3":
        return [Note(5, duration=8, tuplet=3, tuplet_start=True), Note(6, duration=8, tuplet=3),
                Note(5, duration=8, tuplet=3, tuplet_end=True)], "1/4"
    if symbol.kind == "span":
        second = Note(3) if symbol.family == "gliss" else six
        return [replace(five, spans_start=(sid,)), replace(second, spans_end=(sid,))], "2/4"
    if symbol.kind == "composite":
        grace = {"slide.pad": dict(grace_before="3", marks=("slide.up",)), "dizi.zeng": dict(grace_after="6"),
                 "text.free": dict(text="中速")}[sid]
        return [Note(5, **grace)], "1/4"
    if sid == "pedal.off":      # a release prints nothing without a pedal to release
        return [Note(5, marks=("pedal.on",)), Note(6, marks=(sid,))], "2/4"
    if symbol.kind != "bar":
        return [Note(5, marks=(sid,))], "1/4"
    a, b, c, d = Note(5), Note(6), Note(5), Note(3)
    if sid in ("bar.dashed", "bar.double", "bar.final"):
        return [replace(a, bar_end=(sid,)), b], "1/4"
    if sid in ("bar.repeat_start", "bar.repeat_end"):
        return [replace(a, bar_start=("bar.repeat_start",)), replace(b, bar_end=("bar.repeat_end",))], "1/4"
    if sid == "bar.repeat_both":
        return [replace(a, bar_start=("bar.repeat_start",), bar_end=("bar.repeat_both",)),
                replace(b, bar_end=("bar.repeat_end",)), c], "1/4"
    if sid.startswith("volta."):
        # One alternative alone prints "1.2." (it covers both passes): show the first ending next to a second.
        count = {"volta.1": 2, "volta.2": 2, "volta.3": 3, "volta.4": 4, "volta.final": 2, "volta.end": 1}[sid]
        notes = [replace(a, bar_start=("bar.repeat_start",), bar_end=("bar.repeat_end",))]
        for k in range(1, count + 1):
            starts = (f"volta.{k}",) + (("volta.final",) if sid == "volta.final" and k == count else ())
            notes.append(Note(k % 7 + 1, bar_start=starts, bar_end=("volta.end",) if k == count else ()))
        return notes, "1/4"
    if sid in ("nav.segno", "nav.ds"):
        return [a, replace(b, bar_start=("nav.segno",)), replace(c, bar_end=("nav.ds",))], "1/4"
    if sid == "nav.dc":
        return [a, replace(b, bar_end=("nav.dc",))], "1/4"
    if sid == "nav.fine":
        return [replace(a, bar_end=("nav.fine",)), replace(b, bar_end=("nav.dc",))], "1/4"
    if sid == "nav.tocoda":
        return [replace(a, bar_start=("nav.segno",), bar_end=("nav.tocoda",)), replace(b, bar_end=("nav.ds",)), d], "1/4"
    if symbol.bar_edge == "start":
        return [replace(a, bar_start=(sid,)), b], "1/4"
    return [replace(a, bar_end=(sid,)), b], "1/4"


def source_for(symbol) -> str:
    notes, meter = context(symbol)
    if symbol.kind == "bar":
        # One bar per context note: two quarters, the bar items on the bar's edges.
        expanded = []
        for note in notes:
            expanded.append(replace(note, bar_end=()))
            expanded.append(Note(note.degree % 7 + 1, bar_end=note.bar_end))
        notes, meter = expanded, "2/4"
    score = Score(title="", composer="", key="C", meter="2/4" if meter == "1/4" else meter, tempo=96,
                  notes=notes, print_variant="tc")
    if meter == "1/4":
        # 1/4 keeps a single 5 per bar (Score only offers common meters; jianpu-ly accepts 1/4).
        total = sum(n.ticks for n in notes)
        score.meter = "2/4"
        if total % 96:
            score.notes.extend(score.rests_between(total, total + 96 - total % 96))
    text = Project(score).notation()
    if meter == "1/4":
        text = text.replace(" 2/4", " 1/4")
        # Drop the padding rest that made the 2/4 score valid: one 5 per 1/4 bar remains.
        text = "\n".join(re.sub(r"(?: c0)+$", "", line) for line in text.split("\n"))
    return text


def ink_crop(image: QImage, margin: int = 6) -> QImage:
    """Crop a page to its ink. LilyPond's -dcrop uses the layout extents, and marks drawn outside their
    extents on purpose (zero-size slide arrows, caesura) would be cut off."""
    left, top, right, bottom = image.width(), image.height(), -1, -1
    for y in range(image.height()):
        for x in range(image.width()):
            if image.pixelColor(x, y).lightness() < 200:
                left, right, top, bottom = min(left, x), max(right, x), min(top, y), max(bottom, y)
    if right < 0:
        return image
    left, top = max(0, left - margin), max(0, top - margin)
    return image.copy(left, top, min(image.width(), right + margin + 1) - left,
                      min(image.height(), bottom + margin + 1) - top)


def to_alpha(path: Path, target: Path) -> None:
    image = ink_crop(QImage(str(path)).convertToFormat(QImage.Format.Format_ARGB32))
    out = QImage(image.size(), QImage.Format.Format_ARGB32)
    for y in range(image.height()):
        for x in range(image.width()):
            color = image.pixelColor(x, y)
            alpha = round((1 - (color.red() * 0.299 + color.green() * 0.587 + color.blue() * 0.114) / 255) * color.alpha())
            out.setPixelColor(x, y, QColor(0, 0, 0, alpha))
    target.parent.mkdir(parents=True, exist_ok=True)
    out.save(str(target), "PNG")


def render_one(symbol, lilypond: Path, work: Path) -> tuple[str, str]:
    folder = work / symbol.id
    try:
        source = source_for(symbol)
        render(source, folder, lilypond, create_pdf=False)
        ly = (folder / "score.ly").read_text(encoding="utf-8")
        ly = re.sub(r"\\once \\override Score\.RehearsalMark[^\n]*\\mark \\markup\{1=C\}", "", ly)
        hide = HIDE + (r"\override Score.BarLine.stencil = ##f " if symbol.kind != "bar" else "")
        ly = ly.replace("\\override Staff.TimeSignature.style = #'numbered", hide)
        crop = folder / "crop.ly"
        crop.write_text(ly + "\n\\header { tagline = ##f }\n", encoding="utf-8")
        done = subprocess.run([str(lilypond), "-dno-point-and-click", "--pdf", "--png", "-dresolution=240",
                               "-o", str(folder / "crop"), str(crop)], cwd=folder, env=lilypond_env(),
                              capture_output=True, text=True, timeout=120)
        if done.returncode:
            return symbol.id, done.stderr[-400:]
        png = folder / "crop.png"
        if not png.is_file():
            return symbol.id, "no png"
        to_alpha(png, OUT / f"{symbol.id}.png")
        return symbol.id, ""
    except Exception as exc:     # report and continue: the app falls back to the drawn glyph
        return symbol.id, str(exc).splitlines()[-1] if str(exc) else type(exc).__name__


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", nargs="*", help="symbol ids")
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args()
    QGuiApplication(sys.argv[:1])
    lilypond = find_tool("lilypond")
    if lilypond is None:
        raise SystemExit("LilyPond not found (set JIANPU_LILYPOND)")
    wanted = [s for s in symbols.SYMBOLS.values() if not args.only or s.id in args.only]
    with tempfile.TemporaryDirectory(prefix="jianpu-previews-") as temp:
        with ThreadPoolExecutor(args.jobs) as pool:
            results = list(pool.map(lambda s: render_one(s, lilypond.resolve(), Path(temp)), wanted))
    failed = [(sid, error) for sid, error in results if error]
    print(f"rendered {len(results) - len(failed)} / {len(results)} previews into {OUT}")
    for sid, error in failed:
        print(f"  FAILED {sid}: {error}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
