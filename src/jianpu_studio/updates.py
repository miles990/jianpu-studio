"""Manual, verified updates from this project's stable GitHub releases."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

REPOSITORY = "miles990/jianpu-studio"
RELEASE_URL = f"https://github.com/{REPOSITORY}/releases/latest"
API_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
MAX_INSTALLER_BYTES = 1024 * 1024 * 1024
APP_ID = "{B07FD976-AEA4-42A2-B6CC-64EA0EA06A30}_is1"


class UpdateCancelled(Exception):
    pass


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    url: str
    size: int
    sha256: str
    notes: str = ""


@dataclass(frozen=True)
class InstallTarget:
    directory: Path
    scope: str


def version_tuple(value: str) -> tuple[int, int, int]:
    if not isinstance(value, str) or not re.fullmatch(r"v?\d+\.\d+\.\d+", value):
        raise ValueError("更新版本格式不正確。")
    return tuple(int(n) for n in value.removeprefix("v").split("."))


def release_info(data: dict, current_version: str) -> UpdateInfo | None:
    if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
        raise ValueError("更新來源不是正式發布版本。")
    version = str(data.get("tag_name", "")).removeprefix("v")
    if version_tuple(version) <= version_tuple(current_version):
        return None
    filename = f"JianpuStudio-{version}-Windows-x64-Setup.exe"
    asset = next((a for a in data.get("assets", []) if a.get("name") == filename), None)
    if not asset:
        raise ValueError("新版安裝檔尚未上傳完成，請稍後再試。")
    expected = f"https://github.com/{REPOSITORY}/releases/download/v{version}/{filename}"
    if asset.get("browser_download_url") != expected or asset.get("state") != "uploaded":
        raise ValueError("更新下載來源不符，已停止更新。")
    digest = asset.get("digest", "")
    if not isinstance(digest, str) or not re.fullmatch(r"sha256:[a-fA-F0-9]{64}", digest):
        raise ValueError("更新檔缺少 SHA-256 校驗，已停止更新。")
    size = asset.get("size")
    if type(size) is not int or not 0 < size <= MAX_INSTALLER_BYTES:
        raise ValueError("更新檔大小不正確。")
    return UpdateInfo(version, expected, size, digest[7:].lower(), str(data.get("body", ""))[:16000])


def fetch_update(current_version: str) -> UpdateInfo | None:
    request = urllib.request.Request(API_URL, headers={"User-Agent": "JianpuStudio-Updater", "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError("更新資訊過大。")
        return release_info(json.loads(raw), current_version)
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 429):
            raise RuntimeError("GitHub 暫時限制查詢次數，請稍後再試，或開啟下載頁。") from exc
        raise RuntimeError(f"無法取得更新資訊（HTTP {exc.code}），請稍後重試。") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError("目前無法連線到 GitHub。請確認網路後重試；離線寫譜不受影響。") from exc


def download_update(info: UpdateInfo, directory: Path, cancel: threading.Event, progress=None) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"JianpuStudio-{info.version}-Windows-x64-Setup.exe"
    partial = target.with_suffix(".download")
    digest = hashlib.sha256()
    received = 0
    request = urllib.request.Request(info.url, headers={"User-Agent": "JianpuStudio-Updater"})
    try:
        if cancel.is_set():
            raise UpdateCancelled("下載已取消。")
        with urllib.request.urlopen(request, timeout=30) as response, partial.open("wb") as out:
            while True:
                if cancel.is_set():
                    raise UpdateCancelled("下載已取消。")
                # read1 returns currently available socket data instead of
                # waiting for a whole megabyte on slow but active connections.
                chunk = response.read1(64 * 1024)
                if not chunk:
                    break
                received += len(chunk)
                if received > info.size or received > MAX_INSTALLER_BYTES:
                    raise ValueError("更新檔大小不符，已停止下載。")
                out.write(chunk)
                digest.update(chunk)
                if progress:
                    progress(received, info.size)
        if received != info.size or digest.hexdigest() != info.sha256:
            raise ValueError("更新檔校驗失敗，檔案可能不完整。請重新下載；原程式尚未變更。")
        if cancel.is_set():
            raise UpdateCancelled("下載已取消。")
        partial.replace(target)
        return target
    finally:
        partial.unlink(missing_ok=True)


def installed_target(directory: Path) -> InstallTarget | None:
    if os.name != "nt":
        return None
    import winreg
    key = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{APP_ID}"
    expected = os.path.normcase(str(directory.resolve()).rstrip("\\/"))
    for hive, scope in ((winreg.HKEY_CURRENT_USER, "CURRENTUSER"), (winreg.HKEY_LOCAL_MACHINE, "ALLUSERS")):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, key, 0, winreg.KEY_READ | view) as entry:
                    location = winreg.QueryValueEx(entry, "InstallLocation")[0]
                if os.path.normcase(str(Path(location).resolve()).rstrip("\\/")) == expected:
                    return InstallTarget(directory.resolve(), scope)
            except OSError:
                continue
    return None


def installer_arguments(target: InstallTarget, log: Path, restart=True) -> list[str]:
    if target.scope not in ("CURRENTUSER", "ALLUSERS"):
        raise ValueError("不支援的安裝模式。")
    return ["/SILENT", "/NORESTART", "/CLOSEAPPLICATIONS", f"/{target.scope}",
            f"/DIR={target.directory}", f"/LOG={log}", f"/JIANPURESTART={int(restart)}"]


def launch_update(installer: Path, info: UpdateInfo, target: InstallTarget, log: Path):
    # Verify again immediately before handing the file to Windows.
    digest = hashlib.sha256()
    with installer.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    if installer.stat().st_size != info.size or digest.hexdigest() != info.sha256:
        raise ValueError("更新檔在下載後已改變，請重新下載。")
    if os.name != "nt":
        raise RuntimeError("程式內套用更新目前只支援 Windows 安裝版。")
    args = installer_arguments(target, log)
    # ShellExecute handles the Windows elevation dialog when required. No shell
    # command text, credentials, or user-generated code is executed.
    os.startfile(str(installer), "open", arguments=subprocess.list2cmdline(args))
