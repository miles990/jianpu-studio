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


# ---------------------------------------------------------------- 23. long header fields
def edge_ink(path, page=0, band=8):
    """Dark pixels within ``band`` points of the left/right page edge (text run off the page)."""
    from PySide6.QtCore import QSize
    from PySide6.QtPdf import QPdfDocument
    document = QPdfDocument(None)
    document.load(str(path))
    size = document.pagePointSize(page)
    image = document.render(page, QSize(int(size.width()), int(size.height())))
    document.close()
    dark = 0
    for y in range(image.height()):
        for x in [*range(band), *range(image.width() - band, image.width())]:
            colour = image.pixelColor(x, y)
            dark += colour.alpha() > 128 and colour.green() < 128
    return dark


@pytest.mark.integration
@pytest.mark.parametrize("draft", [False, True])
def test_the_longest_title_and_composer_stay_on_the_page(draft, tmp_path, lilypond):  # noqa: F811
    score = bars(n=8, parts=2, staff_size=30, paper_size="a4", with_staff=True)
    score.title, score.composer = "W" * 200, "編曲 " + "文" * 100
    score.validate()
    if draft:
        score.parts[0].notes[0] = replace(score.parts[0].notes[0], bar_start=("nav.segno",))
    result = render(Project(score).notation(draft=draft), tmp_path.resolve(), lilypond, create_pdf=True,
                    staff_size=30, paper_size="a4")
    assert no_warnings(result) == []
    assert pdf_text(result.pdf).count("W") == 200                   # every letter is printed ...
    assert edge_ink(result.pdf) == 0                                # ... and nothing runs off the page
    printed = [t for kind, t, *_ in svg_items(lilypond, result.lilypond) if kind == "text"]
    assert sum(t.count("文") for t in printed) == 100 and any("編曲" in t for t in printed)


@pytest.mark.integration
def test_a_short_title_prints_on_one_line_at_its_size(tmp_path, lilypond):  # noqa: F811
    score = bars(n=4, parts=1)
    score.title, score.composer = "賽馬", "黃海懷 曲"
    result = render(Project(score).notation(), tmp_path.resolve(), lilypond, create_pdf=True)
    items = svg_items(lilypond, result.lilypond)
    title = [item for item in items if item[0] == "text" and item[1] == "賽馬"]
    composer = [item for item in items if item[0] == "text" and item[1] == "黃海懷 曲"]
    assert len(title) == 1 and len(composer) == 1 and title[0][4] > composer[0][4]


# ---------------------------------------------------------------- 24. structure over a pickup
PICKUP_CASES = {   # (score, pitches of part 1 in playing order: a quarter pickup C, then whole notes)
    "repeat from the pickup": (bars({0: ("bar.repeat_start",)}, {1: ("bar.repeat_end",)}, parts=1, pickup=48),
                               [60, 62, 60, 62, 64]),
    "repeat from the pickup, two parts": (bars({0: ("bar.repeat_start",)}, {1: ("bar.repeat_end",)}, pickup=48,
                                               with_staff=True), [60, 62, 60, 62, 64]),
    "implicit repeat over the pickup": (bars(ends={1: ("bar.repeat_end",)}, parts=1, pickup=48), [60, 62, 60, 62, 64]),
    "Segno on the pickup": (bars({0: ("nav.segno",)}, {2: ("nav.ds",)}, n=4, parts=1, pickup=48),
                            [60, 62, 64, 60, 62, 64, 65]),
    "Segno on the pickup, two parts": (bars({0: ("nav.segno",)}, {2: ("nav.ds",)}, n=4, pickup=48),
                                       [60, 62, 64, 60, 62, 64, 65]),
    "D.C. with a pickup, two parts": (bars(ends={2: ("nav.dc",)}, n=4, pickup=48), [60, 62, 64, 60, 62, 64, 65]),
    "repeat and endings over the pickup": (bars({0: ("bar.repeat_start",), 2: ("volta.1",), 3: ("volta.2",)},
                                                {1: ("bar.repeat_end",), 3: ("volta.end",)}, n=5, pickup=48),
                                           [60, 62, 64, 60, 62, 65, 67]),
}


@pytest.mark.integration
@pytest.mark.parametrize("name", list(PICKUP_CASES))
def test_a_structure_over_a_pickup_typesets_without_bar_check_warnings(name, tmp_path, lilypond):  # noqa: F811
    from jianpu_studio import smf
    score, pitches = PICKUP_CASES[name]
    assert score.notation_error() is None, name
    result = render(Project(score).notation(), tmp_path.resolve(), lilypond, create_pdf=True)
    assert no_warnings(result) == [], name
    notes = [n for n in smf.read(result.midi.read_bytes())[0] if n.track == 1]
    assert [n.pitch for n in notes] == pitches
    beat = 60 / score.tempo
    starts = [round(n.start / beat, 3) for n in notes]
    beats = [1 if pitch == 60 else 4 for pitch in pitches]            # the pickup is a quarter
    assert starts == [sum(beats[:k]) for k in range(len(beats))], starts   # back to back, in order
    ly = result.lilypond.read_text(encoding="utf-8")
    printed = ly.split("% === BEGIN JIANPU STAFF ===")[1].split("% === END JIANPU STAFF ===")[0]
    assert " | " in printed                                           # the printed staff keeps its bar checks
