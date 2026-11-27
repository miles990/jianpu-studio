"""Review 2 #9: unchanged older source-mode projects must still open (and recover) as editable.

Every ``source`` below is byte-for-byte what an older release wrote for the stored score:
v1 texts come from the 0.3.1 ``Score.to_jianpu`` (0.1.3 and 0.2.1 write the same words for
these scores apart from not replacing braces), v2 texts from the 0.4 development export just
before whole-bar rests were spelled ``0 0 0 0``.
"""

import json

import pytest
from PySide6.QtWidgets import QMessageBox

from jianpu_studio import migrate
from jianpu_studio.app import MainWindow
from jianpu_studio.model import Note, Project, Score, load_project

REST_SOURCE = "title=休止\ncomposer=\nNoIndent\n1=C 4/4 4=96\n0 - - -\n1 - 0 -\n0 - - c2\n"
REST_NOTES = [{"degree": 0, "duration": 1}, {"degree": 1, "duration": 2}, {"degree": 0, "duration": 2},
              {"degree": 0, "duration": 2, "dotted": True}, {"degree": 2, "duration": 4}]


def v1(source, notes, **score):
    return {"format": "jianpu-studio", "version": 1, "mode": "source", "source": source,
            "score": {"title": "未命名樂曲", "notes": notes, **score}}


def v2(source, parts, **score):
    return {"format": "jianpu-studio", "version": 2, "mode": "source", "source": source,
            "score": {"title": "未命名樂曲", "parts": parts, **score}}


OLD_PROJECTS = {
    "v1 rests": v1(REST_SOURCE, REST_NOTES, title="休止"),
    "v2 rests": v2(REST_SOURCE, [{"name": "", "lyrics": "", "notes": REST_NOTES}], title="休止"),
    "v1 rest marks": v1(
        "title=未命名樂曲\ncomposer=\nNoIndent\n1=C 4/4 4=96\nR{ 0 \\fermata - - -\n0 - - c3 Fr=0 }\n",
        [{"degree": 0, "duration": 1, "articulation": "fermata", "repeat_start": True},
         {"degree": 0, "duration": 2, "dotted": True}, {"degree": 3, "duration": 4, "finger": 0, "repeat_end": True}]),
    "v1 text": v1(
        "title=二泉映月（片段）' 甲'  / ｛乙｝ 50％ $x #(y)\ncomposer=阿炳：NextPart\nNoIndent\n"
        "1=C 4/4 4=96\n1 - - -\n0 - - -\nH: 小，星2 星 -_亮*\n",
        [{"degree": 1, "duration": 1}, {"degree": 0, "duration": 1}],
        title='二泉映月（片段）"甲" \\ {乙} 50% $x #(y)', composer="阿炳：NextPart", lyrics="小，星2 星 -_亮*"),
    "v2 catalog": v2(
        "title=未命名樂曲\ncomposer=\nNoIndent\n1=C 4/4 4=96\nc1/// 2/// - slide=⌒ c3\n0 \\fermata - - -\n"
        "slideUp 5 \\tenuto - 0 -\n",
        [{"name": "", "lyrics": "", "notes": [
            {"degree": 1, "marks": ["trem.3"]}, {"degree": 2, "duration": 2, "marks": ["erhu.trem_bow"]},
            {"degree": 3, "marks": ["slide.back"]}, {"degree": 0, "duration": 1, "marks": ["art.fermata"]},
            {"degree": 5, "duration": 2, "marks": ["art.tenuto", "slide.up"]}, {"degree": 0, "duration": 2}]}]),
    "v2 parts": v2(
        "title=標題”引號”(括號)﹛﹜%\ncomposer=\nNoIndent\ninstrument=二胡 ﹟(1)\n1=C 4/4 4=96\n1 - - -\n"
        "H: _\"春2\" 天, 來\nNextPart\ninstrument=高胡”乙”\n1=C 4/4\n0 - - -\n",
        [{"name": "二胡 #(1)", "lyrics": "春2 天，來", "notes": [{"degree": 1, "duration": 1}]},
         {"name": "高胡\"乙\"", "lyrics": "", "notes": [{"degree": 0, "duration": 1}]}],
        title="標題\"引號\"（括號）{}%"),
}


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.mark.parametrize("name", OLD_PROJECTS)
def test_unchanged_older_source_projects_become_editable(tmp_path, name):
    project = load_project(write(tmp_path / "舊 專案.jps", OLD_PROJECTS[name]))
    assert project.mode == "source" and project.source.strip() != project.score.to_jianpu().strip()
    visual = MainWindow.prepare_visual_project(project)
    assert visual.mode == "visual" and visual.score == project.score


@pytest.mark.parametrize("old, new", [
    ("1=C 4/4\n0 - - -", "1=C 4/4\n1 - - -"),                       # a rest is not a note
    ("1=C 4/4\n1 - - -", "1=C 4/4\n0 0 0 0"),                       # a long note is not a rest
    ("1=C 4/4\n0 - - - 1", "1=C 4/4\n0 0 0 0"),                     # an extra note
    ("1=C 4/4\n0 - \\fermata - -", "1=C 4/4\n0 \\fermata 0 0 0"),   # mark on another beat
    ("1=C 4/4\nc1///", "1=C 4/4\nc1"),                              # tremolo only on one side
    ("title=甲乙\n1=C 4/4\nc1", "title=甲丙\n1=C 4/4\nc1"),          # different title
    ("title=a' b\n1=C 4/4\nc1", "title=a b\n1=C 4/4\nc1"),           # quote vs no quote
    ("1=C 4/4\nc1\nH: ab cd", "1=C 4/4\nc1\nH: abcd"),              # different syllables
    ("1=C 4/4\nc1\nH: 春2", "1=C 4/4\nc1\nH: _\"春3\""),             # different lyric
    ("title=a\n1=C 4/4\nc1", "composer=a\n1=C 4/4\nc1"),            # different field
])
def test_genuinely_different_sources_are_still_refused(old, new):
    assert not migrate.same_music_text(old, new)


def test_edited_older_source_is_still_refused(tmp_path):
    data = OLD_PROJECTS["v1 rests"]
    edited = dict(data, source=data["source"].replace("1 - 0 -", "1 - 2 -"))
    with pytest.raises(ValueError, match="未載入"):
        MainWindow.prepare_visual_project(load_project(write(tmp_path / "改過.jps", edited)))


@pytest.fixture
def window(qtbot, monkeypatch, tmp_path):
    errors = []
    monkeypatch.setattr(MainWindow, "render_score", lambda *a: None)
    monkeypatch.setattr(MainWindow, "error", lambda self, title, error: errors.append((title, str(error))))
    widget = MainWindow(recovery=False)
    widget.errors = errors
    widget.recovery_path = tmp_path / "recovery.jps"
    widget.autosave.stop()
    qtbot.addWidget(widget, before_close_func=lambda w: setattr(w, "dirty", False))
    widget.reset(Project(Score(notes=[Note(5, duration=1)])))
    return widget


@pytest.mark.parametrize("name", ["v1 rests", "v2 rests"])
def test_older_long_rest_recovery_is_restored(window, monkeypatch, name):
    write(window.recovery_path, OLD_PROJECTS[name])
    data = window.recovery_path.read_bytes()
    expected = load_project(window.recovery_path).score
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    assert window.restore_recovery()
    assert window.project.mode == "visual" and window.project.score == expected and window.dirty
    assert window.recovery_path.read_bytes() == data
    assert not list(window.recovery_path.parent.glob("recovery-legacy-*.jps"))
    assert not window.errors
