"""Third evaluation, item 4: signs sit in the same place on the canvas and on paper.

For fixture scores holding the signs the evaluator found misplaced (D.S. al Coda, Fine,
D.C. al Fine, To Coda, tr, open-string 0, the metre), the test reads

* the canvas from what it really paints (the view rendered through ``QSvgGenerator``) plus the
  layout's bar frames, and
* the print from LilyPond's SVG output of the very ``score.ly`` the app engraves, with the
  grobs of interest labelled by class (``output-attributes``; this changes no layout),

and compares, sign by sign, the side of the staff (above / below) and the bar or note the sign
belongs to. PDF text extraction is not used (the bundled fonts make it unreliable on CI), and
nothing here depends on a font's metrics beyond "a text is roughly half an em per letter".
"""
import re
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace

import pytest
from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QFontMetricsF, QPainter

from jianpu_studio import engraving, glyphs
from jianpu_studio.engine import find_tool, lilypond_env, render
from jianpu_studio.model import Note, Part, Project, Score
from jianpu_studio.score_view import NoteList as ScoreView


@pytest.fixture
def lilypond():
    tool = find_tool("lilypond")
    if not tool:
        pytest.skip("JIANPU_LILYPOND not configured")
    return tool.resolve()


# ---------------------------------------------------------------- fixture scores
def etude():
    """1=D étude (the evaluator's): pickup, fingerings, strings, bowings, slides, vibrato,
    staccato, tr, hairpins, :‖ with 1./2., Segno, To Coda, harmonic, D.S. al Coda, Coda, fermata."""
    e = 8
    notes = [
        Note(5, marks=("erhu.outer", "bow.up", "finger.0", "dyn.mp"), text="中速"),                  # pickup
        Note(1, octave=1, marks=("bow.down", "finger.3")),
        Note(6, duration=e, marks=("finger.1",), spans_start=("line.slur",)),
        Note(5, duration=e, spans_end=("line.slur",)),
        Note(3, marks=("erhu.inner", "finger.2")), Note(5, marks=("finger.0",)),
        Note(6, dotted=True, marks=("erhu.vibrato",)), Note(1, octave=1, duration=e, marks=("finger.3",)),
        Note(5, duration=2, marks=("slide.down",)),
        Note(3, duration=e, marks=("art.staccato",), spans_start=("hairpin.cresc",)),
        Note(5, duration=e, marks=("art.staccato",)), Note(2, duration=e, marks=("art.staccato",)),
        Note(3, duration=e, marks=("art.staccato",)),
        Note(1, duration=2, marks=("orn.trill",), bar_end=("bar.repeat_end",)),
        Note(2, marks=("slide.up",), bar_start=("volta.1",)), Note(3, duration=e), Note(5, duration=e),
        Note(6, duration=2, spans_end=("hairpin.cresc",)),
        Note(6, bar_start=("volta.2",)), Note(5), Note(3), Note(2, bar_end=("volta.end",)),
        Note(1, marks=("dyn.f",), bar_start=("nav.segno",)), Note(2), Note(3), Note(5),
        Note(6, duration=e, marks=("pos.2",), spans_start=("line.slur", "hairpin.dim")),
        Note(1, octave=1, duration=e), Note(2, octave=1, duration=e),
        Note(3, octave=1, duration=e, spans_end=("line.slur",)),
        Note(2, octave=1, duration=2, spans_end=("hairpin.dim",), bar_end=("nav.tocoda",)),
        Note(1, octave=1, duration=1, marks=("harmonic",), bar_end=("nav.ds",)),
        Note(5), Note(6), Note(1, octave=1, duration=2),
        Note(1, octave=1, duration=2, dotted=True, marks=("art.fermata",)),
    ]
    score = Score(title="D調練習曲", composer="評測", key="D", meter="4/4", tempo=72, pickup=48, staff_size=24,
                  notes=notes)
    score.validate()
    assert score.notation_error() is None
    return score


def flat_b_piece():
    """1=♭B, 2/4: tr on the first note of a bar, an open-string 0 under a slur, a tuning text."""
    e = 8
    notes = [
        Note(3, duration=e, marks=("dyn.p", "pos.1"), spans_start=("line.slur",)), Note(5, duration=e),
        Note(6, duration=e, marks=("finger.0",)), Note(1, octave=1, duration=e, spans_end=("line.slur",)),
        Note(6, duration=e, marks=("orn.trill",)), Note(4, duration=e, accidental="#"), Note(5),
        Note(7, duration=e, accidental="b", marks=("art.staccato",), spans_start=("hairpin.dim",)),
        Note(6, duration=e, marks=("art.staccato",)), Note(5, duration=e), Note(3, duration=e),
        Note(5, duration=2, marks=("slide.down", "erhu.vibrato"), spans_end=("hairpin.dim",)),
    ]
    score = Score(title="降B調小品", key="Bb", meter="2/4", tempo=72, staff_size=24, notes=notes)
    score.validate()
    assert score.notation_error() is None
    return score


def duet():
    """Two parts, ‖: 1. 2. :‖, Fine and D.C. on the top part (the shipped example's structure)."""
    upper = [Note(d) for d in (3, 5, 6, 1, 2, 1, 6, 5, 2, 7, 6, 4, 5, 3, 1, 1, 7, 6, 5, 3, 2, 3, 2, 2)]
    lower = [Note(d, duration=2) for d in (1, 3, 4, 3, 5, 2, 3, 1, 5, 1, 5, 5)]
    for row, change in ((0, dict(bar_start=("bar.repeat_start",), marks=("dyn.mf",))),
                        (3, dict(bar_end=("bar.repeat_end",))), (4, dict(bar_start=("volta.1",))),
                        (8, dict(bar_start=("volta.2",))), (11, dict(bar_end=("volta.end",))),
                        (15, dict(bar_end=("nav.fine",))), (23, dict(bar_end=("nav.dc",)))):
        upper[row] = replace(upper[row], **change)
    score = Score(title="二重奏", key="D", tempo=72, staff_size=24,
                  parts=[Part("二胡一", upper), Part("二胡二", lower)])
    score.validate()
    assert score.notation_error() is None
    return score


# ---------------------------------------------------------------- reading an SVG
@dataclass
class Item:
    kind: str           # text | path | rect | ellipse | line
    text: str
    x: float
    y: float
    size: float = 0.0   # font size of a text
    width: float = 0.0
    height: float = 0.0
    cls: str = ""       # LilyPond grob class (print), "" on the canvas
    italic: bool = False
    left: float = 0.0   # a path's leftmost and rightmost x (absolute)
    right: float = 0.0


_NUMBER = r"-?\d+(?:\.\d+)?(?:e-?\d+)?"


def _matrix(text):
    """SVG transform attribute as (a, b, c, d, e, f)."""
    out = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    for name, args in re.findall(r"(\w+)\(([^)]*)\)", text or ""):
        v = [float(n) for n in re.findall(_NUMBER, args)]
        if name == "translate":
            m = (1, 0, 0, 1, v[0], v[1] if len(v) > 1 else 0)
        elif name == "scale":
            m = (v[0], 0, 0, v[1] if len(v) > 1 else v[0], 0, 0)
        elif name == "matrix":
            m = tuple(v)
        else:
            continue
        a, b, c, d, e, f = out
        out = (a * m[0] + c * m[1], b * m[0] + d * m[1], a * m[2] + c * m[3], b * m[2] + d * m[3],
               a * m[4] + c * m[5] + e, b * m[4] + d * m[5] + f)
    return out


def _compose(outer, inner):
    a, b, c, d, e, f = outer
    return (a * inner[0] + c * inner[1], b * inner[0] + d * inner[1], a * inner[2] + c * inner[3],
            b * inner[2] + d * inner[3], a * inner[4] + c * inner[5] + e, b * inner[4] + d * inner[5] + f)


def path_extent(d: str) -> tuple[float, float]:
    """(min x, max x) of a glyph outline in its own units. LilyPond writes glyphs as one absolute
    M followed by relative commands; control points are included (a slight overestimate)."""
    x = y = 0.0
    low, high = float("inf"), float("-inf")
    for command, args in re.findall(r"([MmLlHhVvCcSsQqTtZz])([^MmLlHhVvCcSsQqTtZz]*)", d):
        v = [float(n) for n in re.findall(_NUMBER, args)]
        if command in "Zz":
            continue
        step = {"M": 2, "m": 2, "L": 2, "l": 2, "H": 1, "h": 1, "V": 1, "v": 1, "C": 6, "c": 6, "S": 4, "s": 4,
                "Q": 4, "q": 4, "T": 2, "t": 2}[command]
        for k in range(0, len(v) - step + 1, step):
            group = v[k:k + step]
            if command in "Hh":
                xs, end = [group[0]], (group[0], None)
            elif command in "Vv":
                xs, end = [], (None, group[0])
            else:
                xs, end = group[0::2], (group[-2], group[-1])
            relative = command.islower()
            for value in xs:
                low, high = min(low, x + value if relative else value), max(high, x + value if relative else value)
            if end[0] is not None:
                x = x + end[0] if relative else end[0]
            if end[1] is not None:
                y = y + end[1] if relative else end[1]
            low, high = min(low, x), max(high, x)
    return (low, high) if low <= high else (0.0, 0.0)


def svg_items(path) -> list[Item]:
    """Every text, path, rect and ellipse of an SVG file with its absolute position."""
    out = []

    def number(element, name, inherited=None):
        value = element.get(name)
        if value is None:
            return float(inherited or 0)
        return float(re.findall(_NUMBER, value)[0])

    def walk(element, matrix, cls, size, italic):
        matrix = _compose(matrix, _matrix(element.get("transform")))
        cls = element.get("class") or cls
        size = number(element, "font-size", size)
        italic = {"italic": True, "normal": False}.get(element.get("font-style"), italic)
        tag = element.tag.rsplit("}", 1)[-1]

        def at(x, y):
            return matrix[0] * x + matrix[2] * y + matrix[4], matrix[1] * x + matrix[3] * y + matrix[5]
        scale = abs(matrix[0])
        if tag == "text":
            x, y = at(number(element, "x"), number(element, "y"))
            out.append(Item("text", "".join(element.itertext()).strip(), x, y, size * scale, cls=cls, italic=italic))
            return
        if tag == "path":
            x, y = at(0, 0)
            low, high = path_extent(element.get("d", ""))
            out.append(Item("path", element.get("d", "")[:24], x, y, cls=cls, left=at(low, 0)[0], right=at(high, 0)[0]))
        elif tag == "rect":
            x, y = at(number(element, "x"), number(element, "y"))
            out.append(Item("rect", "", x, y, width=number(element, "width") * scale,
                            height=number(element, "height") * abs(matrix[3]), cls=cls))
        elif tag == "line":
            x, y = at(number(element, "x1"), number(element, "y1"))
            out.append(Item("line", "", x, y, cls=cls))
        elif tag in ("ellipse", "circle"):
            x, y = at(number(element, "cx"), number(element, "cy"))
            radius = number(element, "rx") if tag == "ellipse" else number(element, "r")
            out.append(Item("ellipse", "", x, y, width=2 * radius * scale, cls=cls))
        for child in element:
            walk(child, matrix, cls, size, italic)

    walk(ET.parse(path).getroot(), (1.0, 0.0, 0.0, 1.0, 0.0, 0.0), "", 0.0, False)
    return out


# ---------------------------------------------------------------- the print
GROBS = ("NoteHead", "Rest", "BarLine", "SpanBar", "Script", "Fingering", "TextScript", "JumpScript", "CodaMark",
         "SegnoMark", "SectionLabel", "TextMark", "TimeSignature", "VoltaBracket", "Hairpin", "DynamicText")
LABELS = "\\layout { \\context { \\Score\n" + "".join(
    f"  \\override {grob}.output-attributes = #'((class . \"{grob}\"))\n" for grob in GROBS) + "} }\n"
# The systems are set far apart, so that a sign between two systems is unmistakably nearer to
# the one it belongs to (this moves whole systems; it does not move a sign within its system).
APART = "\n\\paper { system-system-spacing = #'((basic-distance . 36) (minimum-distance . 36) (padding . 14)) }\n"


def glyph_sequence(score, part):
    """The note row of every number or dash the part prints, in order (a long note is a number
    and dashes, a long rest one 0 per beat)."""
    out = []
    for row, note in enumerate(score.parts[part].notes):
        out += [row] * (engraving.rest_beats(note) if note.degree == 0 else engraving.nominal_beats(note))
    return out


class Print:
    """Where LilyPond put things, from its SVG output of ``score.ly`` (first page)."""

    def __init__(self, score, tmp_path, lilypond, draft=False):
        self.score = score
        result = render(Project(score).notation(draft=draft), tmp_path.resolve(), lilypond, create_pdf=False,
                        staff_size=score.staff_size)
        self.log = result.log
        self.ly = result.lilypond.read_text(encoding="utf-8")
        labelled = tmp_path / "labelled.ly"
        labelled.write_text(self.ly.replace("\\score {", LABELS + "\\score {", 1) + APART, encoding="utf-8")
        subprocess.run([str(lilypond), "-dbackend=svg", "-dno-point-and-click", "-o", str(tmp_path / "print"),
                        str(labelled)], env=lilypond_env(), capture_output=True, check=True, timeout=300)
        pages = sorted(tmp_path.glob("print*.svg"))
        assert len(pages) == 1, "the fixture must fit one page"
        self.items = svg_items(pages[0])
        parts = len(score.parts)
        heads = [i for i in self.items if i.cls in ("NoteHead", "Rest") and i.kind == "text"]
        self.rows = sorted({round(i.y, 1) for i in heads})                  # staff rows, top to bottom
        assert len(self.rows) % parts == 0
        self.systems = [self.rows[k:k + parts] for k in range(0, len(self.rows), parts)]
        self.heads = {}                                                      # (part, note row) -> its number
        self.glyphs = {}                                                     # part -> every number and dash
        for part in range(parts):
            mine = sorted((i for i in heads if self.rows.index(round(i.y, 1)) % parts == part),
                          key=lambda i: (round(i.y, 1), i.x))
            sequence = glyph_sequence(score, part)
            assert len(mine) == len(sequence), (part, len(mine), len(sequence))
            self.glyphs[part] = list(zip(sequence, mine))
            for row, item in reversed(self.glyphs[part]):
                self.heads[part, row] = item
        self.head_size = max(i.size for i in heads)
        self.barlines = sorted((i for i in self.items if i.cls == "BarLine" and i.kind == "rect"),
                               key=lambda i: (i.y, i.x))

    def system_of(self, item):
        return min(range(len(self.systems)), key=lambda k: min(abs(item.y - y) for y in self.systems[k]))

    def side(self, item):
        rows = self.systems[self.system_of(item)]
        return "above" if item.y < rows[0] - 0.5 else "below" if item.y > rows[-1] + 0.5 else "inside"

    def bar_end_x(self, bar):
        """x of the bar line that ends ``bar`` (read on the top part)."""
        start, end = self.score.part_bars(0)[bar]
        last = [item for row, item in self.glyphs[0] if start <= row < end][-1]
        return min(b.x for b in self.barlines if self._on_row(b, last) and b.x > last.x)

    @staticmethod
    def _on_row(barline, head):
        return barline.y - 0.5 <= head.y <= barline.y + barline.height + 0.5

    def bar_start_x(self, bar):
        start, _ = self.score.part_bars(0)[bar]
        first = self.heads[0, start]
        return max((b.x for b in self.barlines if self._on_row(b, first) and b.x < first.x), default=0.0)

    def bar_ending_near(self, item, x):
        """The bar whose end bar line is nearest to ``x`` in ``item``'s system."""
        system = self.system_of(item)
        bars = [bar for bar, (start, _) in enumerate(self.score.part_bars(0))
                if self.system_of(self.heads[0, start]) == system]
        return min(bars, key=lambda bar: abs(self.bar_end_x(bar) - x))

    def texts(self, cls, text=None):
        return [i for i in self.items if i.cls == cls and i.kind == "text" and (text is None or i.text == text)]

    def right_end(self, item):
        """Estimated right end of a text (about half an em per letter, any font)."""
        return item.x + 0.47 * item.size * len(item.text)

    def note_under(self, item, part=0):
        """The note whose number the centre of ``item`` stands over: (row, offset from the
        number's centre in number widths)."""
        centre = item.x + (0.28 * item.size if item.kind == "text" else 0.0)
        half = 0.28 * self.head_size
        system = self.system_of(item)
        heads = [(row, head) for (p, row), head in self.heads.items()
                 if p == part and self.system_of(head) == system]
        row, head = min(heads, key=lambda pair: abs(pair[1].x + half - centre))
        return row, (centre - head.x - half) / (2 * half)


# ---------------------------------------------------------------- the canvas
class Canvas:
    """Where the canvas paints things: its real painting, captured as SVG, plus the layout."""

    def __init__(self, score, qtbot, tmp_path):
        from PySide6.QtSvg import QSvgGenerator
        self.score = score
        view = ScoreView()
        qtbot.addWidget(view)
        view.resize(1300, 2400)                      # tall enough: nothing is scrolled out
        view.set_score(score, 0)
        view.show()
        view.relayout()
        assert view.verticalOffset() == 0
        self.view = view
        target = tmp_path / "canvas.svg"
        generator = QSvgGenerator()
        generator.setFileName(str(target))
        generator.setSize(view.viewport().size())
        generator.setViewBox(QRect(0, 0, view.viewport().width(), view.viewport().height()))
        painter = QPainter(generator)
        view.viewport().render(painter, QPoint(0, 0))
        painter.end()
        self.items = svg_items(target)
        self.scale = view.scale

    def text(self, text):
        found = [i for i in self.items if i.kind == "text" and i.text == text and i.italic]
        assert len(found) == 1, (text, [(i.text, i.x, i.y) for i in self.items if i.kind == "text"])
        return found[0]

    def system_of(self, item):
        def distance(system):
            rows = list(system.rows.values())
            return min(abs(item.y - row.baseline) for row in rows)
        return min(self.view.systems, key=distance)

    def side(self, item):
        rows = list(self.system_of(item).rows.values())
        digit = 22 * self.scale                       # a number is about this tall
        return "above" if item.y < rows[0].baseline - digit else "below" if item.y > rows[-1].baseline + 4 else "inside"

    def right_end(self, item, coda_glyph=False):
        width = QFontMetricsF(glyphs.font("italic", 13 * self.scale)).horizontalAdvance(item.text)
        return item.x + width + (16 * self.scale if coda_glyph else 0)

    def bar_ending_near(self, item, x):
        system = self.system_of(item)
        return min(system.bars, key=lambda bar: abs(self.view.bar_frames[bar].right() - x))

    def note_under(self, item):
        """Note of the active part whose number is nearest to the text's centre."""
        centre = item.x + QFontMetricsF(glyphs.font("italic", 13 * self.scale)).horizontalAdvance(item.text) / 2
        system = self.system_of(item)
        ranges = self.view.part_ranges[0]
        rows = [row for bar in system.bars for row in range(*ranges[bar])]
        return min(rows, key=lambda row: abs(self.view.note_center(row) - centre))


# ---------------------------------------------------------------- the comparison
def bar_sign_places(score, qtbot, tmp_path, lilypond, signs):
    """{name: (canvas place, print place)} with place = (side of the staff, bar it ends)."""
    canvas, printed = Canvas(score, qtbot, tmp_path), Print(score, tmp_path, lilypond)
    out = {}
    for name, cls in signs:
        on_canvas = canvas.text(name)
        coda = name == "To Coda"
        screen = (canvas.side(on_canvas), canvas.bar_ending_near(on_canvas, canvas.right_end(on_canvas, coda)))
        found = printed.texts(cls, name)
        assert len(found) == 1, (name, cls, [(i.cls, i.text) for i in printed.items if i.kind == "text"])
        end = printed.right_end(found[0])
        if coda:        # the ⊕ glyph right of the words is the mark's real right end
            glyph = [i for i in printed.items if i.cls == cls and i.kind == "path"]
            assert len(glyph) == 1
            end = glyph[0].x + 0.4 * found[0].size
        out[name] = (screen, (printed.side(found[0]), printed.bar_ending_near(found[0], end)))
    return out, canvas, printed


@pytest.mark.integration
def test_jump_and_coda_signs_sit_above_the_same_bar_end_on_screen_and_on_paper(qtbot, tmp_path, lilypond):
    score = etude()
    places, canvas, printed = bar_sign_places(score, qtbot, tmp_path, lilypond,
                                              [("D.S. al Coda", "JumpScript"), ("To Coda", "CodaMark")])
    assert places["To Coda"] == (("above", 7), ("above", 7))            # the bar it leaves, not the next
    assert places["D.S. al Coda"] == (("above", 8), ("above", 8))       # was below, in the hairpin
    # To Coda ends at its bar line (it used to be centred on it, half over the next bar).
    glyph = next(i for i in printed.items if i.cls == "CodaMark" and i.kind == "path")
    assert printed.bar_start_x(7) < glyph.x < printed.bar_end_x(7) + 0.3
    # "⊕ Coda" starts the coda bar on both, above the staff.
    label = printed.texts("SectionLabel", "Coda")
    assert len(label) == 1 and printed.side(label[0]) == "above"
    assert printed.bar_start_x(9) - 3 < label[0].x < printed.bar_end_x(9)
    on_canvas = canvas.text("Coda")
    assert canvas.side(on_canvas) == "above"
    assert canvas.view.bar_frames[9].left() < on_canvas.x < canvas.view.bar_frames[9].right()
    # No jump text is below the staff, where the hairpins are.
    hairpins = [i for i in printed.items if i.cls == "Hairpin"]
    assert hairpins and all(printed.side(i) == "below" for i in hairpins)
    assert all(printed.side(i) == "above" for i in printed.texts("JumpScript"))


@pytest.mark.integration
def test_fine_and_dc_al_fine_sit_above_the_top_part_of_a_duet(qtbot, tmp_path, lilypond):
    score = duet()
    places, canvas, printed = bar_sign_places(score, qtbot, tmp_path, lilypond,
                                              [("Fine", "JumpScript"), ("D.C. al Fine", "JumpScript")])
    assert places["Fine"] == (("above", 3), ("above", 3))
    assert places["D.C. al Fine"] == (("above", 5), ("above", 5))
    assert len(printed.systems[0]) == 2                                 # really two staves per system
    for item in printed.texts("JumpScript"):
        assert item.y < printed.systems[printed.system_of(item)][0]     # above part 1, not under part 2


@pytest.mark.integration
@pytest.mark.parametrize("make, trill_row, open_rows", [(etude, 13, [0, 5]), (flat_b_piece, 4, [2])])
def test_tr_and_open_string_0_stand_over_their_own_note(qtbot, tmp_path, lilypond, make, trill_row, open_rows):
    score = make()
    canvas, printed = Canvas(score, qtbot, tmp_path), Print(score, tmp_path, lilypond)
    assert [row for row, n in enumerate(score.notes) if "orn.trill" in n.marks] == [trill_row]
    assert [row for row, n in enumerate(score.notes) if "finger.0" in n.marks] == open_rows
    # tr -- canvas: the italic "tr" over its note; print: a Script glyph over the same note,
    # right of the bar line before it (it used to reach across the bar line).
    on_canvas = canvas.text("tr")
    assert canvas.note_under(on_canvas) == trill_row and canvas.side(on_canvas) == "above"
    scripts = [i for i in printed.items if i.cls == "Script" and i.kind == "path"]
    over = {printed.note_under(i)[0]: i for i in scripts}
    assert trill_row in over, sorted(over)
    trill = over[trill_row]
    row, offset = printed.note_under(trill)
    assert abs(offset) < 0.6 and printed.side(trill) == "above"          # centred on its number
    bar = next(b for b, (start, end) in enumerate(score.part_bars(0)) if start <= trill_row < end)
    # No wider than a number is tall (the full-size glyph was, and reached the bar line).
    assert 0.5 * printed.head_size < trill.right - trill.left < printed.head_size
    assert trill.left > printed.bar_start_x(bar) + 0.3                   # clear of the bar line before it
    # Every Script the print draws belongs to a note that has such a mark in the score (an
    # octave dot is a Script too).
    script_marks = {"orn.trill", "bow.up", "bow.down", "harmonic", "art.fermata"}
    expected = {row for row, n in enumerate(score.notes) if script_marks & set(n.marks) or n.octave}
    assert set(over) == expected
    # open-string 0 -- print: a small 0 centred over its own note's number; canvas: a small
    # ring centred over the same note.
    zeros = printed.texts("Fingering", "0")
    assert sorted(printed.note_under(i)[0] for i in zeros) == open_rows
    for item in zeros:
        assert abs(printed.note_under(item)[1]) < 0.35 and item.y < printed.heads[0, printed.note_under(item)[0]].y
    rings = [i for i in canvas.items if i.kind == "ellipse"]
    for row in open_rows:
        centre = canvas.view.note_center(row)
        baseline = canvas.view.systems[canvas.view.note_lines[row]].rows[0].baseline
        assert any(abs(i.x - centre) < 2.5 * canvas.scale and i.y < baseline - 20 * canvas.scale for i in rings), row


@pytest.mark.integration
@pytest.mark.parametrize("make, meter", [(etude, "4/4"), (flat_b_piece, "2/4")])
def test_the_metre_is_in_the_header_line_on_screen_and_on_paper(qtbot, tmp_path, lilypond, make, meter):
    score = make()
    canvas, printed = Canvas(score, qtbot, tmp_path), Print(score, tmp_path, lilypond)
    # canvas: one header line "1 = D    4/4 ..." above the first system
    header = [i for i in canvas.items if i.kind == "text" and meter in i.text]
    assert len(header) == 1 and header[0].y < canvas.view.systems[0].rows[0].top
    line = sorted((i for i in canvas.items if i.kind == "text" and abs(i.y - header[0].y) < 1), key=lambda i: i.x)
    assert line[0].text.startswith("1 =") and line[0].x <= header[0].x        # the key comes first
    # print: the same line -- key, then metre -- above the first staff, and no stacked time
    # signature on the number staff
    line_y = [i for i in printed.items if i.kind == "text" and i.text == meter]
    assert len(line_y) == 1 and line_y[0].y < printed.rows[0] - 2
    key = [i for i in printed.items if i.kind == "text" and i.text.startswith("1=") and abs(i.y - line_y[0].y) < 0.5]
    assert len(key) == 1 and key[0].x < line_y[0].x
    assert not [i for i in printed.items if i.cls == "TimeSignature"]
    assert "\\time " + meter in printed.ly                               # the metre itself is unchanged


@pytest.mark.integration
def test_a_header_word_on_the_first_note_prints_once(tmp_path, lilypond):
    """「中速」 on the first note moves into the header line; it used to stay above the note too."""
    printed = Print(etude(), tmp_path, lilypond)
    words = [i for i in printed.items if i.kind == "text" and "中速" in i.text]
    assert len(words) == 1 and words[0].y < printed.rows[0] - 2
    assert not [line for line in printed.log.splitlines() if "warning" in line.lower()]


# ---------------------------------------------------------------- item 2: drafts print what is on screen
def twinkle(bar_starts=None, bar_ends=None, bars=8):
    """``bars`` bars of four quarter notes with the given bar items ({bar: (ids...)})."""
    notes = []
    for bar in range(bars):
        for beat in range(4):
            notes.append(Note((bar + beat) % 7 + 1,
                              bar_start=(bar_starts or {}).get(bar, ()) if beat == 0 else (),
                              bar_end=(bar_ends or {}).get(bar, ()) if beat == 3 else ()))
    score = Score(title="反覆測試", key="C", notes=notes)
    score.validate()
    return score


def first_ending_only():
    """What the palette stores for 「第一結尾」 on bar 4: the repeat's body ends before it."""
    return twinkle({3: ("volta.1",)}, {2: ("bar.repeat_end",), 3: ("volta.end",)})


def unfinished(score, bar=6):
    """The same with a D.S. that has no Segno yet: only a draft prints."""
    notes = score.parts[0].notes
    row = score.part_bars(0)[bar][1] - 1
    notes[row] = replace(notes[row], bar_end=(*notes[row].bar_end, "nav.ds"))
    assert score.notation_error() is not None
    return score


def repeat_end_bars_drawn(view):
    """Bars whose end the canvas draws as :‖."""
    items = view.bar_items_cache
    last = len(items) - 1
    return {bar for bar, (starts, ends) in enumerate(items)
            if view._end_style(bar, starts, ends, items[bar + 1][0] if bar < last else (), bar == last) == "repeat_end"}


def repeat_end_bars_printed(printed):
    """Bars that end with a thick bar line preceded by dots (:‖), read from the print."""
    thick = [b for b in printed.barlines if b.width > 0.4]
    dots = [i for i in printed.items if i.cls == "BarLine" and i.kind == "path"]
    bars = range(len(printed.score.part_bars(0)))
    return {bar for bar in bars for line in thick
            if abs(line.x - printed.bar_end_x(bar)) < 1.0 and any(0 < line.x - d.x < 1.5 for d in dots)}


def draft_lines(score):
    return score.to_jianpu(draft=True).splitlines()


def test_a_repeat_end_before_a_first_ending_is_drawn_at_the_end_of_the_ending_everywhere(qtbot):
    score = first_ending_only()
    assert score.notation_error() is None
    from jianpu_studio.notation_rules import bar_items
    assert engraving.repeat_end_bars(bar_items(score)) == {3}
    view = ScoreView()
    qtbot.addWidget(view)
    view.resize(1300, 900)
    view.set_score(score, 0)
    view.relayout()
    assert repeat_end_bars_drawn(view) == {3}                     # the canvas draws the :‖ it stores
    # the final print: the ending is labelled 1. (LilyPond would say "1.2."), :‖ after it
    assert "\\jsVoltaFirst" in score.to_jianpu()
    # a draft of the same signs: :‖ on the line of bar 4, never in front of the bracket
    lines = [line for line in draft_lines(unfinished(first_ending_only())) if "\\bar" in line or "Volta" in line]
    assert len(lines) == 1 and lines[0].startswith("\\jsDraftVoltaOne ") and '\\bar ":|."' in lines[0]


def test_a_draft_of_complete_endings_puts_the_repeat_sign_where_the_canvas_does():
    score = unfinished(twinkle({3: ("volta.1",), 4: ("volta.2",)}, {2: ("bar.repeat_end",), 4: ("volta.end",)}))
    lines = draft_lines(score)
    marked = [line for line in lines if '":|."' in line]
    assert len(marked) == 1 and marked[0].startswith("\\jsDraftVoltaOne ")       # end of the first ending
    assert not any('":|."' in line for line in lines if "Volta" not in line)     # nothing before the 1.


def test_a_draft_writes_a_both_ways_repeat_as_one_bar_line():
    score = unfinished(twinkle({0: ("bar.repeat_start",)}, {1: ("bar.repeat_both",), 3: ("bar.repeat_end",)}))
    text = score.to_jianpu(draft=True)
    assert text.count('":|.|:"') == 1 and text.count('".|:"') == 1 and text.count('":|."') == 1


@pytest.mark.integration
@pytest.mark.parametrize("draft", [False, True])
def test_a_lone_first_ending_prints_as_the_canvas_draws_it(qtbot, tmp_path, lilypond, draft):
    """The evaluator's case: 小星星, 第一結尾 on bar 4 (then an unfinished D.S. → draft)."""
    score = unfinished(first_ending_only()) if draft else first_ending_only()
    canvas, printed = Canvas(score, qtbot, tmp_path), Print(score, tmp_path, lilypond, draft=draft)
    assert repeat_end_bars_drawn(canvas.view) == {3} == repeat_end_bars_printed(printed)
    # The bracket says 1. as drawn, not LilyPond's "1.2." for a single ending. A draft writes
    # the label as text; the finished print sets it in the number font (one glyph per sign).
    label = [i for i in printed.items if i.cls == "VoltaBracket" and i.kind == ("text" if draft else "path")]
    assert [i.text for i in label] == ["1."] if draft else len(label) == 2
    assert printed.bar_start_x(3) - 0.5 < label[0].x < printed.bar_end_x(3)
    on_canvas = [i for i in canvas.items if i.kind == "text" and i.text == "1." and not i.italic]
    assert len(on_canvas) == 1
    frame = canvas.view.bar_frames[3]
    assert frame.left() < on_canvas[0].x < frame.right()
    assert not [line for line in printed.log.splitlines() if "warning" in line.lower()]


@pytest.mark.integration
def test_a_draft_page_says_it_is_a_draft_and_a_finished_one_does_not(tmp_path, lilypond):
    from jianpu_studio import i18n
    draft = Print(unfinished(first_ending_only()), tmp_path / "draft", lilypond, draft=True)
    marks = [i for i in draft.items if i.kind == "text" and i.text == "草稿"]
    assert len(marks) == 2                                          # under the title, and in the page foot
    top, foot = sorted(marks, key=lambda i: i.y)
    assert top.y < draft.rows[0] - 2 and foot.y > draft.rows[-1] + 10
    assert any("不反覆" in i.text for i in draft.items if i.kind == "text")
    final = Print(first_ending_only(), tmp_path / "final", lilypond)
    assert not [i for i in final.items if i.kind == "text" and "草稿" in i.text]
    # the mark follows the interface language
    i18n.set_language(i18n.SIMPLIFIED)
    try:
        simplified = Print(unfinished(first_ending_only()), tmp_path / "sc", lilypond, draft=True)
    finally:
        i18n.set_language(i18n.TRADITIONAL)
    assert "不反复、不跳跃" in simplified.ly and "不反覆" not in simplified.ly
    assert len([i for i in simplified.items if i.kind == "text" and i.text == "草稿"]) == 2


# ---------------------------------------------------------------- item 5: one Coda sign
def ds_al_coda_with_extra_signs():
    """D.S. al Coda whose coda bar also carries a stored Coda sign (僅符號), and a text-only
    D.S. al Coda beside the working D.S. -- as a file saved by an earlier version may."""
    return twinkle({1: ("nav.segno",), 4: ("nav.coda_sign",)},
                   {2: ("nav.tocoda",), 3: ("nav.ds", "nav.dscoda_text")}, bars=6)


def test_a_coda_sign_on_the_automatic_coda_bar_is_drawn_and_written_once(qtbot):
    from jianpu_studio.notation_rules import bar_items
    score = ds_al_coda_with_extra_signs()
    assert score.notation_error() is None
    assert engraving.redundant_signs(bar_items(score)) == {(4, "nav.coda_sign"): "coda", (3, "nav.dscoda_text"): "nav.ds"}
    text = score.to_jianpu()
    assert "\\jsCodaSign" not in text and "\\jsDSCodaText" not in text and text.count("DS") == 1
    view = ScoreView()
    qtbot.addWidget(view)
    view.resize(1300, 900)
    view.set_score(score, 0)
    view.relayout()
    assert all("nav.coda_sign" not in starts and "nav.dscoda_text" not in ends for starts, ends in view.bar_items_cache)
    assert "nav.coda_sign" in score.notes[16].bar_start            # still stored: the user can remove it
    # away from the automatic label the same sign is drawn and written
    elsewhere = twinkle({1: ("nav.coda_sign",)})
    assert "\\jsCodaSign" in elsewhere.to_jianpu()


@pytest.mark.integration
def test_the_coda_bar_shows_one_coda_sign_on_screen_and_on_paper(qtbot, tmp_path, lilypond):
    score = ds_al_coda_with_extra_signs()
    canvas, printed = Canvas(score, qtbot, tmp_path), Print(score, tmp_path, lilypond)
    assert len([i for i in canvas.items if i.kind == "text" and i.text == "Coda"]) == 1
    assert len([i for i in canvas.items if i.kind == "text" and i.text == "D.S. al Coda"]) == 1
    assert len(printed.texts("SectionLabel", "Coda")) == 1
    assert len(printed.texts("JumpScript", "D.S. al Coda")) == 1
    assert not [i for i in printed.items if i.cls == "TextMark"]    # no second ⊕, no second text
    # the two ⊕ of the piece: one after "To Coda", one before "Coda"
    coda_glyphs = [i for i in printed.items if i.kind == "path" and i.cls in ("CodaMark", "SectionLabel")]
    assert len(coda_glyphs) == 2


# ---------------------------------------------------------------- P2: a page that reads like a book
def bars_per_system(printed):
    out = []
    for start, _ in printed.score.part_bars(0):
        system = printed.system_of(printed.heads[0, start])
        while len(out) <= system:
            out.append(0)
        out[system] += 1
    return out


def planned_lines(score):
    from jianpu_studio.notation_rules import bar_items
    breaks = sorted(engraving.line_breaks(score, bar_items(score)))
    edges = [0, *[bar + 1 for bar in breaks if bar + 1 < len(score.part_bars(0))], len(score.part_bars(0))]
    return [b - a for a, b in zip(edges, edges[1:])]


def test_lines_are_planned_even_and_the_coda_starts_a_line():
    eight = twinkle(bars=8)
    eight.staff_size = 24
    assert planned_lines(eight) == [4, 4]                    # LilyPond alone: six bars, then two
    assert planned_lines(twinkle(bars=4)) == [4]             # a short tune stays on one line
    assert planned_lines(etude()) == [5, 4, 2]               # pickup + 4, 4, and the coda on its own line
    assert planned_lines(duet()) == [3, 3]
    fixed = twinkle(bars=8)
    fixed.bars_per_line = 3                                  # the user's own setting is kept exactly
    assert fixed.to_jianpu().count("\\break") == 2 == Project(fixed).notation().count("\\break")
    assert planned_lines(fixed) == [3, 3, 2]
    # the planned breaks are part of what is engraved, not of the score's own text
    assert "\\break" not in eight.to_jianpu() and Project(eight).notation().count("\\break") == 1
    # denser music gets fewer bars per line, never a line planned wider than the paper
    from jianpu_studio.notation_rules import bar_items
    dense = Score(notes=[Note(1 + k % 7, duration=16) for k in range(16 * 6)], staff_size=24)
    widths, capacity = engraving.bar_widths(dense), engraving.line_capacity(dense)
    lines = planned_lines(dense)
    assert lines == [2, 2, 2] and all(2 * width <= capacity for width in widths)
    assert engraving.line_breaks(dense, bar_items(dense)) == {1, 3}


@pytest.mark.integration
@pytest.mark.parametrize("make", [lambda: twinkle(bars=8), etude, duet], ids=["eight bars", "etude", "duet"])
def test_the_print_breaks_its_lines_where_they_were_planned(tmp_path, lilypond, make):
    """LilyPond adds no break of its own inside a planned line (it would rather start a line
    than squeeze one, so an estimate that was too small would show here)."""
    score = make()
    score.staff_size = 24
    printed = Print(score, tmp_path, lilypond)
    assert bars_per_system(printed) == planned_lines(score)
    if make is etude:                                        # the coda bar starts its own line
        coda = printed.heads[0, score.part_bars(0)[9][0]]
        first_in_system = min((head for (part, _), head in printed.heads.items()
                               if part == 0 and printed.system_of(head) == printed.system_of(coda)), key=lambda h: h.x)
        assert first_in_system is coda


@pytest.mark.integration
def test_a_tuning_on_the_first_note_prints_in_the_header_line_after_the_tempo(tmp_path, lilypond):
    score = flat_b_piece()
    score.parts[0].notes[0] = replace(score.parts[0].notes[0], text="中速 37弦")
    printed = Print(score, tmp_path, lilypond)
    line = sorted((i for i in printed.items if i.kind == "text" and i.y < printed.rows[0] - 2
                   and any(word in i.text for word in ("1=", "2/4", "中速", "= 72", "37弦"))), key=lambda i: i.x)
    assert [i.text for i in line if i.text in ("2/4", "中速", "（37弦）")] == ["2/4", "中速", "（37弦）"]
    assert len({round(i.y) for i in line}) == 1 and line[-1].text == "（37弦）"      # one line; the tuning last
    assert not [i for i in printed.items if i.cls == "TextScript" and "37弦" in i.text]   # not above the note too
