"""User-entered text (title, composer, part names, lyrics) is literal (review P1.10/P1.11).

The converter normalizes fullwidth forms to ASCII *after* our checks and splits the input on
reserved words before it looks at headers or lyrics, so the export must neutralize what the
converter and LilyPond will actually read, not what the user typed.
"""
import random
import re
import string
from pathlib import Path

import pytest

from jianpu_studio.engine import find_tool, render, validate_source
from jianpu_studio.export import literal, upstream_normalized
from jianpu_studio.model import Note, Part, Project, Score
from jianpu_studio.vendor import jianpu_ly
from jianpu_studio.worker import part_count

FULLWIDTH = "".join(chr(c) for c in range(0xFF01, 0xFF5F))
ADVERSARIAL = [
    '練習曲「上」和＂下＂',                     # review: fullwidth quotes became real quotes
    'Study NextPart No. 1',                     # review: reserved word split the score
    "50% 完成",                                  # review: % started a comment in the lyric
    'x" } #(system "touch pwned") { "',
    "＂ ＼ ｛ ｝ ＃（ ＄ ％ ＜＜ ＞＞",
    "$(display 1) #{ c4 #} \\include \"/etc/passwd\" %{ %}",
    "LP: #(x) :LP LPH: H: L: title= instrument= chords=c",
    "NextScore PartMidi angka Indonesian Next​Part",
    "1 2. 3 春2 夏_3 *x .y =z |w -- __ _ ~ a- 4-",
    string.punctuation, FULLWIDTH[:40], FULLWIDTH[40:],
    "“引號”《書名》‘單’ 春“夏”",
]


def test_our_normalization_is_exactly_upstreams():
    for code in range(0x20, 0x10000):
        c = chr(code)
        if 0xD800 <= code < 0xE000:
            continue
        assert upstream_normalized(c) == jianpu_ly.fix_fullwidth(c), hex(code)


def _header_values(source):
    """Header values as the converter will read them."""
    out = []
    for line in source.splitlines():
        line = upstream_normalized(line).strip()
        if re.match(r"\s*[A-Za-z]+\s*=", line):
            out.append(line.split("=", 1)[1].strip())
    return out


def _lyric_lines(source):
    return [upstream_normalized(line)[2:] for line in source.splitlines() if line.startswith("H:")]


def _check_source(score):
    source = Project(score).notation()
    validate_source(source)                                  # nothing is refused as code
    normalized = upstream_normalized(source)
    assert len([p for p in re.split(r"\sNextPart\s", " " + normalized + " ") if p.strip()]) == len(score.parts)
    assert len(re.split(r"\sNextScore\s", " " + normalized + " ")) == 1
    assert not re.search(r"\sPartMidi\s|(\s|^)(angka|Indonesian)(\s|$)", " " + normalized + " ")
    assert part_count(source) == len(score.parts)
    for value in _header_values(source):
        assert '"' not in value and "\\" not in value      # stays inside its LilyPond string
    for lyric in _lyric_lines(source):
        unquoted = re.sub(r'_"[^"]*"', "", lyric)           # quoted syllables are strings
        assert not set('"\\{}%#$') & set(unquoted)
        assert not re.search(r"(^|\s)[*.=|]|\d", unquoted.replace("_", ""))
    return source


@pytest.mark.parametrize("text", ADVERSARIAL)
def test_adversarial_text_stays_text_in_every_field(text):
    notes = [Note(1), Note(2), Note(3), Note(4)]
    _check_source(Score(title=text[:200], composer=text[:200], lyrics=text, notes=notes))
    _check_source(Score(title=text[:200], parts=[Part(text[:40], notes, text), Part(text[-40:], notes, text)]))


def test_random_text_stays_text():
    rng = random.Random(7)
    alphabet = string.printable.replace("\n", "").replace("\r", "").replace("\x0b", "").replace("\x0c", "") \
        + FULLWIDTH + "春夏秋冬“”‘’《》、。" + "​"
    for _ in range(300):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 60))).replace("\t", " ")
        _check_source(Score(title=text, composer=text[::-1], lyrics=text,
                            parts=[Part(text[:40], [Note(5)] * 4, text), Part(text[-40:], [Note(1)] * 4, text)]))


def test_header_text_keeps_ordinary_punctuation_readable():
    assert literal("100% 練習 #1 (A) <B>") == "100% 練習 #1 (A) <B>"
    assert literal('「上」＂下＂ \\ {x}') == "「上」”下” ﹨ ﹛x﹜"
    assert literal("lyrics 春夏 50%", lyric=True) == 'lyrics 春 夏 _"50﹪"'


@pytest.mark.integration
@pytest.mark.parametrize("text", ADVERSARIAL)
def test_adversarial_text_renders_with_the_written_music(tmp_path, text, monkeypatch):
    """Real converter and LilyPond: no error, no warning, nothing executed, notes unchanged."""
    tool = find_tool("lilypond")
    if not tool:
        pytest.skip("JIANPU_LILYPOND not configured")
    from test_notation_engine import midi_note_onsets
    monkeypatch.chdir(tmp_path)
    first, second = [Note(1), Note(2), Note(3), Note(4)], [Note(5), Note(6), Note(7), Note(1, octave=1)]
    score = Score(title=text[:200], composer=text[:200],
                  parts=[Part(text[:40], first, text), Part(text[-40:], second, text)])
    result = render(_check_source(score), (tmp_path / "文字 測試").resolve(), tool.resolve())
    assert "warning" not in result.log.lower() and "error" not in result.log.lower(), result.log
    assert result.pdf.read_bytes().startswith(b"%PDF")
    assert midi_note_onsets(result.midi) == sorted([(i, p) for i, p in enumerate((60, 62, 64, 65))]
                                                   + [(i, p) for i, p in enumerate((67, 69, 71, 72))])
    assert not list(Path(tmp_path).rglob("pwned"))
