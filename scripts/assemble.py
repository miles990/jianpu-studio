"""Attach independently executable engines and their notices to the app."""
import hashlib
import json
import platform
import shutil
import subprocess
import sys
from importlib.metadata import distribution
from pathlib import Path

root = Path(__file__).resolve().parents[1]
dist = root / "dist/JianpuStudio"
shutil.copytree(root / "vendor-runtime", dist / "runtime", dirs_exist_ok=True)
for name in ("docs", "examples"):
    shutil.copytree(root / name, dist / name, dirs_exist_ok=True)
for name in ("LICENSE", "THIRD_PARTY_NOTICES.md", "README.md"):
    shutil.copy2(root / name, dist / name)
licenses = dist / "licenses"
licenses.mkdir(exist_ok=True)
shutil.copy2(root / "src/jianpu_studio/vendor/LICENSE-jianpu-ly", licenses)
shutil.copy2(root / "packaging/LICENSE-Inno-Setup.txt", licenses)
for filename in ("LICENSE.txt", "LICENSE"):
    python_license = Path(sys.base_prefix) / filename
    if python_license.is_file():
        shutil.copy2(python_license, licenses / "Python-LICENSE.txt")
        break
build_info = {
    "python": platform.python_version(),
    "platform": platform.platform(),
    "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
    "packages": {name: distribution(name).version for name in
                 ("PySide6", "shiboken6", "Pillow", "defusedxml", "PyInstaller")},
}
(dist / "BUILD-INFO.json").write_text(json.dumps(build_info, indent=2) + "\n", encoding="utf-8")
for package in ("PySide6", "PySide6_Essentials", "PySide6_Addons", "shiboken6", "Pillow", "defusedxml"):
    d = distribution(package)
    for file in d.files or []:
        if "license" in str(file).lower() or "copying" in str(file).lower():
            source = Path(d.locate_file(file))
            if source.is_file():
                target = licenses / package / str(file).replace("..", "_")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
lines = []
for path in sorted(dist.rglob("*")):
    if path.is_file():
        with path.open("rb") as stream:
            lines.append(f"{hashlib.file_digest(stream, 'sha256').hexdigest()}  {path.relative_to(dist).as_posix()}")
(dist / "MANIFEST.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"Assembled {len(lines)} files")
