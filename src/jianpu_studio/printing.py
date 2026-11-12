"""Keep external PDF readers independent of disposable render jobs."""
import hashlib
import time
from pathlib import Path

from .model import atomic_write


def prepare_print_copy(source: Path, cache: Path) -> Path:
    payload = source.read_bytes()
    cache.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - 7 * 24 * 60 * 60
    for old in cache.glob("jianpu-*.pdf"):
        try:
            if old.stat().st_mtime < cutoff:
                old.unlink()
        except OSError:
            # A PDF reader may still have an older print copy open on Windows.
            pass
    path = cache / f"jianpu-{hashlib.sha256(payload).hexdigest()}.pdf"
    if path.is_file():
        try:
            path.touch()
        except OSError:
            pass
    else:
        atomic_write(path, payload)
    return path
