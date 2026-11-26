"""Exercise frozen worker + bundled LilyPond and Audiveris, offline.

Windows: smoke_bundle.py dist/JianpuStudio --omr   (GUI self-test + OMR)
macOS:   smoke_bundle.py dist/JianpuStudio.app --gui
"""
import argparse
import os
import re
import subprocess
import tempfile
from pathlib import Path

from jianpu_studio.engine import recognize
from jianpu_studio.model import demo_project
from jianpu_studio.musicxml import read_xml, visual_import

parser = argparse.ArgumentParser()
parser.add_argument("directory", type=Path)
parser.add_argument("--omr", action="store_true", help="also run the GUI self-test and Audiveris (Windows)")
parser.add_argument("--gui", action="store_true", help="run the frozen GUI self-test")
args = parser.parse_args()
bundle = args.directory.resolve()
if bundle.suffix == ".app":
    # macOS: executable in Contents/MacOS, engines in Contents/Resources.
    exe = bundle / "Contents/MacOS/JianpuStudio"
    root = bundle / "Contents/Resources"
    lily = root / "runtime/lilypond/bin/lilypond"
    screenshot = "macos-ui.png"
else:
    root = bundle
    exe = root / "JianpuStudio.exe"
    lily = root / "runtime/lilypond/bin/lilypond.exe"
    screenshot = "windows-ui.png"
audiveris = root / "runtime/audiveris/Audiveris.exe"
minor = re.search(r"GNU LilyPond 2\.(\d+)", subprocess.run([str(lily), "--version"], capture_output=True,
                                                          text=True, check=True, timeout=60).stdout)[1]

if args.omr or args.gui:
    diagnostics = Path("work/bundle-diagnostics").resolve()
    diagnostics.mkdir(parents=True, exist_ok=True)
    subprocess.run([str(exe), "--self-test", str(diagnostics)], check=True, timeout=110)
    assert (diagnostics / screenshot).is_file()

with tempfile.TemporaryDirectory(prefix="jianpu 中文 smoke ") as work:
    work = Path(work)
    (work / "score.jly").write_text(demo_project().notation(), encoding="utf-8")
    subprocess.run([str(exe), "--worker", "convert", str(work / "score.jly"), str(work / "score.ly"), minor], check=True, timeout=90)
    assert (work / "score.ly").is_file(), list(work.iterdir())
    subprocess.run([str(lily), "--pdf", "-o", str(work / "score"), str(work / "score.ly")], check=True, timeout=120)
    pdf = (work / "score.pdf").read_bytes()
    assert pdf.startswith(b"%PDF")
    # The demo title is Chinese: the bundled Noto font must be found and used.
    assert b"NotoSerifCJKtc" in pdf, "bundled CJK font not embedded"
    midi = work / "score.mid"
    if not midi.is_file():
        midi = work / "score.midi"
    assert midi.read_bytes().startswith(b"MThd")
    if args.omr:
        os.environ["TESSDATA_PREFIX"] = str(root / "runtime/tessdata")
        subprocess.run([str(lily), "--png", "-dresolution=180", "-o", str(work / "photo"),
                        str(root / "examples/recognition-test.ly")], check=True, timeout=120)
        xml, log = recognize(work / "photo.png", work / "recognized", audiveris)
        assert "couldn't load any languages" not in log.lower(), log
        parsed = read_xml(xml)
        project, _ = visual_import(parsed)
        # Basic public-domain fixture should preserve all 42 pitches and lengths.
        if project is not None:
            assert [(n.degree, n.duration) for n in project.score.notes] == [(n.degree, n.duration) for n in demo_project().score.notes]
        subprocess.run([str(exe), "--worker", "import", str(xml), str(work / "recognized.jly")], check=True, timeout=90)
        assert (work / "recognized.jly").stat().st_size > 30
        subprocess.run([str(exe), "--worker", "convert", str(work / "recognized.jly"), str(work / "recognized.ly"), minor], check=True, timeout=90)
        subprocess.run([str(lily), "--pdf", "-o", str(work / "recognized-score"), str(work / "recognized.ly")], check=True, timeout=120)
        assert (work / "recognized-score.pdf").read_bytes().startswith(b"%PDF")
        # Reproduce the reported five-pixel staff spacing with a real low-DPI
        # fixture. Verify the bundled engine retries and yields renderable music.
        subprocess.run([str(lily), "--png", "-dresolution=72", "-o", str(work / "small-photo"),
                        str(root / "examples/recognition-test.ly")], check=True, timeout=120)
        low_xml, low_log = recognize(work / "small-photo.png", work / "small-recognized", audiveris)
        assert "自動放大" in low_log, low_log
        subprocess.run([str(exe), "--worker", "import", str(low_xml), str(work / "small.jly")], check=True, timeout=90)
        subprocess.run([str(exe), "--worker", "convert", str(work / "small.jly"), str(work / "small.ly"), minor], check=True, timeout=90)
        subprocess.run([str(lily), "--pdf", "-o", str(work / "small-score"), str(work / "small.ly")], check=True, timeout=120)
        assert (work / "small-score.pdf").read_bytes().startswith(b"%PDF")
print("Frozen bundle smoke test passed")
