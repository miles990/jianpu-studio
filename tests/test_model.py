import json
from dataclasses import FrozenInstanceError, replace
from fractions import Fraction

import pytest

from jianpu_studio.model import (
    TICKS_PER_QUARTER,
    Note,
    Project,
    Score,
    demo_project,
    load_project,
    save_project,
)


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


def test_version_one_project_without_new_fields_still_loads():
    project = Project.from_dict({
        "format": "jianpu-studio", "version": 1,
        "score": {"title": "舊版", "notes": [{"degree": 3, "duration": 1}]},
    })
    assert project.score.paper_size == "a4"
    assert project.score.notes == [Note(3, duration=1)]
    assert Project.from_dict(project.to_dict()) == project


def test_snapshot_shares_only_immutable_notes():
    project = demo_project()
    snapshot = project.snapshot()
    assert snapshot.score.notes is not project.score.notes
    assert snapshot.score.notes[0] is project.score.notes[0]
    project.score.notes[0] = replace(project.score.notes[0], degree=7)
    project.score.title = "changed"
    assert snapshot.score.notes[0].degree == 1
    assert snapshot.score.title == "小星星"
    with pytest.raises(FrozenInstanceError):
        snapshot.score.notes[0].degree = 5


@pytest.mark.parametrize("changes", [
    {"finger": True}, {"finger": 5}, {"bow": "unknown"}, {"dynamic": "ffff"},
    {"articulation": "other"}, {"slide": "sideways"}, {"slur_start": 1},
    {"degree": 0, "finger": 0}, {"degree": 0, "bow": "upbow"},
    {"degree": 0, "slide": "up"}, {"degree": 0, "tie": True},
])
def test_invalid_performance_marks_are_rejected(changes):
    with pytest.raises(ValueError):
        Note(**changes).validate()


def test_long_note_marks_attach_to_number_and_tie_follows_dashes():
    note = Note(2, duration=2, finger=3, bow="downbow", dynamic="mf", slide="up", slur_start=True, tie=True)
    assert note.notation_token() == r"slideUp 2 Fr=3 \downbow \mf ( - ~"
    assert Note(3, duration=2, slur_end=True, repeat_end=True).notation_token() == "3 ) - }"


def test_complete_erhu_marks_and_page_settings_roundtrip():
    score = Score(notes=[Note(1, finger=0, bow="downbow", dynamic="p", slide="up",
                              slur_start=True, repeat_start=True),
                         Note(2, finger=1, bow="upbow", articulation="staccato", slur_end=True),
                         Note(3, tie=True), Note(3, repeat_end=True)],
                  paper_size="letter", staff_size=24, bars_per_line=2)
    assert score.notation_error() is None
    assert "R{ slideUp c1 Fr=0" in score.to_jianpu()
    project = Project(score)
    assert Project.from_dict(project.to_dict()) == project


@pytest.mark.parametrize("notes,message", [
    ([Note(1, slur_end=True)], "缺少起點"),
    ([Note(1, slur_start=True), Note(2, slur_start=True)], "重疊連弓"),
    ([Note(1, slur_start=True)], "缺少終點"),
    ([Note(1, tie=True), Note(2)], "相同音高"),
    ([Note(1, tie=True)], "相同音高"),
    ([Note(1), Note(2, repeat_start=True)], "小節開頭"),
    ([Note(1, repeat_start=True, repeat_end=True)], "完整小節末"),
    ([Note(1, duration=1, repeat_end=True)], "需要起點"),
    ([Note(1, duration=1, repeat_start=True)], "缺少終點"),
])
def test_invalid_connections_and_repeats(notes, message):
    assert message in Score(notes=notes).notation_error()


def test_fill_after_dotted_32nd_is_exact_and_failure_does_not_mutate():
    score = Score(notes=[Note(1, duration=32, dotted=True)])
    score.fill_last_bar()
    assert score.rhythm_error() is None
    assert sum(n.ticks for n in score.notes) == 4*TICKS_PER_QUARTER
    impossible = Score(notes=[Note(1, duration=32, dotted=True)] * 21)
    original = list(impossible.notes)
    with pytest.raises(ValueError, match="剩餘拍數"):
        impossible.fill_last_bar()
    assert impossible.notes == original


def test_literal_metadata_cannot_become_source_lines():
    score = Score(title='曲名 {a} "b" \\ c %', notes=[Note(duration=1)])
    source = score.to_jianpu()
    assert "title=曲名 ｛a｝ ' b'  / c ％" in source
    with pytest.raises(ValueError, match="單行"):
        replace(score, title="曲名\u2028LP: bad").validate()
