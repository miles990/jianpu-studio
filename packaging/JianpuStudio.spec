# Build an inspectable, replaceable-library one-folder application (LGPL).
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files

root = Path(SPECPATH).parent
data = collect_data_files('jianpu_studio')
data += [(str(root / 'LICENSE'), 'licenses'),
         (str(root / 'THIRD_PARTY_NOTICES.md'), 'licenses')]
a = Analysis([str(root / 'packaging' / 'launcher.py')],
             pathex=[str(root / 'src')],
             binaries=[], datas=data,
             hiddenimports=['jianpu_studio.worker', 'jianpu_studio.vendor.jianpu_ly'],
             excludes=['PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets',
                       'PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.QtQuickWidgets'],
             noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='JianpuStudio',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, icon=str(root / 'src/jianpu_studio/assets/icon.ico'))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='JianpuStudio')
