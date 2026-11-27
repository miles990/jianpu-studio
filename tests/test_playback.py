import ctypes
import sys
import time

import pytest

from jianpu_studio import playback
from jianpu_studio.engine import find_tool, render
from jianpu_studio.model import demo_project


class FakeAudioToolbox:
    """Records AudioToolbox calls; time advances with each GetTime query."""

    def __init__(self, fail=None, track_beats=(8.0, 12.0)):
        self.calls = []
        self.fail = fail or {}
        self.track_beats = track_beats
        self.now = 0.0
        self.handles = 0

    def __getattr__(self, name):
        def call(*args):
            self.calls.append(name)
            code = self.fail.get(name, 0)
            if code:
                return code
            handler = getattr(type(self), "_" + name, None)
            return (handler(self, *args) or 0) if handler else 0
        return call

    def _handle(self, pointer):
        self.handles += 1
        pointer._obj.value = self.handles

    def _NewMusicSequence(self, pointer):
        self._handle(pointer)

    def _NewMusicPlayer(self, pointer):
        self._handle(pointer)

    def _MusicSequenceGetTrackCount(self, sequence, count):
        count._obj.value = len(self.track_beats)

    def _MusicSequenceGetIndTrack(self, sequence, index, track):
        track._obj.value = 100 + index

    def _MusicTrackGetProperty(self, track, prop, value, size):
        assert prop == 5 and size._obj.value == 8
        ctypes.cast(value, ctypes.POINTER(ctypes.c_double)).contents.value = self.track_beats[track.value - 100]

    def _MusicSequenceGetSecondsForBeats(self, sequence, beats, seconds):
        seconds._obj.value = beats / 2  # 120 BPM

    def _MusicPlayerGetTime(self, player, beats):
        beats._obj.value = self.now


class FakeCoreFoundation:
    def __init__(self):
        self.paths, self.released = [], []

    def CFURLCreateFromFileSystemRepresentation(self, allocator, raw, length, directory):
        self.paths.append(raw[:length].decode("utf-8"))
        return 4242

    def CFRelease(self, url):
        self.released.append(url)


def mac_player(**options):
    at, cf = FakeAudioToolbox(**options), FakeCoreFoundation()
    return playback.MidiPlayer(playback.AudioToolboxBackend(at, cf)), at, cf


def test_mac_backend_call_order_length_and_cleanup(tmp_path):
    player, at, cf = mac_player()
    path = tmp_path / "中文 試聽" / "score one.midi"
    path.parent.mkdir()
    path.write_bytes(b"MThd")
    player.play(path)
    assert cf.paths == [str(path.resolve())] and cf.released == [4242]
    assert at.calls[:3] == ["NewMusicSequence", "MusicSequenceFileLoad", "MusicSequenceGetTrackCount"]
    assert at.calls[-4:] == ["NewMusicPlayer", "MusicPlayerSetSequence", "MusicPlayerPreroll", "MusicPlayerStart"]
    assert player._backend.length_seconds == 6.0  # longest track, 12 beats at 120 BPM
    assert player.playing
    at.calls.clear()
    player.stop()
    assert at.calls == ["MusicPlayerStop", "DisposeMusicPlayer", "DisposeMusicSequence"]
    assert not player.playing
    at.calls.clear()
    player.stop()
    assert at.calls == []  # nothing disposed twice


def test_mac_backend_reports_end_of_sequence(tmp_path):
    player, at, _ = mac_player()
    player.play(tmp_path / "a.midi")
    at.now = 11.0  # 5.5 s: still inside the track
    assert player.playing
    at.now = 14.0  # 7 s: past the end plus the release tail
    assert not player.playing
    assert at.calls[-2:] == ["DisposeMusicPlayer", "DisposeMusicSequence"]


@pytest.mark.parametrize("step", ["MusicSequenceFileLoad", "NewMusicPlayer", "MusicPlayerStart"])
def test_mac_backend_failure_raises_app_error_and_disposes(tmp_path, step):
    player, at, cf = mac_player(fail={step: 100002})
    with pytest.raises(RuntimeError, match="100002"):
        player.play(tmp_path / "missing.midi")
    assert not player.playing
    assert "DisposeMusicSequence" in at.calls
    assert ("DisposeMusicPlayer" in at.calls) == (step != "MusicSequenceFileLoad" and step != "NewMusicPlayer")
    assert cf.released == [4242]


def test_unsupported_platform_message(tmp_path):
    player = playback.MidiPlayer(backend=None)
    player._backend = None
    with pytest.raises(RuntimeError, match="匯出 MIDI"):
        player.play(tmp_path / "a.midi")
    player.stop()
    assert not player.playing


class FakeMci:
    """winmm's mciSendStringW: a sequencer that plays ``length_ms`` and then reports "stopped"."""

    def __init__(self, length_ms=4000):
        self.commands, self.now_ms, self.length_ms, self.open = [], 0, length_ms, False
        self.start_ms = 0

    def mciSendStringW(self, text, buffer, size, hwnd):
        self.commands.append(text)
        if text.startswith("open "):
            self.open = True
        elif text.startswith("close "):
            self.open = False
        elif text.startswith("play jianpu_preview from "):
            self.start_ms = int(text.rsplit(" ", 1)[1])
        elif text.startswith("status "):
            if not self.open:
                return 263          # MCIERR_INVALID_DEVICE_NAME: not open
            position = min(self.start_ms + self.now_ms, self.length_ms)
            buffer.value = {"mode": "playing" if position < self.length_ms else "stopped",
                            "position": str(position)}[text.split()[-1]]
        return 0


def test_windows_backend_commands(tmp_path):
    mci = FakeMci()
    player = playback.MidiPlayer(playback.WinMMBackend(mci))
    path = tmp_path / "score.midi"
    player.play(path)
    assert mci.commands == ["close jianpu_preview", f'open "{path}" type sequencer alias jianpu_preview',
                            "set jianpu_preview time format milliseconds", "play jianpu_preview from 0"]
    assert player.playing
    player.stop()
    assert mci.commands[-1] == "close jianpu_preview" and not player.playing


def test_windows_transport_ends_with_the_midi(tmp_path):
    """Review P1.12: MCI keeps no 'playing' flag for us; the end of the sequence must stop the
    transport and the position must come from the sequencer, not the wall clock.
    (Fake MCI: the real winmm path is unverified on this platform.)"""
    mci = FakeMci(length_ms=4000)
    player = playback.MidiPlayer(playback.WinMMBackend(mci))
    player.play(tmp_path / "score.midi", 1.5)
    assert "play jianpu_preview from 1500" in mci.commands
    mci.now_ms = 1000
    player._started -= 60                       # the wall clock is irrelevant now
    assert player.playing and player.position() == pytest.approx(2.5)
    mci.now_ms = 2600
    assert not player.playing
    assert mci.commands[-1] == "close jianpu_preview"
    assert player.position() == 0.0


def test_switching_parts_while_playing_highlights_the_sounding_note_of_the_new_part(qtbot, tmp_path, monkeypatch):
    """Review P1.9: the old part's map gave row numbers of the wrong part after a switch."""
    from types import SimpleNamespace

    from jianpu_studio.app import MainWindow
    from jianpu_studio.model import Note, Part, Project, Score
    from jianpu_studio.playmap import PlayMap
    monkeypatch.setattr(MainWindow, "render_score", lambda *a, **k: None)
    window = MainWindow(recovery=False)
    window.recovery_path = tmp_path / "recovery.jps"
    window.autosave.stop()
    qtbot.addWidget(window, before_close_func=lambda w: setattr(w, "dirty", False))
    score = Score(parts=[Part("一", [Note(1), Note(2), Note(3), Note(4)]), Part("二", [Note(5, duration=2), Note(6, duration=2)])])
    window.reset(Project(score))
    quarter = 60 / score.tempo
    clock = SimpleNamespace(seconds=0.0)
    window.player = SimpleNamespace(playing=True, position=lambda: clock.seconds, stop=lambda: None)
    window.play_map = PlayMap(window.project.score, 0)
    clock.seconds = 1.5 * quarter
    window.playback_tick()
    assert window.notes.play_note == (0, 1)
    window.switch_part(1)
    for beats, row in ((1.5, 0), (2.5, 1), (3.5, 1)):
        clock.seconds = beats * quarter
        window.playback_tick()
        assert window.notes.play_note == (1, row), beats


def test_app_transport_returns_to_idle_when_windows_playback_ends(qtbot, tmp_path, monkeypatch):
    from jianpu_studio.app import MainWindow
    monkeypatch.setattr(MainWindow, "render_score", lambda *a, **k: None)
    window = MainWindow(recovery=False)
    window.recovery_path = tmp_path / "recovery.jps"
    window.autosave.stop()
    qtbot.addWidget(window, before_close_func=lambda w: setattr(w, "dirty", False))
    mci = FakeMci(length_ms=2000)
    window.player = playback.MidiPlayer(playback.WinMMBackend(mci))
    from jianpu_studio.playmap import PlayMap
    window.play_map = PlayMap(window.project.score)
    window.player.play(tmp_path / "score.midi")
    window.play_timer.start()
    window.playback_tick()
    assert window.player.playing and window.play_timer.isActive()
    mci.now_ms = 2000
    window.playback_tick()
    assert not window.player.playing and not window.play_timer.isActive()
    assert window.notes.play_note is None


@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "darwin", reason="AudioToolbox is macOS-only")
def test_real_macos_playback_advances_and_stops(tmp_path):
    tool = find_tool("lilypond")
    if not tool:
        pytest.skip("JIANPU_LILYPOND not configured")
    result = render(demo_project().notation(), (tmp_path / "試聽 中文").resolve(), tool.resolve(), create_pdf=False)
    player = playback.MidiPlayer()
    player.play(result.midi)
    try:
        backend = player._backend
        assert backend.length_seconds > 5
        time.sleep(0.5)
        position = backend.position()
        assert 0.2 < position < 2.0
        assert player.playing
    finally:
        player.stop()
    assert not player.playing and backend.position() == 0.0
