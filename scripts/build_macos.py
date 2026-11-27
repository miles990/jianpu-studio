"""Build the macOS app and DMG on a Mac (one architecture per build).

    python scripts/fetch_runtime.py --platform macos
    python scripts/build_macos.py            # app + DMG under dist/
    python scripts/smoke_bundle.py dist/JianpuStudio.app --gui

The app is ad-hoc signed (no Developer ID / notarization): users confirm
the first launch in System Settings → Privacy & Security.
"""
import argparse
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_NAME = "簡譜工房.app"


def run(*args, **kwargs):
    print("+", " ".join(str(a) for a in args), flush=True)
    return subprocess.run([str(a) for a in args], check=True, **kwargs)


def make_icns(target: Path):
    """Use the designed macOS icon (packaging/icon/icon.icns: 16–512 px at 1x and 2x)."""
    shutil.copyfile(ROOT / "packaging/icon/icon.icns", target)


def sign(app: Path):
    # Ad-hoc signature, inside-out. LilyPond's own binaries are already
    # (linker) ad-hoc signed and are sealed as bundle resources.
    run("codesign", "--force", "--deep", "--sign", "-", "--timestamp=none", app)
    run("codesign", "--verify", "--deep", "--strict", "--verbose=2", app)


def make_dmg(app: Path, version: str, arch: str) -> Path:
    staging = ROOT / "build/macos/dmg"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    # ditto keeps symlinks, permissions and the code signature intact.
    run("ditto", app, staging / APP_NAME)
    (staging / "Applications").symlink_to("/Applications")
    dmg = ROOT / f"dist/JianpuStudio-{version}-macOS-{arch}.dmg"
    dmg.unlink(missing_ok=True)
    run("hdiutil", "create", "-volname", f"簡譜工房 {version}", "-srcfolder", staging,
        "-fs", "HFS+", "-format", "UDZO", "-imagekey", "zlib-level=9", "-ov", dmg)
    shutil.rmtree(staging)
    return dmg


def main():
    if sys.platform != "darwin":
        raise SystemExit("Build the macOS app on macOS.")
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-pyinstaller", action="store_true", help="reuse dist/JianpuStudio.app")
    args = parser.parse_args()
    version = re.search(r'__version__ = "([^"]+)"', (ROOT / "src/jianpu_studio/__init__.py").read_text())[1]
    arch = "arm64" if platform.machine() == "arm64" else "x86_64"
    if not (ROOT / "vendor-runtime/lilypond/bin/lilypond").is_file():
        raise SystemExit("Run scripts/fetch_runtime.py --platform macos first.")
    app = ROOT / "dist/JianpuStudio.app"
    if not args.skip_pyinstaller:
        icns = ROOT / "build/macos/JianpuStudio.icns"
        icns.parent.mkdir(parents=True, exist_ok=True)
        make_icns(icns)
        run(sys.executable, "-m", "PyInstaller", "--noconfirm", "--distpath", ROOT / "dist",
            "--workpath", ROOT / "build/pyinstaller", ROOT / "packaging/JianpuStudio.spec")
    run(sys.executable, ROOT / "scripts/assemble.py", "--target", app / "Contents/Resources")
    sign(app)
    dmg = make_dmg(app, version, arch)
    app_size = int(subprocess.check_output(["du", "-sk", str(app)]).split()[0]) * 1024
    print(f"App: {app} ({app_size / 1e6:.1f} MB)")
    print(f"DMG: {dmg} ({dmg.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
