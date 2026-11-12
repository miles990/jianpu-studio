"""Verify new visual notation through the real converter and LilyPond outputs."""

import struct
from fractions import Fraction

import pytest

from jianpu_studio.engine import find_tool, render, validate_source
from jianpu_studio.model import Note, Score


@pytest.fixture
def lilypond():
    tool = find_tool("lilypond")
    if not tool:
        pytest.skip("JIANPU_LILYPOND not configured")
    return tool.resolve()


def midi_note_onsets(path):
    """Read SMF note-on events, including running status and metadata lengths."""
    data = path.read_bytes()
    assert data[:4] == b"MThd"
    header_size, _, tracks, division = struct.unpack(">IHHH", data[4:14])
    assert header_size == 6 and 0 < division < 0x8000
    offset, notes = 14, []

    def variable_length(payload, position):
        value = 0
        while True:
            byte = payload[position]
            position += 1
            value = (value << 7) | (byte & 0x7f)
            if not byte & 0x80:
                return value, position

    for _ in range(tracks):
        assert data[offset:offset+4] == b"MTrk"
        size = struct.unpack(">I", data[offset+4:offset+8])[0]
        track = data[offset+8:offset+8+size]
        offset += 8 + size
        position, tick, running_status = 0, 0, None
        while position < len(track):
            delta, position = variable_length(track, position)
            tick += delta
            status = track[position]
            if status & 0x80:
                position += 1
            else:
                status = running_status
                assert status is not None
            if status == 0xff:
                position += 1  # Metadata type.
                length, position = variable_length(track, position)
                position += length
            elif status in (0xf0, 0xf7):
                length, position = variable_length(track, position)
                position += length
            else:
                running_status = status
                command = status >> 4
                length = 1 if command in (0xc, 0xd) else 2
                payload = track[position:position+length]
                position += length
                if command == 0x9 and payload[1]:
                    notes.append((Fraction(tick, division), payload[0]))
        assert position == len(track)
    assert offset == len(data)
    return sorted(notes)


def assert_pdf_and_midi(result):
    assert result.pdf.read_bytes().startswith(b"%PDF")
    assert result.pdf.stat().st_size > 1000
    assert result.midi.read_bytes().startswith(b"MThd")
    assert "warning:" not in result.log.lower()


@pytest.mark.integration
def test_visual_triplet_durations_reach_real_pdf_and_midi(tmp_path, lilypond):
    notes, expected = [], []
    for bar, duration in enumerate((4, 8, 16, 32)):
        group = Score(notes=[
            Note(1, duration=duration, tuplet=3, tuplet_start=True),
            Note(2, duration=duration, tuplet=3),
            Note(3, duration=duration, tuplet=3, tuplet_end=True),
        ])
        group.fill_last_bar()
        notes.extend(group.notes)
        expected.extend((Fraction(bar * 4) + Fraction(index * 8, duration * 3), pitch)
                        for index, pitch in enumerate((60, 62, 64)))
    score = Score(title="三連音時值驗證", with_staff=True, notes=notes)
    result = render(score.to_jianpu(), (tmp_path / "三連音 輸出").resolve(), lilypond)
    assert_pdf_and_midi(result)
    assert midi_note_onsets(result.midi) == expected


@pytest.mark.integration
def test_natural_cancels_previous_sharp_in_real_midi(tmp_path, lilypond):
    score = Score(title="還原記號驗證", with_staff=True, notes=[
        Note(1, accidental="#"), Note(1, accidental="n"), Note(1), Note(1),
    ])
    result = render(score.to_jianpu(), tmp_path.resolve(), lilypond)
    assert_pdf_and_midi(result)
    assert midi_note_onsets(result.midi) == [(0, 61), (1, 60), (2, 60), (3, 60)]


@pytest.mark.integration
def test_new_expression_marks_reach_real_pdf_and_midi(tmp_path, lilypond):
    score = Score(title="常用演奏記號", with_staff=True, notes=[
        Note(1, dynamic="ppp", hairpin="crescendo"),
        Note(2, articulation="staccatissimo"),
        Note(3, articulation="harmonic", hairpin_end=True),
        Note(4, dynamic="fff", articulation="breathe"),
        Note(5, hairpin="diminuendo", articulation="marcato"),
        Note(4, articulation="turn"),
        Note(3, hairpin_end=True),
        Note(2, dynamic="p"),
    ])
    result = render(score.to_jianpu(), tmp_path.resolve(), lilypond)
    assert_pdf_and_midi(result)
    generated = result.lilypond.read_text(encoding="utf-8")
    for symbol in (r"\ppp", r"\fff", r"\<", r"\>", r"\!", r"\breathe",
                   r"\turn", r"\marcato", r"\staccatissimo", r"\flageolet"):
        assert symbol in generated
    assert len(midi_note_onsets(result.midi)) >= len(score.notes)


@pytest.mark.integration
def test_grace_notes_before_short_and_after_long_notes_reach_real_outputs(tmp_path, lilypond):
    score = Score(title="前後倚音驗證", with_staff=True, notes=[
        Note(1, grace_before="23"),
        Note(2, grace_after="34"),
        Note(3, duration=2, grace_after="45"),
    ])
    result = render(score.to_jianpu(), tmp_path.resolve(), lilypond)
    assert_pdf_and_midi(result)
    assert [pitch for _, pitch in midi_note_onsets(result.midi)] == [62, 64, 60, 62, 64, 65, 64, 65, 67]


@pytest.mark.integration
def test_grace_octaves_and_hairpin_inside_triplet_reach_real_outputs(tmp_path, lilypond):
    score = Score(title="倚音八度與三連音", with_staff=True, notes=[
        Note(1, duration=8, tuplet=3, tuplet_start=True, grace_before="23",
             dynamic="ppp", hairpin="crescendo"),
        Note(2, duration=8, tuplet=3, grace_after="#4'5"),
        Note(3, duration=8, tuplet=3, tuplet_end=True, hairpin_end=True),
        Note(4, duration=2, dotted=True),
    ])
    result = render(score.to_jianpu(), tmp_path.resolve(), lilypond)
    assert_pdf_and_midi(result)
    assert [pitch for _, pitch in midi_note_onsets(result.midi)] == [62, 64, 60, 62, 78, 67, 64, 65]


@pytest.mark.integration
def test_grace_accidentals_and_octaves_stay_with_the_correct_note(tmp_path, lilypond):
    score = Score(title="倚音升降與八度", with_staff=True, notes=[
        Note(1, grace_before="1#2'"),
        Note(2, grace_before="b7''#1,"),
        Note(3), Note(4),
    ])
    result = render(score.to_jianpu(), tmp_path.resolve(), lilypond)
    assert_pdf_and_midi(result)
    assert [pitch for _, pitch in midi_note_onsets(result.midi)] == [60, 75, 60, 94, 49, 62, 64, 65]


@pytest.mark.integration
def test_after_grace_keeps_supported_slide_and_breath_marks(tmp_path, lilypond):
    score = Score(title="後倚音滑音與換氣", with_staff=True, notes=[
        Note(1, slide="up", grace_after="23", articulation="breathe"),
        Note(2), Note(3), Note(4),
    ])
    result = render(score.to_jianpu(), tmp_path.resolve(), lilypond)
    assert_pdf_and_midi(result)
    assert [pitch for _, pitch in midi_note_onsets(result.midi)] == [60, 62, 64, 62, 64, 65]


@pytest.mark.parametrize("command", [r"\flageoletUnsafe", r"\staccatissimoUnsafe", r"\include", r"\applyContext"])
def test_expression_whitelist_does_not_allow_arbitrary_commands(command):
    with pytest.raises(ValueError, match="不接受 LilyPond 指令"):
        validate_source("1=C 4/4 1 2 3 4 " + command)
