"""Review 2 print fidelity: what the real LilyPond print shows must match the editor.

* note text with Unicode spaces renders (PDF and MIDI) and prints;
* text on a note with an after-grace attaches to that note;
* the canvas's accidental decisions (engraving.py) equal the accidentals LilyPond engraves,
  read from the SVG glyphs and their x positions next to each printed number.
"""
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtPdf import QPdfDocument

from jianpu_studio import engraving
from jianpu_studio.engine import find_tool, lilypond_env, render
from jianpu_studio.export import note_text
from jianpu_studio.model import Note, Part, Project, Score

sys.path.insert(0, str(Path(__file__).parent))
from test_notation_engine import midi_note_onsets  # noqa: E402


@pytest.fixture
def lilypond():
    tool = find_tool("lilypond")
    if not tool:
        pytest.skip("JIANPU_LILYPOND not configured")
    return tool.resolve()


def pdf_text(path):
    data = QByteArray(path.read_bytes())      # the buffer does not keep its data alive
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    document = QPdfDocument(None)
    document.load(buffer)
    text = " ".join(document.getAllText(page).text() for page in range(document.pageCount()))
    document.close()
    return text


def ink(path, scale=2.0):
    """Dark pixels of page 1 rendered by QtPdf (independent of the PDF's text layer)."""
    from PySide6.QtCore import QSize
    document = QPdfDocument(None)
    document.load(str(path))
    size = document.pagePointSize(0)
    image = document.render(0, QSize(int(size.width() * scale), int(size.height() * scale)))
    document.close()
    # QtPdf renders ink on a transparent page: count pixels that are mostly opaque and dark.
    argb = image.convertToFormat(image.Format.Format_ARGB32)
    data = bytes(argb.constBits())[:argb.bytesPerLine() * argb.height()]
    return sum(1 for k in range(0, len(data), 4) if data[k + 3] > 128 and data[k + 1] < 128)


def ink_per_glyph(path):
    """A small lower bound for one printed CJK character at the default text size."""
    return 50


def svg_items(lilypond, ly: Path):
    """(kind, text-or-path-start, x, y, font-size) of every placed glyph, page by page."""
    subprocess.run([str(lilypond), "-dbackend=svg", "-dno-point-and-click", "-o", str(ly.parent / "print"), str(ly)],
                   env=lilypond_env(), capture_output=True, check=True, timeout=300)
    pages = sorted(ly.parent.glob("print*.svg"), key=lambda p: int(re.sub(r"\D", "", p.stem) or 0))
    out = []
    for page, path in enumerate(pages):
        for group in ET.parse(path).getroot().iter():
            transform = group.get("transform", "")
            numbers = re.findall(r"-?\d+(?:\.\d+)?", transform)
            if not group.tag.endswith("}g") or not transform.startswith("translate") or len(numbers) != 2:
                continue
            x, y = map(float, numbers)
            for element in group:
                tag = element.tag.rsplit("}", 1)[-1]
                if tag == "text":
                    out.append(("text", "".join(element.itertext()).strip(), x, y + 1000 * page,
                                float(element.get("font-size", 0))))
                elif tag == "path":
                    out.append(("path", element.get("d", "")[:24], x, y + 1000 * page, 0.0))
    return out


def no_warnings(result, ignore="$^"):
    return [line for line in result.log.splitlines()
            if re.search(r"warning|error", line, re.I) and not re.search(ignore, line)]


# ---------------------------------------------------------------- 10. Unicode spaces in note text
SPACES = ["　", " ", " ", " ", " ", " "]


def test_note_text_never_hands_the_converter_a_space_it_would_split_on():
    def accepted(text):
        try:
            Note(1, text=text).validate()                   # the model keeps the user's text
        except ValueError:
            return False
        return True

    spaces = [chr(c) for c in range(33, 0x3001) if chr(c).isspace() and accepted("中速" + chr(c) + "深情")]
    assert set(SPACES) <= set(spaces) and len(spaces) == 16
    for space in spaces:
        text = "中速" + space + "深情"
        exported = note_text(text)
        assert exported.startswith("中速") and exported.endswith("深情")
        assert {c for c in exported if c.isspace()} == {" "}, repr(space)
    assert note_text("中速　深情") == "中速    深情"              # about the same printed width


@pytest.mark.integration
def test_note_text_with_unicode_spaces_renders_pdf_and_midi(qtbot, tmp_path, lilypond):
    texts = ["中速" + space + "深情" for space in SPACES]
    notes = [Note(1 + i % 7, text=text) for i, text in enumerate(texts)] + [Note(1), Note(2)]
    score = Score(notes=notes)
    assert [n.text for n in score.parts[0].notes[:len(texts)]] == texts
    result = render(Project(score).notation(), (tmp_path / "text").resolve(), lilypond)
    assert no_warnings(result) == []
    # Every text is placed as one markup (SVG output keeps the characters) ...
    placed = [t for kind, t, *_ in svg_items(lilypond, result.lilypond) if kind == "text"]
    assert sum("中速" in t for t in placed) == len(texts) and sum("深情" in t for t in placed) == len(texts)
    # ... and really inks the page. PDF text extraction is not used: with the bundled Noto CJK
    # font (CI, installers) QtPdf returns no usable CJK text, with system fonts it does.
    plain = render(Project(Score(notes=[Note(1 + i % 7) for i in range(len(texts))] + [Note(1), Note(2)])).notation(),
                   (tmp_path / "plain").resolve(), lilypond)
    assert ink(result.pdf) - ink(plain.pdf) > len(texts) * 4 * ink_per_glyph(result.pdf)
    assert midi_note_onsets(result.midi) == midi_note_onsets(plain.midi)


# ---------------------------------------------------------------- 11. text + after-grace
@pytest.mark.integration
def test_text_on_a_note_with_an_after_grace_prints_on_that_note(tmp_path, lilypond):
    score = Score(notes=[Note(1, text="主音", grace_after="23"), Note(5), Note(6), Note(7)])
    score.validate()                                       # existing files keep this combination
    result = render(Project(score).notation(), (tmp_path / "grace").resolve(), lilypond)
    assert no_warnings(result) == []                       # no "Unattached TextScriptEvent"
    items = svg_items(lilypond, result.lilypond)
    text_x = next(x for kind, t, x, *_ in items if kind == "text" and t == "主音")
    main_size = max(size for kind, t, *_, size in items if kind == "text" and t in "1567")
    mains = {t: x for kind, t, x, _, size in items if kind == "text" and t in "1567" and size == main_size}
    assert abs(text_x - mains["1"]) < 0.5 < mains["5"] - text_x, (text_x, mains)
    plain = Score(notes=[Note(1, grace_after="23"), Note(5), Note(6), Note(7)])
    plain_midi = render(Project(plain).notation(), (tmp_path / "plain").resolve(), lilypond, create_pdf=False)
    assert midi_note_onsets(result.midi) == midi_note_onsets(plain_midi.midi)


# ---------------------------------------------------------------- 12. accidentals: canvas == print
def n(spec, **fields):
    """'#4' sharp fa, "4'" high fa, 'b7,' low flat ti, 'n4' stored natural, '0' rest."""
    accidental = spec[0] if spec[0] in "#bn" else ""
    rest = spec[len(accidental):]
    return Note(int(rest[0]), accidental=accidental, octave=rest.count("'") - rest.count(","), **fields)


def bar(*specs):
    return [n(s) for s in specs]


ACCIDENTAL_FIXTURES = {
    # a real tie (from a single head) over the bar line: the continuation hides its sharp and
    # the rest of that bar shows every later fa's sign, even the same sharp or a natural
    "tie_over_bar_then_same_sharp": [*bar("1", "2", "3"), n("#4", tie=True), *bar("#4", "#4", "4", "#4")],
    "tie_over_bar_then_natural": [*bar("1", "2", "3"), n("#4", tie=True), *bar("#4", "4", "1", "1")],
    "tie_over_bar_flat": [*bar("1", "2", "3"), n("b7", tie=True), *bar("b7", "7", "b7", "1")],
    "tie_over_bar_natural": [*bar("1", "2", "3"), n("4", tie=True), *bar("4", "4", "#4", "4")],
    "tie_over_bar_other_octave": [*bar("1", "2", "3"), n("#4", tie=True), *bar("#4", "#4'", "4'", "4")],
    "tie_over_bar_expires": [*bar("1", "2", "3"), n("#4", tie=True), *bar("#4", "1", "1", "1"), *bar("4", "1", "1", "1")],
    "tie_chain_over_bar": [*bar("1", "2", "3"), n("#4", tie=True), n("#4", tie=True), *bar("#4", "#4", "1")],
    "tie_eighths_over_bar": [*bar("1", "2", "3"), n("1", duration=8), n("#4", duration=8, tie=True),
                             n("#4", duration=8), n("#4", duration=8), *bar("4", "1", "1")],
    "tie_into_half": [*bar("1", "2", "3"), n("#4", tie=True), n("#4", duration=2), *bar("#4", "4")],
    "tie_in_bar": [n("#4", tie=True), *bar("#4", "#4", "4"), *bar("#4"), n("4", tie=True), *bar("4", "4")],
    # jianpu-ly draws the tie from a half or longer (head plus dashes) as a curve, not a
    # LilyPond tie: the continuation is a fresh note and reprints its sign in the new bar
    "dashed_tie_over_bar": [n("#4", duration=1, tie=True), *bar("#4", "#4", "4", "1")],
    "dashed_tie_in_bar": [n("#4", duration=2, tie=True), *bar("#4", "#4"), *bar("1", "1", "1", "1")],
    "dotted_quarter_tie": [*bar("1", "2"), n("#4", dotted=True), n("#4", duration=8, tie=True), *bar("#4", "#4", "1", "1")],
    # grace notes take part in time order, in their note's bar
    "grace_before_and_after": [n("4", grace_before="#4"), n("#4"), n("4", grace_after="#4"), n("#4")],
    "grace_own_signs": [n("#4", grace_before="4"), n("4", grace_before="#4"), n("#4", grace_after="4"), n("#4")],
    "grace_at_bar_start": [*bar("#4", "1", "1", "1"), n("1", grace_before="4"), *bar("4", "#4", "1")],
    "grace_sets_new_bar": [*bar("1", "1", "1", "1"), n("4", grace_before="#4"), *bar("4", "1", "1")],
    "after_grace_at_bar_end": [*bar("1", "1", "1"), n("1", grace_after="#4"), *bar("4", "#4", "1", "1")],
    "grace_groups": [n("1", grace_before="#44#4"), n("4"), n("5", grace_after="b7#4"), n("7"), *bar("1", "1", "1", "1")],
    "grace_after_tie": [*bar("1", "2", "3"), n("#4", tie=True), n("#4"), n("1", grace_before="#4"), *bar("4", "1")],
    # ordinary cases
    "tuplet": [n("#4", duration=8, tuplet=3, tuplet_start=True), n("#4", duration=8, tuplet=3),
               n("4", duration=8, tuplet=3, tuplet_end=True), *bar("#4", "#4", "4")],
    "octaves": bar("#4", "4'", "4,", "4", "#4'", "4'", "b7,", "7"),
    "stored_naturals": bar("n4", "#4", "n4", "n4", "b7", "n7", "7", "b7"),
    "rests": [*bar("#4", "0", "4", "1"), n("0", duration=2), *bar("#4", "4")],
}
TWO_PART_FIXTURES = {
    "two_parts": [bar("#4", "4", "4", "#4", "1", "1", "1", "1"),
                  [*bar("4", "4", "#4", "4"), n("#4", tie=True), *bar("#4", "4", "1")]],
    "two_parts_graces": [[n("4", grace_before="#4"), *bar("4", "#4", "1")],
                         [*bar("#4", "1"), n("4", grace_after="b7"), n("7")]],
}
SIGNS = {"M0 112c0 13 8 25 20 30l3": "#", "M-8 375c9 5 18 7 28 7s19": "n", "M34 33l-1 -58v-10c0 -21 ": "b"}


def fixture_scores():
    scores = {name: Score(title=name, notes=notes) for name, notes in ACCIDENTAL_FIXTURES.items()}
    scores.update({name: Score(title=name, parts=[Part(f"P{i + 1}", notes) for i, notes in enumerate(parts)])
                   for name, parts in TWO_PART_FIXTURES.items()})
    for name, score in scores.items():
        score.validate()
        assert not score.rhythm_error(), (name, score.rhythm_error())
    return scores


def printed_accidentals(items, scores):
    """{fixture: per part, per note: (before-grace signs, sign, after-grace signs)} as engraved.

    Every printed number is a <text>; grace numbers are smaller (about 0.707×) and sit above
    the staff's main numbers. An accidental glyph sits just left of and above its number.
    Page numbers (in the top margin, y < 10 mm on their page) are not notes.
    """
    titles = sorted((y, t) for kind, t, x, y, size in items if kind == "text" and t in scores)
    assert [t for _, t in titles] == list(scores), "each fixture must print once, in order"
    digits = [[x, y, t, size, ""] for kind, t, x, y, size in items
              if kind == "text" and len(t) == 1 and t in "01234567" and y % 1000 > 10]
    main_size = max(d[3] for d in digits)
    digits = [d for d in digits if d[3] == main_size or abs(d[3] - main_size * 0.7069) < 0.05]
    signs = [(x, y, SIGNS[code]) for kind, code, x, y, _ in items if kind == "path" and code in SIGNS]
    assert signs, "no accidental glyphs found (unknown LilyPond font?)"
    for x, y, sign in signs:
        near = [d for d in digits if 0.5 < d[0] - x < 1.3 and 0.3 < d[1] - y < 1.5]
        assert near, ("accidental without a number", x, y, sign)
        min(near, key=lambda d: d[0])[4] = sign
    out = {}
    for index, (name, score) in enumerate(scores.items()):
        top = titles[index][0]
        bottom = titles[index + 1][0] if index + 1 < len(titles) else float("inf")
        mine = [d for d in digits if top < d[1] < bottom]
        rows = sorted({round(d[1], 2) for d in mine if d[3] == main_size})
        assert len(rows) == len(score.parts), (name, "fixture must fit one line", rows)
        staves = [sorted(d for d in mine if row - 3 < d[1] <= row + 0.01) for row in rows]
        out[name] = [read_part(name, part.notes, staff, main_size) for part, staff in zip(score.parts, staves)]
    return out


def read_part(name, notes, staff, main_size):
    k, result = 0, []
    for note in notes:
        entry = []
        for field in ("grace_before", "main", "grace_after"):
            count = (engraving.rest_beats(note) if note.degree == 0 else 1) if field == "main" else \
                len(re.findall(r"[1-7]", getattr(note, field)))
            got = staff[k:k + count]
            k += count
            assert len(got) == count and all((d[3] == main_size) == (field == "main") for d in got), (name, note)
            if field == "main":
                assert all(d[2] == str(note.degree) for d in got), (name, note, got)
            entry.append(tuple(d[4] for d in got))
        result.append((entry[0], entry[1][0], entry[2]))
    assert k == len(staff), (name, "unread numbers")
    return result


def canvas_accidentals(score):
    return [list(zip((b for b, _ in engraving.shown_grace_accidentals(score, p)),
                     engraving.shown_accidentals(score, p),
                     (a for _, a in engraving.shown_grace_accidentals(score, p))))
            for p in range(len(score.parts))]


def test_canvas_accidental_rules_for_ties_and_graces():
    """The rules found in real renders (the integration test below checks them all)."""
    def shown(notes):
        score = Score(notes=notes)
        return engraving.shown_accidentals(score, 0), engraving.shown_grace_accidentals(score, 0)

    mains, _ = shown(ACCIDENTAL_FIXTURES["tie_over_bar_then_same_sharp"])
    assert mains == ["", "", "", "#", "", "#", "n", "#"]
    mains, _ = shown(ACCIDENTAL_FIXTURES["dashed_tie_over_bar"])
    assert mains == ["#", "#", "", "n", ""]
    mains, graces = shown(ACCIDENTAL_FIXTURES["grace_before_and_after"])
    assert mains == ["n", "#", "n", ""] and graces == [(("#",), ()), ((), ()), ((), ("#",)), ((), ())]


@pytest.mark.integration
def test_canvas_accidentals_equal_the_printed_accidentals(tmp_path, lilypond):
    scores = fixture_scores()
    source = "\nNextScore\n".join(Project(score).notation() for score in scores.values())
    result = render(source, (tmp_path / "acc").resolve(), lilypond, create_pdf=False)
    assert no_warnings(result, ignore="compress") == []   # many short scores per page
    printed = printed_accidentals(svg_items(lilypond, result.lilypond), scores)
    differences = {name: (printed[name], canvas_accidentals(score))
                   for name, score in scores.items() if printed[name] != canvas_accidentals(score)}
    assert differences == {}


def test_canvas_grace_notes_show_the_printed_signs(qtbot):
    """The canvas draws each grace note's sign from shown_grace_accidentals (the rule checked
    against LilyPond above), not the sign as typed."""
    from jianpu_studio.score_view import NoteList
    view = NoteList()
    qtbot.addWidget(view)
    view.resize(850, 500)
    notes = [Note(4, accidental="#"), Note(1, grace_before="#4"), Note(2, grace_before="4"), Note(3)]
    score = Score(notes=notes)
    view.set_score(score)
    signs = engraving.shown_grace_accidentals(score, 0)
    assert [tuple(t[0] for t in view.note_graces[row][0]) for row in range(4)] == [s[0] for s in signs]
    assert signs[1][0] == ("",) and signs[2][0] == ("n",)
