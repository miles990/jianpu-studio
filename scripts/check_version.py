"""Fail before packaging if source, installer, or release tag disagree."""
import os
import re
import tomllib
from pathlib import Path

root = Path(__file__).resolve().parents[1]
version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
init = (root / "src/jianpu_studio/__init__.py").read_text()
installer = (root / "packaging/installer.iss").read_text(encoding="utf-8-sig")
assert re.search(r'__version__ = "([^"]+)"', init)[1] == version, "Python version mismatch"
assert re.search(r'#define AppVersion "([^"]+)"', installer)[1] == version, "Installer version mismatch"
ref = os.environ.get("GITHUB_REF", "")
if ref.startswith("refs/tags/"):
    assert ref == "refs/tags/v"+version, "Release tag does not match package version"
print("Verified version:", version)
