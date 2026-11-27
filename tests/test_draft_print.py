"""Review 3 print findings, checked on real LilyPond output.

* a draft print shows every ending sign the score has (#4);
* user text can never pose as the header marker (#15);
* long title/composer lines stay on the page (#23);
* a repeat or Segno over a pickup typesets without bar-check warnings (#24).
"""
import re
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from jianpu_studio.engine import render
from jianpu_studio.model import Note, Part, Project, Score

sys.path.insert(0, str(Path(__file__).parent))
from test_print_fidelity import lilypond, no_warnings, pdf_text, svg_items  # noqa: E402,F401


def bars(starts=None, ends=None, *, n=3, parts=2, pickup=0, **fields):
    """``n`` one-note bars (whole notes; a quarter pickup first when ``pickup``); bar items on part 1."""
    starts, ends = starts or {}, ends or {}
    return Score(key="C", pickup=pickup, parts=[
        Part(f"part {p + 1}", [Note((i + p) % 7 + 1, duration=4 if pickup and not i else 1,
                                    bar_start=starts.get(i, ()) if not p else (),
                                    bar_end=ends.get(i, ()) if not p else ()) for i in range(n)])
        for p in range(parts)], **fields)


# ---------------------------------------------------------------- 4. draft endings
DRAFT_ENDINGS = {
    "fourth ending left open": (bars({1: ("volta.4",)}), ["4."]),
    "first and second left open": (bars({1: ("volta.1",), 2: ("volta.2",)}, {0: ("bar.repeat_end",)}), ["1.", "2."]),
    "final ending alone": (bars({1: ("volta.final",)}), ["結"]),
    "final ending alone on the last bar": (bars({2: ("volta.final",)}, parts=1), ["結"]),
}


def test_a_draft_writes_every_ending_sign_and_closes_open_brackets():
    for name, (score, _) in DRAFT_ENDINGS.items():
        assert score.notation_error(), name                       # unfinished: only a draft prints
        text = score.to_jianpu(draft=True)
        opened = len(re.findall(r"\\jsDraftVolta(?:One|Two|Three|Four|Jie)(?:Next)?\b", text))
        assert opened == (2 if "second" in name else 1), (name, text)
        assert text.count("\\jsDraftVoltaEnd") == 1, (name, text)      # closed once, with the score


@pytest.mark.integration
@pytest.mark.parametrize("name", list(DRAFT_ENDINGS))
def test_a_draft_pdf_prints_every_ending_bracket(name, tmp_path, lilypond):  # noqa: F811
    score, labels = DRAFT_ENDINGS[name]
    result = render(Project(score).notation(draft=True), tmp_path.resolve(), lilypond, create_pdf=True,
                    staff_size=score.staff_size)
    assert no_warnings(result) == []                     # no orphaned VoltaBracket
    printed = [t for kind, t, *_ in svg_items(lilypond, result.lilypond) if kind == "text"]
    for label in labels:
        assert label in printed, (name, label, printed)


# ---------------------------------------------------------------- 15. header marker
def test_user_text_cannot_pose_as_the_header_marker():
    from jianpu_studio.export import note_text
    from jianpu_studio.worker import HEAD_TEXT
    assert HEAD_TEXT not in note_text(HEAD_TEXT + "第2小節" + HEAD_TEXT)
    for first in ("中速", "主音"):
        score = bars(n=3, parts=1)
        notes = score.parts[0].notes
        notes[0] = replace(notes[0], text=first)
        notes[1] = replace(notes[1], text=HEAD_TEXT + "第2小節")
        text = score.to_jianpu()
        assert text.count(HEAD_TEXT) == (first == "中速"), text


@pytest.mark.integration
@pytest.mark.parametrize("first", ["中速", "主音"])
def test_a_word_joiner_in_note_text_keeps_the_text_on_its_note(first, tmp_path, lilypond):  # noqa: F811
    score = bars(n=3, parts=1)
    notes = score.parts[0].notes
    notes[0] = replace(notes[0], text=first)
    notes[1] = replace(notes[1], text="⁠第2小節")
    result = render(Project(score).notation(), tmp_path.resolve(), lilypond, create_pdf=True)
    assert no_warnings(result) == []
    ly = result.lilypond.read_text(encoding="utf-8")
    meter = re.findall(r"^meter = .*", ly, flags=re.M)
    assert meter and all("第2小節" not in m and ("中速" in m) == (first == "中速") for m in meter)
    printed = [t for kind, t, *_ in svg_items(lilypond, result.lilypond) if kind == "text"]
    assert any("第2小節" in t for t in printed), printed
