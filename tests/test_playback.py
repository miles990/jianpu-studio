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


def test_windows_backend_commands_unchanged(tmp_path):
    class FakeMci:
        def __init__(self):
            self.commands = []

        def mciSendStringW(self, text, *args):
            self.commands.append(text)
            return 0

    mci = FakeMci()
    player = playback.MidiPlayer(playback.WinMMBackend(mci))
    path = tmp_path / "score.midi"
    player.play(path)
    assert mci.commands == ["close jianpu_preview", f'open "{path}" type sequencer alias jianpu_preview',
                            "play jianpu_preview from 0"]
    assert player.playing  # Windows keeps the flag until stop()
    player.stop()
    assert mci.commands[-1] == "close jianpu_preview" and not player.playing


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
