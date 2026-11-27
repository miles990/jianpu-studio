"""Round-4 domain review (erhu teacher / engraver): marks look the same on screen and in print.

The canvas position (glyphs.position, from the registry ``place``/``kind``) and the side the
LilyPond macro prints on (read from the same registry row's export macro) must agree, and the
marks the review found ambiguous must be distinguishable.
"""
import re
from pathlib import Path

import pytest
from PySide6.QtGui import QColor, QImage, QPainter

from jianpu_studio import glyphs, symbols, symbols_ui
from jianpu_studio.engine import find_tool, render
from jianpu_studio.model import Note, Part, Project, Score, load_project

ROOT = Path(__file__).resolve().parents[1]
DIGIT_WIDTH = 1.3          # staff spaces: a jianpu digit starts at the script origin and ends about here


def macro(symbol) -> str:
    word = symbol.export.partition(":")[2].split(" ")[0].lstrip("\\")
    for s in symbols.SYMBOLS.values():
        if word in s.macros:
            value = s.macros[word]
            return value if isinstance(value, str) else value[0]
    return ""


def postscript_x(text: str) -> list[float]:
    """x of every moveto/lineto/curveto point in a \\postscript string."""
    body = re.search(r'\\postscript "([^"]*)"', text)[1]
    tokens = body.split()
    xs = []
    for index, token in enumerate(tokens):
        if token in ("moveto", "lineto"):
            xs.append(float(tokens[index - 2]))
        elif token == "curveto":
            xs += [float(tokens[index - k]) for k in (6, 4, 2)]
    return xs


def print_side(symbol) -> str:
    """Where the export prints a point/pre mark, judged from the macro text alone."""
    slot, word = symbol.words()
    text = macro(symbol)
    if "Accidental.after-line-breaking" in text:      # restyled jianpu-ly slide arrow
        return "right" if "ly:stencil-translate" in text else "left"
    if slot == "pre":
        return "left"           # jianpu-ly's own pre words print before the head
    if slot == "after" or "\\breathe" in text:
        return "right"
    if symbol.category in ("dynamic", "pedal") or text.startswith("_") or "_\\markup" in text:
        return "below"
    if "\\postscript" in text and "##f" in text and "with-dimensions #'(0 . 0)" in text:
        xs = postscript_x(text)
        if max(xs) < 0:
            return "left"
        if min(xs) > DIGIT_WIDTH:
            return "right"
    return "above"


def point_marks():
    return [s for s in symbols.SYMBOLS.values() if s.kind in ("point", "pre") and s.storage == "marks"]


@pytest.mark.parametrize("sid", [s.id for s in point_marks()])
def test_canvas_position_and_printed_side_come_from_the_same_registry_row(sid):
    symbol = symbols.SYMBOLS[sid]
    if symbol.id.startswith("arp."):            # jianpu-ly arpeggio words print left of the head
        assert glyphs.position(symbol) == "left"
        return
    assert glyphs.position(symbol) == print_side(symbol), (sid, symbol.export)


def draw(sid, size=60) -> QImage:
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(0)
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    glyphs.draw_mark(p, symbols.SYMBOLS[sid], size / 2, size / 2, 2.0, QColor("black"))
    p.end()
    return image


def ink(image: QImage) -> tuple[int, int, int]:
    """(width, height, dark pixels) of what was drawn."""
    xs, ys, count = [], [], 0
    for y in range(image.height()):
        for x in range(image.width()):
            if image.pixelColor(x, y).alpha() > 128:
                xs.append(x)
                ys.append(y)
                count += 1
    return (max(xs) - min(xs) + 1, max(ys) - min(ys) + 1, count) if xs else (0, 0, 0)


def test_mo_and_tan_are_drawn_strokes_not_the_dot_like_glyph(qapp):
    for sid in ("gz.mo", "pipa.tan"):
        symbol = symbols.SYMBOLS[sid]
        assert symbol.canvas == "shape:backslash" and "丶" not in macro(symbol)
        assert "\\postscript" in macro(symbol) and print_side(symbol) == "above"
        width, height, _ = ink(draw(sid))
        assert height > 15 and width > 8                     # a clear slanted stroke, not a dot
    assert not symbols.SYMBOLS["pipa.tan"].hidden


def test_tenuto_and_finger_one_are_distinguishable(qapp):
    tenuto, finger = symbols.SYMBOLS["art.tenuto"], symbols.SYMBOLS["finger.1"]
    assert tenuto.canvas != finger.canvas and finger.export == "post:Fr=1"
    assert macro(tenuto) and "\\tenuto" in macro(tenuto) and "setlinewidth" in macro(tenuto)
    t_width, t_height, _ = ink(draw("art.tenuto"))
    f_width, f_height, _ = ink(draw("finger.1"))
    assert t_width < f_width * 0.8 and t_height >= 4          # short and thick vs the long thin 一


@pytest.mark.parametrize("sid, count", [("trem.1", 1), ("trem.2", 2), ("trem.3", 3), ("trem.4", 4),
                                        ("erhu.trem_bow", 3)])
def test_tremolo_strokes_match_on_screen_and_in_print_and_do_not_claim_playback(sid, count):
    symbol = symbols.SYMBOLS[sid]
    assert symbol.canvas == f"shape:tremolo({count})"
    text = macro(symbol)
    assert text.count("moveto") == count and print_side(symbol) == glyphs.position(symbol) == "below"
    assert symbols_ui.silent(symbol)                         # MIDI holds one note: 試聽不表現
    xs = postscript_x(text)
    assert 0 <= min(xs) and max(xs) <= DIGIT_WIDTH          # under the digit, not into the next number


def test_slides_print_where_the_usage_text_says():
    up, down = symbols.SYMBOLS["slide.up"], symbols.SYMBOLS["slide.down"]
    assert print_side(up) == glyphs.position(up) == "left"          # 滑進：before the note
    assert print_side(down) == glyphs.position(down) == "right"     # 往下滑出：after the note
    assert "curveto" in macro(up) and "curveto" in macro(down)       # small curved arrows
    assert max(postscript_x(macro(up))) - min(postscript_x(macro(up))) < 1.6
    assert symbols_ui.usage("slide.down").startswith("由本音往下滑")


def test_names_and_groups_from_the_review():
    names = {sid: symbols.SYMBOLS[sid].name for sid in
             ("caesura", "orn.reverseturn", "nav.segno_sign", "nav.segno", "volta.1", "volta.final", "line.tie",
              "gz.chan")}
    assert names == {"caesura": "停頓記號 //", "orn.reverseturn": "逆迴音", "nav.segno_sign": "Segno 𝄋（僅符號）",
                     "nav.segno": "Segno 記號 𝄋（D.S. 跳回處）", "volta.1": "第一結尾（1.）",
                     "volta.final": "最後結尾（結）", "line.tie": "連結線（延音線）", "gz.chan": "顫音（左手按揉）"}
    erhu = {s.id for s in symbols_ui.palette_symbols() if symbols_ui.group_matches(s, "erhu")}
    assert "finger.5" not in erhu and not erhu & {f"string.{k}" for k in range(1, 6)}
    assert {"pos.1", "pos.5", "slide.back_up", "slide.back_down", "dyn.pppp", "dyn.ffff", "tune.up"} <= erhu
    fingering = {s.id for s in symbols.symbols(category="fingering")}
    assert {"erhu.inner", "erhu.outer", "bow.up", "bow.down"} <= fingering
    assert glyphs.layer(symbols.SYMBOLS["bow.up"]) == glyphs.LAYER_BOW          # still stacked outside 指法
    # print order outward: Fingering (一), then text scripts (內, 一把) -- the canvas stacks the same way
    assert glyphs.layer(symbols.SYMBOLS["finger.1"]) < glyphs.layer(symbols.SYMBOLS["erhu.inner"])
    assert glyphs.layer(symbols.SYMBOLS["finger.1"]) < glyphs.layer(symbols.SYMBOLS["pos.1"])


def test_old_files_keep_opening():
    note = Note(5, marks=("slide.back", "finger.5", "string.1"))
    score = Score(notes=[note, Note(5), Note(5), Note(5)])
    score.validate()
    assert "\\jsSlideBackUp" in score.to_jianpu() and "slide=" not in score.to_jianpu()


ERHU_D_FIRST_POSITION = {  # 1=D, strings D–A: (degree, accidental, octave) -> finger
    (1, "", 0): 0, (2, "", 0): 1, (3, "", 0): 2, (4, "", 0): 3, (4, "#", 0): 3,
    (5, "", 0): 0, (6, "", 0): 1, (7, "b", 0): 2, (7, "", 0): 2, (1, "", 1): 3, (2, "", 1): 4,
}


def test_duet_example_fingerings_are_right_for_a_d_erhu():
    project = load_project(ROOT / "examples/duet.jps")
    assert project.score.key == "D"
    marked = 0
    for part in project.score.parts:
        for note in part.notes:
            assert note.degree == 0 or (note.degree, note.octave) >= (1, 0), "below the open inner string"
            for mark in note.marks:
                if mark.startswith("finger."):
                    marked += 1
                    assert ERHU_D_FIRST_POSITION[(note.degree, note.accidental, note.octave)] == int(mark[7:])
            assert not {"nav.coda_sign", "nav.tocoda"} & set(note.bar_start + note.bar_end)
    assert marked >= 6


@pytest.mark.integration
def test_round4_marks_print_with_real_lilypond(tmp_path):
    tool = find_tool("lilypond")
    if not tool:
        pytest.skip("JIANPU_LILYPOND not configured")
    ids = ["gz.mo", "pipa.tan", "slide.up", "slide.down", "trem.1", "trem.2", "trem.3", "trem.4", "erhu.trem_bow",
           "art.tenuto", "caesura", "erhu.vibrato", "breath.v", "slide.back_up", "slide.back_down", "tune.up",
           "tune.down", "pos.1", "pos.2", "pos.3", "pos.4", "pos.5", "dyn.pppp", "dyn.ffff", "slide.back"]
    notes = [Note(5, octave=i % 2, marks=(sid,)) for i, sid in enumerate(ids)]
    notes += [Note(5)] * (-len(notes) % 4)
    result = render(Project(Score(parts=[Part("", notes)], key="D")).notation(), tmp_path / "round 4", tool.resolve())
    assert result.pdf.is_file()
    assert not re.search(r"warning|error", result.log, re.I), result.log[-3000:]


@pytest.mark.integration
def test_pppp_and_ffff_extend_the_midi_dynamic_range(tmp_path):
    from test_notation_v2 import read_midi
    tool = find_tool("lilypond")
    if not tool:
        pytest.skip("JIANPU_LILYPOND not configured")
    notes = [Note(5, marks=(sid,)) for sid in ("dyn.pppp", "dyn.ppp", "dyn.fff", "dyn.ffff")]
    result = render(Project(Score(parts=[Part("", notes)], key="D")).notation(), tmp_path / "dyn", tool.resolve())
    velocities = [v for _, v in read_midi(result.midi)[0][0]]
    assert velocities[0] < velocities[1] < velocities[2] < velocities[3]
