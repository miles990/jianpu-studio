"""CLI worker also available in the frozen Windows executable."""

import sys
from pathlib import Path

from .engine import validate_source
from .model import atomic_write
from .musicxml import read_xml
from .vendor import jianpu_ly


def run(args: list[str]) -> int:
    try:
        operation, source, target, *extra = args
        if operation == "convert":
            data = Path(source).read_text(encoding="utf-8-sig")
            validate_source(data)
            jianpu_ly._lilypond_minor_version = int(extra[0]) if extra else 24
            paper_size = extra[1] if len(extra) > 1 else "a4"
            staff_size = int(extra[2]) if len(extra) > 2 else 20
            if paper_size not in ("a4", "letter") or not 16 <= staff_size <= 30:
                raise ValueError("Unsupported page or staff size")
            jianpu_ly.staff_size = jianpu_ly.lyric_size = staff_size
            result = jianpu_ly.process_input(data)
            result += '\n\\paper { #(set-paper-size "' + paper_size + '") }\n'
            # Prefer a bundled CJK font; system fallbacks cover developer installs.
            result += '\n\\paper { #(define fonts (set-global-fonts #:roman "Noto Serif CJK TC,Microsoft JhengHei,serif" #:sans "Noto Sans CJK TC,Microsoft JhengHei,sans-serif" #:factor (/ staff-height pt 20))) }\n'
        elif operation == "import":
            result = jianpu_ly.xml2jianpu(read_xml(Path(source)))
        else:
            raise ValueError("Unknown worker operation")
        atomic_write(Path(target), result)
        return 0
    except Exception as exc:
        # windowed PyInstaller builds may have no standard output stream.
        message = str(exc)
        if sys.stderr is not None:
            print(message, file=sys.stderr)
        if len(args) >= 3:
            atomic_write(Path(args[2]).with_suffix(".error.txt"), message)
        return 1
