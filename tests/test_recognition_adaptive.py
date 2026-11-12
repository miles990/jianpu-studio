import threading

import pytest
from PIL import Image

from jianpu_studio import engine

LOW = "INFO Running\nWARN With a too low interline value of 5 pixels, the picture resolution is too low (try 300 DPI).\nExit forced. Failure"


@pytest.mark.parametrize("exit_failure", [True, False])
def test_low_resolution_retry_uses_measured_staff_spacing(tmp_path, monkeypatch, exit_failure):
    photo = tmp_path / "原圖.png"
    Image.new("L", (555, 374), 255).save(photo)
    original = photo.read_bytes()
    calls = []

    def run(args, cwd, cancel, **kwargs):
        with Image.open(cwd / args[-1]) as im:
            calls.append(im.size)
        if len(calls) == 1:
            if exit_failure:
                raise engine.ToolError(LOW)
            return LOW
        (cwd / "score.musicxml").write_text('<score-partwise version="4.0"/>')
        return "Recognition completed"

    monkeypatch.setattr(engine, "run_tool", run)
    xml, log = engine.recognize(photo, tmp_path / "output", tmp_path / "Audiveris.exe")
    assert xml.is_file()
    assert calls == [(555, 374), (1665, 1122)]
    assert "放大 3 倍" in log and "校對" in log
    assert photo.read_bytes() == original


def test_retry_stops_after_one_attempt_and_preserves_details(tmp_path, monkeypatch):
    photo = tmp_path / "photo.png"
    Image.new("L", (555, 374), 255).save(photo)
    calls = []

    def fail(*args, **kwargs):
        calls.append(1)
        raise engine.ToolError(LOW)

    monkeypatch.setattr(engine, "run_tool", fail)
    with pytest.raises(engine.RecognitionError, match="自動重試仍未完成") as caught:
        engine.recognize(photo, tmp_path / "out", tmp_path / "Audiveris.exe")
    assert len(calls) == 2
    assert "5 pixels" in caught.value.details


def test_cancel_is_not_retried(tmp_path, monkeypatch):
    photo = tmp_path / "photo.png"
    Image.new("L", (30, 30), 255).save(photo)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(engine.Cancelled):
        engine.recognize(photo, tmp_path / "out", tmp_path / "Audiveris.exe", cancel)


def test_unknown_failure_has_actionable_summary_and_full_log(tmp_path, monkeypatch):
    photo = tmp_path / "photo.png"
    Image.new("L", (30, 30), 255).save(photo)
    def fail(*args, **kwargs):
        raise engine.ToolError("INFO startup\nERROR cannot transcribe mixed notation")
    monkeypatch.setattr(engine, "run_tool", fail)
    with pytest.raises(engine.RecognitionError, match="照片辨識未完成") as caught:
        engine.recognize(photo, tmp_path / "out", tmp_path / "Audiveris.exe")
    assert "ERROR cannot transcribe" in caught.value.details


def test_large_image_is_not_upscaled_past_memory_limit(tmp_path, monkeypatch):
    photo = tmp_path / "photo.png"
    Image.new("L", (2400, 2400), 255).save(photo)
    def fail(*args, **kwargs):
        raise engine.ToolError(LOW)
    monkeypatch.setattr(engine, "run_tool", fail)
    with pytest.raises(engine.RecognitionError, match="大小上限"):
        engine.recognize(photo, tmp_path / "out", tmp_path / "Audiveris.exe")
    assert not (tmp_path / "out/resolution-retry.png").exists()
