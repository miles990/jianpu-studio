"""Download pinned official distributions, verify SHA-256, and stage runtimes.

The MSI is administratively EXTRACTED on the build runner, never installed on
the user's machine. Java, DLLs, models and licenses remain together.
"""
import argparse
import hashlib
import json
import os
import platform as host
import re
import shutil
import subprocess
import tarfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK = json.loads((ROOT / "packaging/runtime-lock.json").read_text())


def download(key: str) -> Path:
    spec = LOCK[key]
    cache = ROOT / "work/downloads"
    cache.mkdir(parents=True, exist_ok=True)
    # Name the cache after the pinned file, so a version bump never reuses
    # an older archive under the same key.
    target = cache / spec["url"].rsplit("/", 1)[-1]
    if not target.is_file() or hashlib.file_digest(target.open("rb"), "sha256").hexdigest() != spec["sha256"]:
        print(f"Downloading {key}", flush=True)
        part = target.with_suffix(".part")
        with urllib.request.urlopen(spec["url"], timeout=180) as response, part.open("wb") as output:
            shutil.copyfileobj(response, output)
        digest = hashlib.file_digest(part.open("rb"), "sha256").hexdigest()
        if digest != spec["sha256"]:
            part.unlink()
            raise RuntimeError(f"SHA-256 mismatch for {key}: {digest}")
        part.replace(target)
    return target


def lilypond_key(platform: str, arch: str) -> str:
    return f"lilypond-macos-{arch}" if platform == "macos" else f"lilypond-{platform}"


def stage(platform: str, with_omr: bool = True, arch: str = ""):
    destination = ROOT / "vendor-runtime"
    destination.mkdir(exist_ok=True)
    key = lilypond_key(platform, arch)
    version = re.search(r"/lilypond-(\d+\.\d+\.\d+)-", LOCK[key]["url"])[1]
    archive = download(key)
    unpack = ROOT / "work/lilypond-extract"
    shutil.rmtree(unpack, ignore_errors=True)
    unpack.mkdir(parents=True)
    if platform == "windows":
        with zipfile.ZipFile(archive) as z:
            z.extractall(unpack)
    else:
        with tarfile.open(archive) as t:
            t.extractall(unpack, filter="data")
    # Replace, never merge: a stale share/lilypond/<old version> must not remain.
    shutil.rmtree(destination / "lilypond", ignore_errors=True)
    shutil.copytree(unpack / f"lilypond-{version}", destination / "lilypond", symlinks=True)
    # The Traditional Chinese Noto OTF contains the full CJK glyph set, so
    # Simplified titles and lyrics also render without missing glyphs.
    font = destination / f"lilypond/share/lilypond/{version}/fonts/otf/NotoSerifCJKtc-Regular.otf"
    shutil.copy2(download("font"), font)
    licenses = destination / "licenses"
    licenses.mkdir(exist_ok=True)
    shutil.copy2(download("font-license"), licenses / "Noto-OFL.txt")
    if platform == "windows" and with_omr:
        if os.name != "nt":
            raise RuntimeError("Extract Windows Audiveris MSI on a Windows runner.")
        msi = ROOT / "work/Audiveris.msi"
        shutil.copy2(download("audiveris-windows"), msi)
        target = (ROOT / "work/audiveris-extract").resolve()
        log = (ROOT / "work/msi-extract.log").resolve()
        result = subprocess.run(["msiexec.exe", "/a", str(msi), "/qn", f"TARGETDIR={target}", "/L*v", str(log)])
        if result.returncode not in (0, 3010):
            raise RuntimeError(f"Audiveris extraction failed ({result.returncode}); see {log}")
        candidates = list(target.rglob("Audiveris.exe"))
        if len(candidates) != 1:
            raise RuntimeError(f"Expected one Audiveris launcher, found {candidates}")
        shutil.copytree(candidates[0].parent, destination / "audiveris", dirs_exist_ok=True)
        from patch_windows_launcher import patch_launcher
        patch_launcher(destination / "audiveris/Audiveris.exe")
        shutil.copy2(download("audiveris-license"), licenses / "Audiveris-AGPL-3.0.txt")
        tessdata = destination / "tessdata"
        tessdata.mkdir(exist_ok=True)
        shutil.copy2(download("ocr-english"), tessdata / "eng.traineddata")
        shutil.copy2(ROOT / "src/jianpu_studio/vendor/LICENSE-jianpu-ly", licenses / "Tessdata-Apache-2.0.txt")
    shutil.copy2(ROOT / "packaging/runtime-lock.json", destination / "runtime-lock.json")
    print(f"Verified runtimes staged in {destination}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    default = "windows" if os.name == "nt" else "macos" if host.system() == "Darwin" else "linux"
    parser.add_argument("--platform", choices=["windows", "linux", "macos"], default=default)
    parser.add_argument("--arch", choices=["arm64", "x86_64"],
                        default="arm64" if host.machine().lower() in ("arm64", "aarch64") else "x86_64",
                        help="macOS LilyPond architecture (default: this machine)")
    parser.add_argument("--no-omr", action="store_true")
    args = parser.parse_args()
    stage(args.platform, not args.no_omr, args.arch)
