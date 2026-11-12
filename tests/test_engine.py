import struct
import sys
import threading

import pytest

from jianpu_studio import engine
from jianpu_studio.engine import Cancelled, ToolError, find_tool, render, run_tool, validate_source
from jianpu_studio.model import Note, Score, demo_project


@pytest.mark.parametrize("source", [
    "LP:\n#(system \"touch pwned\")\n:LP",
    "1=C 4/4 \\include \"private-file\"",
    'title=x" } #(system "x")',
    "chords=c #(system 'x')",
    "1=C 4/4 \\applyContext", "LPH: code :LPH",
])
def test_code_extensions_blocked(source):
    with pytest.raises(ValueError):
        validate_source(source)


def test_safe_notation_allowed():
    validate_source("title=小星星\n1=C 4/4 q#1 q2 3 4 5 \\fermata\nH: 星光")


def test_cancel_running_process(tmp_path):
    cancel = threading.Event()
    timer = threading.Timer(0.3, cancel.set)
    timer.start()
    try:
        with pytest.raises(Cancelled):
            run_tool([sys.executable, "-c", "import time; time.sleep(30)"], tmp_path, cancel)
    finally:
        timer.cancel()


def test_timeout_and_failure(tmp_path):
    with pytest.raises(ToolError, match="超過"):
        run_tool([sys.executable, "-c", "import time; time.sleep(30)"], tmp_path, threading.Event(), 0.3)
    with pytest.raises(ToolError, match="bad score"):
        run_tool([sys.executable, "-c", "import sys; print('bad score'); sys.exit(2)"], tmp_path, threading.Event())


@pytest.mark.integration
def test_real_lilypond_pdf_and_midi(tmp_path):
    tool = find_tool("lilypond")
    if not tool:
        pytest.skip("JIANPU_LILYPOND not configured")
    output = (tmp_path / "中文 空白路徑").resolve()
    project = demo_project()
    project.score.lyrics = "一閃一閃亮晶晶"
    result = render(project.notation(), output, tool.resolve())
    assert result.pdf.read_bytes().startswith(b"%PDF")
    assert result.midi.read_bytes().startswith(b"MThd")
    assert "小星星" in result.lilypond.read_text(encoding="utf-8")
    assert result.pdf.stat().st_size > 5000


@pytest.mark.integration
def test_real_lilypond_midi_only_never_creates_pdf(tmp_path):
    tool = find_tool("lilypond")
    if not tool:
        pytest.skip("JIANPU_LILYPOND not configured")
    output = (tmp_path / "試聽 中文路徑").resolve()
    result = render(demo_project().notation(), output, tool.resolve(), create_pdf=False)
    assert result.pdf == output / "score.pdf"
    assert not list(output.glob("*.pdf"))
    assert result.lilypond.is_file()
    assert (output / "render.log").read_text(encoding="utf-8") == result.log
    midi = result.midi.read_bytes()
    assert midi.startswith(b"MThd")
    header_size, format_type, tracks, division = struct.unpack(">IHHH", midi[4:14])
    assert header_size == 6 and format_type in (0, 1) and tracks > 0 and division > 0
    offset = 14
    for _ in range(tracks):
        assert midi[offset:offset+4] == b"MTrk"
        size = struct.unpack(">I", midi[offset+4:offset+8])[0]
        track = midi[offset+8:offset+8+size]
        assert len(track) == size and track.endswith(b"\xff\x2f\x00")
        offset += 8 + size
    assert offset == len(midi)


@pytest.mark.parametrize("unexpected_pdf", [False, True])
def test_midi_only_requires_midi_and_rejects_pdf(tmp_path, monkeypatch, unexpected_pdf):
    def fake_tool(args, cwd, *unused, **options):
        if "--version" in args:
            return "GNU LilyPond 2.24.4"
        if "-dno-print-pages" in args and unexpected_pdf:
            (cwd / "score.pdf").write_bytes(b"unexpected PDF")
            (cwd / "score.midi").write_bytes(b"MThd")
        return ""

    monkeypatch.setattr(engine, "run_tool", fake_tool)
    message = "不應含有 PDF" if unexpected_pdf else "未產生 MIDI"
    with pytest.raises(ToolError, match=message):
        render(demo_project().notation(), tmp_path, tmp_path / "lilypond", create_pdf=False)


@pytest.mark.integration
def test_bad_score_reports_converter_error(tmp_path):
    tool = find_tool("lilypond")
    if not tool:
        pytest.skip("JIANPU_LILYPOND not configured")
    with pytest.raises(ToolError):
        render("1=C 4/4\n1 2 3", tmp_path.resolve(), tool.resolve())


@pytest.mark.integration
def test_multiple_movements_choose_primary_midi(tmp_path):
    tool = find_tool("lilypond")
    if not tool:
        pytest.skip("JIANPU_LILYPOND not configured")
    result = render("1=C 4/4 1 2 3 4\nNextScore\n1=C 4/4 5 6 7 1\n", tmp_path.resolve(), tool.resolve())
    assert result.midi.name in ("score.mid", "score.midi")
    assert len(list(tmp_path.glob("*.mid*"))) == 2


@pytest.mark.integration
def test_erhu_marks_and_letter_layout_reach_real_pdf(tmp_path, qapp):
    from PySide6.QtPdf import QPdfDocument

    tool = find_tool("lilypond")
    if not tool:
        pytest.skip("JIANPU_LILYPOND not configured")
    score = Score(title="二胡排版驗證", with_staff=True, paper_size="letter", staff_size=24, bars_per_line=2, notes=[
        Note(1, octave=-2, duration=8, finger=0, bow="downbow", dynamic="mf", slide="up", slur_start=True, repeat_start=True),
        Note(2, duration=8, finger=1, articulation="staccato"),
        Note(3, octave=2, finger=2, bow="upbow", articulation="trill"),
        Note(4, duration=2, finger=3, articulation="fermata", slur_end=True, tie=True),
        Note(4, duration=2, finger=4, slide="down", slur_start=True),
        Note(5, duration=2, bow="downbow", slur_end=True, repeat_end=True),
        Note(6, duration=2, dotted=True, finger=2, dynamic="p"),
        Note(5, articulation="mordent"),
    ])
    result = render(score.to_jianpu(), tmp_path.resolve(), tool.resolve(),
                    paper_size=score.paper_size, staff_size=score.staff_size)
    assert result.midi.read_bytes().startswith(b"MThd")
    doc = QPdfDocument()
    assert doc.load(str(result.pdf)) == QPdfDocument.Error.None_
    size = doc.pagePointSize(0)
    assert abs(size.width() - 612) < 1 and abs(size.height() - 792) < 1
    doc.close()
    assert "warning:" not in result.log.lower()


@pytest.mark.parametrize("options", [
    {"paper_size": 'a4\") #(system "bad")'}, {"staff_size": True}, {"staff_size": 31},
    {"create_pdf": None}, {"create_pdf": 0}, {"create_pdf": 1}, {"create_pdf": "false"},
])
def test_render_options_are_validated_before_process_start(tmp_path, options):
    with pytest.raises(ValueError):
        render(demo_project().notation(), tmp_path, tmp_path / "unused", **options)
