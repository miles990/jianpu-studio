from dataclasses import replace
from fractions import Fraction

import pytest

from jianpu_studio.measures import (
    clear_measures,
    delete_measures,
    duplicate_measures,
    insert_measures,
    measure_ranges,
    replace_rest_input,
)
from jianpu_studio.model import TICKS_PER_QUARTER, Note, Project, Score, tuplet_ranges


def triplet(duration=8, degrees=(1, 2, 3)):
    return [Note(degree, duration=duration, tuplet=3, tuplet_start=i == 0, tuplet_end=i == 2)
            for i, degree in enumerate(degrees)]


def applied(score, result):
    return replace(score, notes=result.notes)


@pytest.mark.parametrize("duration", (4, 8, 16, 32))
def test_triplets_have_exact_nominal_two_note_duration(duration):
    group = triplet(duration)
    assert all(type(n.ticks) is int for n in group)
    assert sum(n.beats for n in group) == Fraction(8, duration)
    assert sum(n.ticks for n in group) == 2*Note(duration=duration).ticks
    assert tuplet_ranges(group) == [(0, 3)]
    score = Score(notes=group)
    score.fill_last_bar()
    assert sum(n.ticks for n in score.notes) == 4*TICKS_PER_QUARTER
    assert score.rhythm_error() is None
    assert score.notation_error() is None
    source = score.to_jianpu()
    assert source.count("3[") == 1
    assert source.count("]") == 1


def test_triplets_and_dotted_32nds_share_an_exact_integer_timebase():
    notes = triplet(32) + [Note(4, duration=32, dotted=True)] * 20
    score = Score(notes=notes)
    assert Note(duration=32, dotted=True).ticks == 9
    assert sum(n.ticks for n in notes) == 192
    assert score.rhythm_error() is None
    assert score.to_jianpu()


@pytest.mark.parametrize("notes", [
    triplet()[:2], triplet()[1:], [replace(triplet()[0], tuplet_start=False)] + triplet()[1:],
    triplet()[:2] + [replace(triplet()[2], tuplet_end=False)],
    triplet()[:1] + [Note(2, duration=8)] + triplet()[2:],
    triplet()[:1] + [replace(triplet()[1], duration=16)] + triplet()[2:],
    [replace(n, duration=2) for n in triplet()],
    [replace(n, dotted=True) for n in triplet()],
])
def test_incomplete_or_unsupported_tuplet_groups_rejected(notes):
    with pytest.raises(ValueError, match="三連音"):
        tuplet_ranges(notes)
    with pytest.raises(ValueError, match="三連音"):
        Score(notes=notes).validate()


def test_complete_group_crossing_a_bar_is_rejected():
    score = Score(notes=[Note(4, duration=2, dotted=True), Note(5, duration=8)] + triplet())
    assert score.rhythm_error() is not None
    assert "三連音跨越小節" in score.notation_error()


def test_successive_complete_triplets_fill_a_bar_without_rounding():
    score = Score(notes=triplet() + triplet() + triplet() + triplet())
    assert score.notation_error() is None
    assert score.rhythm_error() is None
    assert tuplet_ranges(score.notes) == [(0, 3), (3, 6), (6, 9), (9, 12)]


@pytest.mark.parametrize("changes", [
    {"tuplet": True}, {"tuplet": 5}, {"tuplet_start": True}, {"tuplet_end": 1},
    {"hairpin": "sideways"}, {"hairpin_end": 1}, {"tuplet": 3, "dotted": True},
    {"tuplet": 3, "duration": 1},
])
def test_invalid_notation_fields_rejected(changes):
    with pytest.raises(ValueError):
        Note(**changes).validate()


def test_hairpin_tokens_and_shared_end_start():
    notes = [Note(1, hairpin="crescendo", dynamic="ppp"),
             Note(2, hairpin_end=True, hairpin="diminuendo", dynamic="fff"),
             Note(3, hairpin_end=True), Note(4)]
    score = Score(notes=notes)
    assert score.notation_error() is None
    assert notes[0].notation_token() == r"c1 \ppp \<"
    assert notes[1].notation_token() == r"c2 \fff \! \>"
    assert r"c3 \!" in score.to_jianpu()


@pytest.mark.parametrize("notes,message", [
    ([Note(hairpin_end=True)], "缺少起點"),
    ([Note(hairpin="crescendo")], "缺少終點"),
    ([Note(hairpin="crescendo"), Note(hairpin="diminuendo")], "重疊"),
    ([Note(hairpin="crescendo"), Note(dynamic="mf"), Note(hairpin_end=True)], "力度會中斷"),
    ([Note(hairpin="crescendo", hairpin_end=True)], "缺少起點"),
])
def test_invalid_hairpins_have_actionable_errors(notes, message):
    assert message in Score(notes=notes).notation_error()


def test_natural_matches_unaltered_note_for_tie_and_source_pitch():
    score = Score(notes=[Note(1, accidental="#"), Note(1, accidental="n", tie=True), Note(1), Note(2)])
    assert score.notation_error() is None
    assert score.notes[1].token() == "c1"
    assert r"c#1 c1 ~ c1 c2" in score.to_jianpu()


@pytest.mark.parametrize("mark,token", [
    ("breathe", r"\breathe"), ("turn", r"\turn"), ("marcato", r"\marcato"),
    ("staccatissimo", r"\staccatissimo"), ("harmonic", r"\flageolet"),
])
def test_basic_articulations_emit_supported_lilypond_symbols(mark, token):
    note = Note(1, articulation=mark)
    note.validate()
    assert token in note.notation_token()


def test_breath_after_long_note_follows_its_complete_duration():
    assert Note(1, duration=2, articulation="breathe").notation_token() == r"1 - \breathe"


@pytest.mark.parametrize("grace,source", (("23", "23"), ("#4'5", "#'45"), ("2,,3", ",,23"),
                                          ("b7''#1,", "b''7#,1"), ("1#2'", "1#'2"), ("12345671", "12345671")))
def test_grace_is_zero_time_and_emits_before_and_after_tokens(grace, source):
    plain = Note(1, duration=2)
    ornamented = replace(plain, grace_before=grace, grace_after=grace)
    assert ornamented.ticks == plain.ticks
    assert ornamented.notation_token() == f"g[{source}] 1 - [{source}]g"


def test_grace_declares_prefix_octaves_without_changing_regular_note_octaves():
    plain = Score(notes=[Note(1, octave=1, duration=1)])
    assert "OctavesBefore" not in plain.to_jianpu()
    score = replace(plain, notes=[replace(plain.notes[0], grace_before="1#2'", grace_after="b7''#1,")])
    source = score.to_jianpu()
    assert "OctavesBefore\n" in source
    assert "g[1#'2] 1' - - - [b''7#,1]g" in source


@pytest.mark.parametrize("grace", ("0", "8", "123456712", "1'''", "2,,,", "1',", "1 2", "1] LP:", "\\include", 1, None))
def test_grace_validation_rejects_unsupported_notes_and_source_injection(grace):
    with pytest.raises(ValueError, match="倚音"):
        Note(grace_before=grace).validate()
    with pytest.raises(ValueError, match="倚音"):
        Note(grace_after=grace).validate()


def test_rest_cannot_have_grace_notes():
    with pytest.raises(ValueError, match="休止符不能加倚音"):
        Note(0, grace_before="23").validate()


@pytest.mark.parametrize("changes", [
    {"finger": 0}, {"bow": "downbow"}, {"dynamic": "p"}, {"articulation": "staccato"},
    {"articulation": "fermata"}, {"articulation": "harmonic"}, {"slur_start": True},
    {"slur_end": True}, {"tie": True}, {"hairpin": "crescendo"}, {"hairpin_end": True},
])
def test_after_grace_rejects_upstream_detached_or_misattached_events(changes):
    with pytest.raises(ValueError, match="後倚音目前不能"):
        Note(grace_after="23", **changes).validate()
    # Before-grace notes do not trigger the upstream afterGrace wrapping bug.
    Note(grace_before="23", **changes).validate()


def test_after_grace_keeps_safe_slide_and_breath_after_the_ornament():
    note = Note(grace_after="23", slide="up", articulation="breathe")
    assert note.notation_token() == r"slideUp c1 [23]g \breathe"


def test_new_notation_fields_roundtrip_and_old_projects_still_load():
    notes = triplet()
    notes[0] = replace(notes[0], hairpin="crescendo", grace_before="23", dynamic="ppp")
    notes[1] = replace(notes[1], grace_after="#4'5")
    notes[-1] = replace(notes[-1], hairpin_end=True, articulation="harmonic")
    project = Project(Score(notes=notes + [Note(4, duration=2, dotted=True)]))
    assert Project.from_dict(project.to_dict()) == project
    assert project.notation()
    old = {"format": "jianpu-studio", "version": 1, "score": {"notes": [{"degree": 1, "duration": 1}]}}
    assert Project.from_dict(old).score.notes == [Note(1, duration=1)]


def test_partial_triplet_delete_copy_and_duration_edit_cannot_validate():
    score = Score(notes=triplet() + [Note(4, duration=2, dotted=True)])
    with pytest.raises(ValueError, match="三連音"):
        replace(score, notes=score.notes[1:]).validate()
    with pytest.raises(ValueError, match="三連音"):
        replace(score, notes=score.notes + [score.notes[0]]).validate()
    changed = list(score.notes)
    changed[1] = replace(changed[1], duration=16)
    with pytest.raises(ValueError, match="三連音"):
        replace(score, notes=changed).validate()
    changed[1] = replace(score.notes[1], degree=7, finger=3)
    replace(score, notes=changed).validate()


def test_measure_copy_preserves_whole_triplets_hairpins_and_grace():
    notes = triplet()
    notes[0] = replace(notes[0], grace_before="23", hairpin="crescendo")
    notes[1] = replace(notes[1], grace_after="45")
    notes[2] = replace(notes[2], hairpin_end=True)
    score = Score(notes=notes + [Note(4, duration=2, dotted=True)])
    duplicate = duplicate_measures(score, [0])
    result = applied(score, duplicate)
    assert tuplet_ranges(result.notes) == [(0, 3), (4, 7)]
    assert result.notes[:4] == result.notes[4:]
    assert result.notation_error() is None
    assert result.to_jianpu().count("3[") == 2
    inserted = applied(result, insert_measures(result, 1))
    assert len(measure_ranges(inserted)) == 3
    deleted = applied(inserted, delete_measures(inserted, [1]))
    assert deleted.notes == result.notes
    cleared = applied(result, clear_measures(result, [0]))
    assert len(tuplet_ranges(cleared.notes)) == 1
    assert cleared.to_jianpu()


def test_edit_through_hairpin_removes_both_ends_without_losing_other_marks():
    notes = [Note(1, duration=1, hairpin="crescendo"), Note(2, duration=1),
             Note(3, duration=1, hairpin_end=True), Note(4, duration=1, grace_before="23")]
    score = Score(notes=notes)
    result = clear_measures(score, [1])
    assert not any(n.hairpin or n.hairpin_end for n in result.notes)
    assert result.notes[-1].grace_before == "23"
    assert "漸強弱" in result.notice
    assert applied(score, result).to_jianpu()


def test_rest_input_within_triplet_preserves_group_and_exact_bar_length():
    score = Score(notes=triplet(degrees=(0, 0, 0)) + [Note(4, duration=2, dotted=True)])
    for row in range(3):
        result = replace_rest_input(score, row, Note(row+1, duration=8, grace_before="23"))
        score = applied(score, result)
    assert tuplet_ranges(score.notes) == [(0, 3)]
    assert [n.degree for n in score.notes[:3]] == [1, 2, 3]
    assert score.notes[0].tuplet_start and score.notes[2].tuplet_end
    assert sum(n.ticks for n in score.notes) == 4*TICKS_PER_QUARTER
    assert score.to_jianpu()


def test_rest_input_cannot_change_one_triplet_duration_or_create_partial_group():
    score = Score(notes=triplet(degrees=(0, 0, 0)) + [Note(4, duration=2, dotted=True)])
    with pytest.raises(ValueError, match="完整三連音群組"):
        replace_rest_input(score, 0, Note(1, duration=4))
    with pytest.raises(ValueError, match="完整的三個"):
        replace_rest_input(Score(notes=[Note(0, duration=1)]), 0, triplet()[0])
