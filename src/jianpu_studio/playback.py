"""Use Windows' built-in MIDI sequencer; no separate player installation."""

import os
from pathlib import Path


class MidiPlayer:
    def __init__(self):
        self.playing = False
        self._mci = None
        if os.name == "nt":
            import ctypes
            self._mci = ctypes.WinDLL("winmm")
            self._mci.mciSendStringW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_void_p]
            self._mci.mciSendStringW.restype = ctypes.c_uint

    def command(self, text: str):
        if self._mci is None:
            raise RuntimeError("內建 MIDI 試聽支援 Windows；其他系統請匯出 MIDI。")
        code = self._mci.mciSendStringW(text, None, 0, None)
        if code:
            raise RuntimeError(f"Windows MIDI 播放失敗（{code}）。請確認音訊裝置可用；也可匯出 MIDI。")

    def play(self, path: Path):
        self.stop()
        self.command(f'open "{path}" type sequencer alias jianpu_preview')
        try:
            self.command("play jianpu_preview from 0")
            self.playing = True
        except Exception:
            self.stop()
            raise

    def stop(self):
        if self._mci:
            self._mci.mciSendStringW("close jianpu_preview", None, 0, None)
        self.playing = False
