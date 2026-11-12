"""Isolated converters and cancellable external tools. No shell execution."""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .model import atomic_write


class ToolError(RuntimeError):
    pass


class Cancelled(ToolError):
    pass


def app_root() -> Path:
    return Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[2]


def find_tool(name: str, explicit: str = "") -> Path | None:
    if explicit:
        p = Path(explicit).expanduser()
        return p if p.is_file() else None
    env_name = "JIANPU_LILYPOND" if name == "lilypond" else "JIANPU_AUDIVERIS"
    if os.environ.get(env_name):
        return find_tool(name, os.environ[env_name])
    root = app_root()
    candidates = ([root / "runtime/lilypond/bin/lilypond.exe", root / "runtime/lilypond/bin/lilypond"]
                  if name == "lilypond" else
                  [root / "runtime/audiveris/Audiveris.exe", root / "runtime/audiveris/bin/Audiveris"])
    if name == "audiveris":
        for base in (os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
            if base:
                candidates += [Path(base) / "Audiveris/Audiveris.exe"]
    for p in candidates:
        if p.is_file():
            return p
    found = shutil.which(name) or shutil.which(name.capitalize())
    return Path(found) if found else None


def terminate_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=15)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def run_tool(args: list[str], cwd: Path, cancel: threading.Event, timeout: float = 120,
             env: dict | None = None) -> str:
    if cancel.is_set():
        raise Cancelled("作業已取消。")
    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    process = subprocess.Popen(args, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kwargs)
    started = time.monotonic()
    try:
        while True:
            if cancel.is_set():
                raise Cancelled("作業已取消。")
            if time.monotonic() - started > timeout:
                raise ToolError(f"作業超過 {int(timeout)} 秒。請減少頁數或檢查樂譜內容。")
            try:
                output, _ = process.communicate(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                continue
    finally:
        if process.poll() is None:
            terminate_tree(process)
            process.communicate()
    text = output.decode("utf-8", errors="replace")
    if process.returncode:
        raise ToolError(text[-16000:] or f"工具結束碼：{process.returncode}")
    return text


def worker_command(*args: str) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--worker", *args]
    return [sys.executable, "-m", "jianpu_studio", "--worker", *args]


def worker_env() -> dict:
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    if not getattr(sys, "frozen", False):
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1]) + os.pathsep + env.get("PYTHONPATH", "")
    return env


def validate_source(source: str) -> None:
    """Allow notation but exclude raw LilyPond/Scheme execution extensions.

    This is defense in depth, not an OS sandbox. Do not render untrusted .ly.
    jianpu-ly deliberately supports arbitrary LilyPond; this GUI does not.
    """
    if not source.strip() or len(source) > 4 * 1024 * 1024:
        raise ValueError("樂譜是空白或超過 4 MB。")
    permitted = {"p", "pp", "ppp", "mp", "mf", "f", "ff", "fff", "sfz", "fermata", "bar",
                 "break", "noBreak", "pageBreak", "noPageBreak", "breathe", "accent", "staccato",
                 "tenuto", "marcato", "trill", "prall", "mordent", "turn"}
    for line in source.splitlines():
        line = line.split("%", 1)[0]
        if re.search(r"\bLPH?:|#\s*[({]|\$|[\x00-\x08]", line):
            raise ValueError("為安全起見，不支援內嵌 LilyPond／Scheme 程式碼（LP:、LPH:）。")
        if re.match(r"\s*(chords|frets)\s*=", line):
            raise ValueError("此版本的原始碼模式不接受 chords= 或 frets=。")
        if re.match(r"\s*[A-Za-z]+\s*=", line):
            if any(c in line.split("=", 1)[1] for c in ('"', "\\", "{", "}")):
                raise ValueError("標題等文字欄位不能含引號、反斜線或大括號。")
        for command in re.findall(r"\\([A-Za-z]+)", line):
            if command not in permitted:
                raise ValueError(f"此版本不接受 LilyPond 指令：\\{command}")


@dataclass
class RenderResult:
    pdf: Path
    midi: Path | None
    lilypond: Path
    log: str


def render(source: str, output: Path, lilypond: Path,
           cancel: threading.Event | None = None) -> RenderResult:
    cancel = cancel or threading.Event()
    validate_source(source)
    output.mkdir(parents=True, exist_ok=True)
    source_path = output / "score.jly"
    atomic_write(source_path, source)
    version = run_tool([str(lilypond), "--version"], output, cancel, timeout=20)
    match = re.search(r"GNU LilyPond 2\.(\d+)", version)
    if not match or int(match[1]) < 24:
        raise ToolError("請使用 LilyPond 2.24 或更新版本。安裝版已附帶 2.24.4。")
    try:
        log = run_tool(worker_command("convert", str(source_path), str(output / "score.ly"), match[1]),
                       output, cancel, env=worker_env())
    except ToolError:
        error = output / "score.error.txt"
        if error.is_file():
            raise ToolError(error.read_text(encoding="utf-8")) from None
        raise
    log += run_tool([str(lilypond), "-dno-point-and-click", "--pdf", "-o", str(output / "score"),
                     str(output / "score.ly")], output, cancel, timeout=120)
    pdf = output / "score.pdf"
    if not pdf.is_file():
        raise ToolError("排譜引擎未產生 PDF。\n" + log[-12000:])
    # LilyPond numbers additional movements/parts. Prefer the main MIDI;
    # lexicographic sorting would incorrectly put score-1 before score.
    midi = next((p for p in (output / "score.midi", output / "score.mid") if p.is_file()), None)
    if midi is None:
        midi = next(iter(sorted(output.glob("*.midi"))), None) or next(iter(sorted(output.glob("*.mid"))), None)
    atomic_write(output / "render.log", log)
    return RenderResult(pdf, midi, output / "score.ly", log)


def recognize(image: Path, output: Path, audiveris: Path,
              cancel: threading.Event | None = None) -> tuple[Path, str]:
    cancel = cancel or threading.Event()
    image, output = image.resolve(), output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    # The upstream Windows jpackage launcher can replace non-ACP command-line
    # characters with '?'. Keep ALL file arguments relative and ASCII. Windows
    # still receives the Unicode working directory through CreateProcessW.
    staged = output / ("input" + image.suffix.lower())
    if staged != image:
        shutil.copy2(image, staged)
    env = dict(os.environ)
    model = Path(env.get("TESSDATA_PREFIX", str(app_root() / "runtime/tessdata"))) / "eng.traineddata"
    if model.is_file():
        (output / "tessdata").mkdir(exist_ok=True)
        shutil.copy2(model, output / "tessdata/eng.traineddata")
        env["TESSDATA_PREFIX"] = "tessdata"
    log = run_tool([str(audiveris), "-batch", "-transcribe", "-export", "-save", "-output", ".", "--", staged.name],
                   output, cancel, timeout=600, env=env)
    candidates = sorted(output.rglob("*.mxl")) + sorted(output.rglob("*.musicxml")) + sorted(output.rglob("*.xml"))
    candidates = [p for p in candidates if p.name != "container.xml"]
    atomic_write(output / "recognition.log", log)
    if not candidates:
        raise ToolError("未能辨識出樂譜。請使用清晰、平整、完整包含五線譜的照片。\n" + log[-10000:])
    return candidates[0], log
