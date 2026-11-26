"""Play MIDI with the operating system's built-in synthesizer.

Windows uses the winmm MCI sequencer; macOS uses AudioToolbox's MusicPlayer
(Apple's DLS software synth). Neither needs a separate player installation.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import sys
from pathlib import Path

from .i18n import N_, tr

UNSUPPORTED = N_("內建 MIDI 試聽支援 Windows 與 macOS；其他系統請匯出 MIDI。")


class WinMMBackend:
    def __init__(self, mci=None):
        if mci is None:
            mci = ctypes.WinDLL("winmm")
            mci.mciSendStringW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_void_p]
            mci.mciSendStringW.restype = ctypes.c_uint
        self._mci = mci

    def command(self, text: str):
        code = self._mci.mciSendStringW(text, None, 0, None)
        if code:
            raise RuntimeError(tr("Windows MIDI 播放失敗（{code}）。請確認音訊裝置可用；也可匯出 MIDI。").format(code=code))

    def play(self, path: Path):
        self.command(f'open "{path}" type sequencer alias jianpu_preview')
        try:
            self.command("play jianpu_preview from 0")
        except Exception:
            self.stop()
            raise

    def active(self) -> bool:
        # Unchanged Windows behaviour: the flag stays set until stop().
        return True

    def stop(self):
        self._mci.mciSendStringW("close jianpu_preview", None, 0, None)


# AudioToolbox constants (MusicPlayer.h).
_ANY_FILE_TYPE = 0
_TRACK_LENGTH = 5  # kSequenceTrackProperty_TrackLength, MusicTimeStamp (beats)
_RELEASE_TAIL_SECONDS = 0.6


def load_audiotoolbox():
    """Load and type AudioToolbox + CoreFoundation for the macOS backend."""
    at = ctypes.CDLL(ctypes.util.find_library("AudioToolbox"))
    cf = ctypes.CDLL(ctypes.util.find_library("CoreFoundation"))
    vp, u32, f64, st = ctypes.c_void_p, ctypes.c_uint32, ctypes.c_double, ctypes.c_int32
    cf.CFURLCreateFromFileSystemRepresentation.argtypes = [vp, ctypes.c_char_p, ctypes.c_long, ctypes.c_bool]
    cf.CFURLCreateFromFileSystemRepresentation.restype = vp
    cf.CFRelease.argtypes = [vp]
    cf.CFRelease.restype = None
    signatures = {
        "NewMusicSequence": [ctypes.POINTER(vp)],
        "DisposeMusicSequence": [vp],
        "MusicSequenceFileLoad": [vp, vp, u32, u32],
        "MusicSequenceGetTrackCount": [vp, ctypes.POINTER(u32)],
        "MusicSequenceGetIndTrack": [vp, u32, ctypes.POINTER(vp)],
        "MusicSequenceGetSecondsForBeats": [vp, f64, ctypes.POINTER(f64)],
        "MusicTrackGetProperty": [vp, u32, vp, ctypes.POINTER(u32)],
        "NewMusicPlayer": [ctypes.POINTER(vp)],
        "DisposeMusicPlayer": [vp],
        "MusicPlayerSetSequence": [vp, vp],
        "MusicPlayerPreroll": [vp],
        "MusicPlayerStart": [vp],
        "MusicPlayerStop": [vp],
        "MusicPlayerGetTime": [vp, ctypes.POINTER(f64)],
    }
    for name, argtypes in signatures.items():
        function = getattr(at, name)
        function.argtypes = argtypes
        function.restype = st
    return at, cf


class AudioToolboxBackend:
    """MusicPlayer never stops by itself; end of track is computed explicitly."""

    def __init__(self, at=None, cf=None):
        if at is None or cf is None:
            at, cf = load_audiotoolbox()
        self._at, self._cf = at, cf
        self._sequence = None
        self._player = None
        self.length_seconds = 0.0

    def _check(self, code: int):
        if code:
            raise RuntimeError(tr("macOS MIDI 播放失敗（{code}）。請確認音訊裝置可用；也可匯出 MIDI。").format(code=code))

    def _seconds(self, beats: float) -> float:
        seconds = ctypes.c_double()
        self._check(self._at.MusicSequenceGetSecondsForBeats(self._sequence, beats, ctypes.byref(seconds)))
        return seconds.value

    def play(self, path: Path):
        at = self._at
        try:
            sequence = ctypes.c_void_p()
            self._check(at.NewMusicSequence(ctypes.byref(sequence)))
            self._sequence = sequence
            raw = os.fsencode(str(Path(path).resolve()))
            url = self._cf.CFURLCreateFromFileSystemRepresentation(None, raw, len(raw), False)
            if not url:
                raise RuntimeError(tr("無法讀取 MIDI 檔案路徑。"))
            try:
                self._check(at.MusicSequenceFileLoad(sequence, url, _ANY_FILE_TYPE, 0))
            finally:
                self._cf.CFRelease(url)
            count = ctypes.c_uint32()
            self._check(at.MusicSequenceGetTrackCount(sequence, ctypes.byref(count)))
            beats = 0.0
            for index in range(count.value):
                track = ctypes.c_void_p()
                self._check(at.MusicSequenceGetIndTrack(sequence, index, ctypes.byref(track)))
                length = ctypes.c_double()
                size = ctypes.c_uint32(ctypes.sizeof(length))
                self._check(at.MusicTrackGetProperty(track, _TRACK_LENGTH, ctypes.byref(length), ctypes.byref(size)))
                beats = max(beats, length.value)
            self.length_seconds = self._seconds(beats)
            player = ctypes.c_void_p()
            self._check(at.NewMusicPlayer(ctypes.byref(player)))
            self._player = player
            self._check(at.MusicPlayerSetSequence(player, sequence))
            self._check(at.MusicPlayerPreroll(player))
            self._check(at.MusicPlayerStart(player))
        except Exception:
            self.stop()
            raise

    def position(self) -> float:
        """Elapsed playback time in seconds (0 when idle)."""
        if self._player is None:
            return 0.0
        beats = ctypes.c_double()
        if self._at.MusicPlayerGetTime(self._player, ctypes.byref(beats)):
            return 0.0
        return self._seconds(beats.value)

    def active(self) -> bool:
        if self._player is None:
            return False
        try:
            return self.position() < self.length_seconds + _RELEASE_TAIL_SECONDS
        except RuntimeError:
            return False

    def stop(self):
        player, sequence = self._player, self._sequence
        self._player = self._sequence = None
        self.length_seconds = 0.0
        if player is not None:
            self._at.MusicPlayerStop(player)
            self._at.DisposeMusicPlayer(player)
        if sequence is not None:
            self._at.DisposeMusicSequence(sequence)


def default_backend():
    if os.name == "nt":
        return WinMMBackend()
    if sys.platform == "darwin":
        try:
            return AudioToolboxBackend()
        except (OSError, AttributeError, TypeError):
            return None
    return None


class MidiPlayer:
    """Interface used by the app: play(path), stop() and the playing flag."""

    def __init__(self, backend=None):
        self._backend = backend if backend is not None else default_backend()
        self._playing = False

    @property
    def playing(self) -> bool:
        # macOS reports the real end of the sequence; Windows keeps the flag
        # until stop() as before.
        if self._playing and not self._backend.active():
            self.stop()
        return self._playing

    def play(self, path: Path):
        self.stop()
        if self._backend is None:
            raise RuntimeError(tr(UNSUPPORTED))
        self._backend.play(Path(path))
        self._playing = True

    def stop(self):
        if self._backend is not None:
            self._backend.stop()
        self._playing = False
