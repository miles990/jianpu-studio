"""Isolated converters and cancellable external tools. No shell execution."""

from __future__ import annotations

import math
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

from . import symbols
from .i18n import tr
from .model import atomic_write


class ToolError(RuntimeError):
    pass


class Cancelled(ToolError):
    pass


class RecognitionError(ToolError):
    def __init__(self, message: str, details: str):
        super().__init__(message)
        self.details = details


def app_root() -> Path:
    """Directory holding runtime/, docs/ and examples/ next to the program.

    Windows/Linux one-folder builds keep them beside the executable. A macOS
    .app keeps the executable in Contents/MacOS and resources (including the
    bundled LilyPond) in Contents/Resources.
    """
    if not getattr(sys, "frozen", False):
        return Path(__file__).resolve().parents[2]
    executable = Path(sys.executable).resolve().parent
    if sys.platform == "darwin" and executable.name == "MacOS" and (executable.parent / "Resources").is_dir():
        return executable.parent / "Resources"
    return executable


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
    if sys.platform == "darwin":
        # Apps started from Finder do not inherit the shell's Homebrew PATH.
        candidates += [Path(prefix) / "bin" / name for prefix in ("/opt/homebrew", "/usr/local")]
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
        raise Cancelled(tr("作業已取消。"))
    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    process = subprocess.Popen(args, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kwargs)
    started = time.monotonic()
    try:
        while True:
            if cancel.is_set():
                raise Cancelled(tr("作業已取消。"))
            if time.monotonic() - started > timeout:
                raise ToolError(tr("作業超過 {seconds} 秒。請減少頁數或檢查樂譜內容。").format(seconds=int(timeout)))
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
        raise ToolError(text[-16000:] or tr("工具結束碼：{code}").format(code=process.returncode))
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


def lilypond_env() -> dict:
    """English LilyPond messages on every OS, without changing text encoding.

    LilyPond translates its log through gettext. LC_MESSAGES=C selects the
    untranslated (English) messages while LC_CTYPE/LANG keep the user's UTF-8
    character handling, so warnings read the same everywhere and Unicode
    paths keep working. LC_ALL would override LC_MESSAGES, so it is moved to
    LC_CTYPE instead of being dropped.
    """
    env = dict(os.environ)
    every = env.pop("LC_ALL", "")
    if every and "LC_CTYPE" not in env:
        env["LC_CTYPE"] = every
    env.pop("LANGUAGE", None)
    env["LC_MESSAGES"] = "C"
    return env


def validate_source(source: str) -> None:
    """Allow notation but exclude raw LilyPond/Scheme execution extensions.

    This is defense in depth, not an OS sandbox. Do not render untrusted .ly.
    jianpu-ly deliberately supports arbitrary LilyPond; this GUI does not.
    """
    if not source.strip() or len(source) > 4 * 1024 * 1024:
        raise ValueError(tr("樂譜是空白或超過 4 MB。"))
    # Single \\words only: the registry's export words and app prelude names (never MIDI twins).
    permitted = symbols.source_words()
    from .export import upstream_normalized
    for line in source.splitlines():
        # Check what the converter will actually read: it turns fullwidth ＂＼｛ into ASCII first.
        line = upstream_normalized(line).split("%", 1)[0]
        if re.search(r"\bLPH?:|#\s*[({]|\$|[\x00-\x08]", line):
            raise ValueError(tr("為安全起見，不支援內嵌 LilyPond／Scheme 程式碼（LP:、LPH:）。"))
        if re.match(r"\s*(slide|Fr)=", line):
            # Upstream treats such a line as a header and silently drops it (tech.md P4).
            raise ValueError(tr("一行不能以 slide= 或 Fr= 開頭（會被當成標題而整行遺失）；請接在前一個音符後面。"))
        if re.match(r"\s*(chords|frets)\s*=", line):
            raise ValueError(tr("此版本的原始碼模式不接受 chords= 或 frets=。"))
        if re.match(r"\s*[A-Za-z]+\s*=", line):
            if any(c in line.split("=", 1)[1] for c in ('"', "\\", "{", "}")):
                raise ValueError(tr("標題等文字欄位不能含引號、反斜線或大括號。"))
        for command in re.findall(r"\\([A-Za-z]+)", line):
            if command not in permitted:
                raise ValueError(tr("此版本不接受 LilyPond 指令：\\{command}").format(command=command))


@dataclass
class RenderResult:
    pdf: Path
    midi: Path | None
    lilypond: Path
    log: str


def render(source: str, output: Path, lilypond: Path,
           cancel: threading.Event | None = None, *, paper_size="a4", staff_size=20,
           create_pdf: bool = True) -> RenderResult:
    cancel = cancel or threading.Event()
    if type(create_pdf) is not bool:
        raise ValueError(tr("是否產生 PDF 須為布林值。"))
    if paper_size not in ("a4", "letter") or type(staff_size) is not int or not 16 <= staff_size <= 30:
        raise ValueError(tr("紙張須為 A4 / Letter，譜字大小須為 16–30。"))
    validate_source(source)
    output.mkdir(parents=True, exist_ok=True)
    source_path = output / "score.jly"
    atomic_write(source_path, source)
    version = run_tool([str(lilypond), "--version"], output, cancel, timeout=20, env=lilypond_env())
    match = re.search(r"GNU LilyPond 2\.(\d+)", version)
    if not match or int(match[1]) < 24:
        raise ToolError(tr("請使用 LilyPond 2.24 或更新版本。安裝版已附帶 2.26.0。"))
    try:
        log = run_tool(worker_command("convert", str(source_path), str(output / "score.ly"), match[1], paper_size, str(staff_size)),
                       output, cancel, env=worker_env())
    except ToolError:
        error = output / "score.error.txt"
        if error.is_file():
            raise ToolError(error.read_text(encoding="utf-8")) from None
        raise
    output_option = "--pdf" if create_pdf else "-dno-print-pages"
    log += run_tool([str(lilypond), "-dno-point-and-click", output_option, "-o", str(output / "score"),
                     str(output / "score.ly")], output, cancel, timeout=120, env=lilypond_env())
    pdf = output / "score.pdf"
    if create_pdf and not pdf.is_file():
        raise ToolError(tr("排譜引擎未產生 PDF。") + "\n" + log[-12000:])
    if not create_pdf and pdf.exists():
        raise ToolError(tr("僅產生 MIDI 的作業不應含有 PDF。") + "\n" + log[-12000:])
    # LilyPond numbers additional movements/parts. Prefer the main MIDI;
    # lexicographic sorting would incorrectly put score-1 before score.
    midi = next((p for p in (output / "score.midi", output / "score.mid") if p.is_file()), None)
    if midi is None:
        midi = next(iter(sorted(output.glob("*.midi"))), None) or next(iter(sorted(output.glob("*.mid"))), None)
    if not create_pdf and midi is None:
        raise ToolError(tr("排譜引擎未產生 MIDI。") + "\n" + log[-12000:])
    atomic_write(output / "render.log", log)
    return RenderResult(pdf, midi, output / "score.ly", log)


def recognize(image: Path, output: Path, audiveris: Path,
              cancel: threading.Event | None = None, *, retry_low_resolution=True) -> tuple[Path, str]:
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
    try:
        log = run_tool([str(audiveris), "-batch", "-transcribe", "-export", "-save", "-output", ".", "--", staged.name],
                       output, cancel, timeout=600, env=env)
        if re.search(r"too low interline value of\s+\d", log, re.I):
            raise ToolError(log)
    except Cancelled:
        raise
    except ToolError as exc:
        log = str(exc)
        atomic_write(output / "recognition.log", log)
        low = re.search(r"too low interline value of\s+(\d+(?:\.\d+)?)\s+pixels", log, re.I)
        if low and retry_low_resolution:
            from PIL import Image
            interline = float(low[1])
            factor = min(4, max(2, math.ceil(12 / max(interline, 1))))
            with Image.open(staged) as original:
                w, h = original.size
                if w * h * factor * factor > 40_000_000:
                    raise RecognitionError(tr("譜線間距太小，且放大後會超過影像大小上限。請裁切成單頁或使用較清晰的原圖。"), log) from exc
                scaled = original.convert("L").resize((w*factor, h*factor), Image.Resampling.LANCZOS)
                enhanced = output / "resolution-retry.png"
                scaled.save(enhanced, dpi=(300, 300))
            note = tr("原圖 {width} × {height} 像素，譜線間距 {interline} 像素。已自動放大 {factor} 倍重試；放大不會補回遺失細節，請對照原圖校對。").format(
                width=w, height=h, interline=f"{interline:g}", factor=factor)
            try:
                xml, retry_log = recognize(enhanced, output / "resolution-retry", audiveris, cancel, retry_low_resolution=False)
            except Cancelled:
                raise
            except ToolError as retry_error:
                details = log + "\n\n" + note + "\n\n" + getattr(retry_error, "details", str(retry_error))
                raise RecognitionError(note + "\n\n" + tr("自動重試仍未完成。請優先使用原始 PDF、300 DPI 掃描圖，或更近距離拍攝完整五線譜。"), details) from retry_error
            combined = note + "\n\n" + retry_log
            atomic_write(output / "recognition.log", combined)
            return xml, combined
        if low:
            message = tr("譜線間距只有 {pixels} 像素，解析度不足。請使用原始 PDF 或較清晰的掃描圖。").format(pixels=low[1])
        elif "OutOfMemoryError" in log:
            message = tr("辨識時記憶體不足。請裁切成單頁並關閉其他大型程式後重試。")
        elif "couldn't load any languages" in log.lower():
            message = tr("辨識字庫無法載入。請重新安裝完整版，或檢查工具設定。")
        else:
            message = tr("照片辨識未完成。請確認完整五線譜清楚、水平且沒有陰影；詳細紀錄可在「作業訊息」複製。")
        raise RecognitionError(message, log) from exc
    candidates = sorted(output.rglob("*.mxl")) + sorted(output.rglob("*.musicxml")) + sorted(output.rglob("*.xml"))
    candidates = [p for p in candidates if p.name != "container.xml"]
    atomic_write(output / "recognition.log", log)
    if not candidates:
        raise RecognitionError(tr("未能辨識出五線譜。請使用清晰、平整、完整包含譜線的照片，或先在 Audiveris 校對。"), log)
    return candidates[0], log
