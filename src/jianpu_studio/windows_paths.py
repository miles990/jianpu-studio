"""Work around the upstream JDK launcher's non-ANSI installation-path bug.

Use a short path when available. Otherwise temporarily map an unused drive in
the current Windows logon session. No admin rights, permanent registry change,
or copying of the installed Java distribution is required. Exact-target
removal avoids deleting another application's drive definition.
"""

import os
import threading
from contextlib import contextmanager
from pathlib import Path

_drive_lock = threading.Lock()


@contextmanager
def compatible_launcher(executable: Path, *, use_short_path: bool = True):
    executable = executable.resolve()
    if os.name != "nt" or str(executable).isascii():
        yield executable
        return

    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    short_path = kernel.GetShortPathNameW
    short_path.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    short_path.restype = wintypes.DWORD
    length = short_path(str(executable), None, 0)
    if use_short_path and length:
        buffer = ctypes.create_unicode_buffer(length)
        if short_path(str(executable), buffer, length) and buffer.value.isascii():
            yield Path(buffer.value)
            return

    define = kernel.DefineDosDeviceW
    define.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR]
    define.restype = wintypes.BOOL
    kernel.GetLogicalDrives.restype = wintypes.DWORD
    # Include the sibling OCR model directory when using our bundled layout.
    base = executable.parent
    if (base.parent / "tessdata/eng.traineddata").is_file():
        base = base.parent
    relative = executable.relative_to(base)
    if not str(relative).isascii():
        raise RuntimeError("辨識工具的執行檔名稱需為英文；請使用完整版內附的 Audiveris.exe。")
    target = "\\??\\" + str(base)
    device = None
    with _drive_lock:
        occupied = kernel.GetLogicalDrives()
        if not occupied:
            raise RuntimeError("無法讀取 Windows 磁碟清單，請改用英文安裝路徑。")
        for code in range(ord("Z"), ord("P") - 1, -1):
            if not occupied & (1 << (code - ord("A"))):
                name = chr(code) + ":"
                if define(1, name, target):  # DDD_RAW_TARGET_PATH
                    device = name
                    break
    if device is None:
        raise RuntimeError("無法建立辨識工具需要的暫存路徑。請釋放 P:–Z: 其中一個磁碟代號，或將程式安裝到英文路徑。")
    try:
        yield Path(device + "\\") / relative
    finally:
        # RAW_TARGET_PATH | REMOVE_DEFINITION | EXACT_MATCH_ON_REMOVE
        define(1 | 2 | 4, device, target)
