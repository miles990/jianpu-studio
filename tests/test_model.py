import json
from fractions import Fraction

import pytest

from jianpu_studio.model import Note, Project, Score, demo_project, load_project, save_project


def test_demo_and_unicode_roundtrip(tmp_path):
    project = demo_project()
    assert project.score.rhythm_error() is None
    assert "title=小星星" in project.notation()
    path = tmp_path / "中文資料夾" / "我的樂譜.jps"
    save_project(path, project)
    assert load_project(path) == project


def test_rhythm_crossing_and_fill():
    score = Score(notes=[Note(1), Note(2, duration=8)])
    assert "還缺 5/2 拍" in score.rhythm_error()
    score.fill_last_bar()
    assert sum(n.beats for n in score.notes) == 4
    assert score.rhythm_error() is None
    score = Score(notes=[Note(1, duration=2, dotted=True), Note(2, duration=2)])
    assert "第 2 個音符超出" in score.rhythm_error()


@pytest.mark.parametrize("note,token", [
    (Note(1, 1, 8, True, "#"), "q#1'."),
    (Note(0, duration=1), "0 - - -"),
    (Note(6, -1, 2, True), "6, - -"),
    (Note(2, duration=32), "d2"),
])
def test_tokens(note, token):
    assert note.token() == token


def test_fraction_precision():
    score = Score(meter="6/8", notes=[Note(1, duration=8, dotted=True)] * 4)
    assert score.bar_length == Fraction(3)
    assert score.rhythm_error() is None


@pytest.mark.parametrize("mutation", [
    lambda d: d.update(version=999),
    lambda d: d["score"].update(tempo="fast"),
    lambda d: d["score"]["notes"][0].update(degree=12),
    lambda d: d["score"].update(title="bad\nLP: text"),
    lambda d: d.update(mode="bad"),
])
def test_invalid_project_rejected(mutation, tmp_path):
    data = demo_project().to_dict()
    mutation(data)
    path = tmp_path / "bad.jps"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        load_project(path)


def test_empty_cannot_render():
    with pytest.raises(ValueError, match="先加入"):
        Project().notation()
