import sys
import threading

import pytest

from jianpu_studio.engine import Cancelled, ToolError, find_tool, render, run_tool, validate_source
from jianpu_studio.model import demo_project


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
