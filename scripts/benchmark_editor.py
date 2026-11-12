"""Measure real Qt note edits including event processing; no LilyPond work."""

import argparse
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import PySide6  # noqa: E402
from PySide6.QtCore import qVersion  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from jianpu_studio.app import MainWindow  # noqa: E402
from jianpu_studio.model import Note, Project, Score  # noqa: E402


def git_output(*args):
    return subprocess.check_output(["git", *args], text=True,
                                   cwd=Path(__file__).resolve().parents[1]).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--counts", type=int, nargs="+", default=[1000, 10000])
    parser.add_argument("--repetitions", type=int, default=5)
    args = parser.parse_args()
    app = QApplication([])
    app.setApplicationName("JianpuStudioBenchmark")
    MainWindow.render_score = lambda *a: None
    window = MainWindow(recovery=False)
    window.recovery_path = window.work / "benchmark-recovery.jps"
    window.autosave.stop()
    window.resize(1380, 850)
    window.show()
    result = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": sys.version,
        "pyside": PySide6.__version__,
        "qt": qVersion(),
        "commit": git_output("rev-parse", "HEAD"),
        "working_tree_dirty": bool(git_output("status", "--porcelain")),
        "qt_platform": os.environ.get("QT_QPA_PLATFORM", ""),
        "qt_scale_factor": os.environ.get("QT_SCALE_FACTOR", "1"),
        "window_size": [1380, 850],
        "method": "[Note(1)] * count; select middle note; change pitch inspector; "
                  "time edit plus QApplication.processEvents; render_score stubbed; "
                  "setup excluded; no wall-time assertions",
        "measurements": [],
    }
    try:
        for count in args.counts:
            window.reset(Project(Score(notes=[Note(1)] * count)))
            window.autopreview.stop()
            window.notes.select_rows([count // 2])
            window.notes.scrollTo(window.notes.currentIndex())
            window.input_mode.setCurrentIndex(window.input_mode.findData("edit"))
            app.processEvents()
            timings = []
            for i in range(args.repetitions):
                degree = i % 6 + 2
                started = time.perf_counter_ns()
                window.degree.setCurrentIndex(window.degree.findData(degree))
                app.processEvents()
                timings.append((time.perf_counter_ns() - started) / 1_000_000)
                assert window.project.score.notes[count // 2].degree == degree
                assert len(window.project.score.notes) == count
            projects = [entry[0] for entry in window.history] + [window.project]
            result["measurements"].append({
                "note_count": count,
                "repetitions": args.repetitions,
                "milliseconds": timings,
                "median_ms": statistics.median(timings),
                "min_ms": min(timings),
                "max_ms": max(timings),
                "history_snapshots": len(window.history),
                "history_and_current_note_list_bytes": sum(sys.getsizeof(p.score.notes) for p in projects),
                "history_and_current_unique_notes": len({id(n) for p in projects for n in p.score.notes}),
            })
    finally:
        window.dirty = False
        window.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    result["memory_note"] = "Note-list bytes use sys.getsizeof and include list references only; " \
                            "they exclude Note objects, project metadata, and native Qt allocations."
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
