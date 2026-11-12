"""Exercise frozen worker + bundled LilyPond and Audiveris, offline."""
import argparse
import os
import subprocess
import tempfile
from pathlib import Path

from jianpu_studio.engine import recognize
from jianpu_studio.model import demo_project
from jianpu_studio.musicxml import read_xml, visual_import

parser = argparse.ArgumentParser()
parser.add_argument("directory", type=Path)
parser.add_argument("--omr", action="store_true")
args = parser.parse_args()
root = args.directory.resolve()
exe = root / "JianpuStudio.exe"
lily = root / "runtime/lilypond/bin/lilypond.exe"
audiveris = root / "runtime/audiveris/Audiveris.exe"

with tempfile.TemporaryDirectory(prefix="jianpu 中文 smoke ") as work:
    work = Path(work)
    (work / "score.jly").write_text(demo_project().notation(), encoding="utf-8")
    subprocess.run([str(exe), "--worker", "convert", str(work / "score.jly"), str(work / "score.ly"), "24"], check=True, timeout=90)
    assert (work / "score.ly").is_file(), list(work.iterdir())
    subprocess.run([str(lily), "--pdf", "-o", str(work / "score"), str(work / "score.ly")], check=True, timeout=120)
    assert (work / "score.pdf").read_bytes().startswith(b"%PDF")
    assert (work / "score.midi").read_bytes().startswith(b"MThd")
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
        subprocess.run([str(exe), "--worker", "convert", str(work / "recognized.jly"), str(work / "recognized.ly"), "24"], check=True, timeout=90)
        subprocess.run([str(lily), "--pdf", "-o", str(work / "recognized"), str(work / "recognized.ly")], check=True, timeout=120)
        assert (work / "recognized.pdf").read_bytes().startswith(b"%PDF")
print("Frozen bundle smoke test passed")
