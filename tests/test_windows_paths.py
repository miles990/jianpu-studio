import os

import pytest

from jianpu_studio.windows_paths import compatible_launcher


@pytest.mark.skipif(os.name != "nt", reason="Windows path compatibility")
@pytest.mark.parametrize("fail_inside", [False, True])
def test_unicode_alias_reads_the_same_file_and_is_removed(tmp_path, fail_inside):
    import ctypes
    file = tmp_path / "中文資料夾" / "辨識工具" / "Audiveris.exe"
    file.parent.mkdir(parents=True)
    file.write_bytes(b"path identity fixture")
    before = ctypes.windll.kernel32.GetLogicalDrives()
    try:
        with compatible_launcher(file, use_short_path=False) as alias:
            assert str(alias).isascii()
            assert alias.read_bytes() == file.read_bytes()
            if fail_inside:
                raise RuntimeError("simulated process failure")
    except RuntimeError as exc:
        assert fail_inside and str(exc) == "simulated process failure"
    assert ctypes.windll.kernel32.GetLogicalDrives() == before
