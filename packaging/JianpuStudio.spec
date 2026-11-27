# Build an inspectable, replaceable-library one-folder application (LGPL).
# Windows: dist/JianpuStudio/. macOS: additionally dist/JianpuStudio.app
# (scripts/build_macos.py copies packaging/icon/icon.icns and adds the engines).
# collect_data_files includes assets/icons/*.svg and assets/symbols/*.png (palette previews).
import os
import re
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files

root = Path(SPECPATH).parent
mac = sys.platform == 'darwin'
version = re.search(r'__version__ = "([^"]+)"', (root / 'src/jianpu_studio/__init__.py').read_text())[1]
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
          console=False, argv_emulation=False,
          icon=None if mac else str(root / 'src/jianpu_studio/assets/icon.ico'))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='JianpuStudio')

if mac:
    # LilyPond 2.26.0's arm64 build requires macOS 26; its x86_64 build 10.15.
    # PySide6 6.8 requires macOS 12.
    minimum = os.environ.get('JIANPU_MACOS_MINIMUM', '26.0' if os.uname().machine == 'arm64' else '12.0')
    document_type = 'io.github.miles990.jianpustudio.jps'
    app = BUNDLE(
        coll, name='JianpuStudio.app', version=version,
        icon=str(root / 'build/macos/JianpuStudio.icns'),
        bundle_identifier='io.github.miles990.jianpustudio',
        info_plist={
            'CFBundleName': '簡譜工房',
            'CFBundleDisplayName': '簡譜工房',
            'CFBundleShortVersionString': version,
            'CFBundleVersion': version,
            'CFBundleDevelopmentRegion': 'zh_TW',
            'CFBundleLocalizations': ['zh_TW', 'zh_CN'],
            'LSMinimumSystemVersion': minimum,
            'LSApplicationCategoryType': 'public.app-category.music',
            'NSHighResolutionCapable': True,
            'NSHumanReadableCopyright': 'MIT License. Bundled engines keep their own licenses.',
            'CFBundleDocumentTypes': [{
                'CFBundleTypeName': '簡譜工房樂譜',
                'CFBundleTypeRole': 'Editor',
                'LSHandlerRank': 'Owner',
                'LSItemContentTypes': [document_type],
                'CFBundleTypeExtensions': ['jps'],
                'CFBundleTypeIconFile': 'JianpuStudio.icns',
            }],
            'UTExportedTypeDeclarations': [{
                'UTTypeIdentifier': document_type,
                'UTTypeDescription': '簡譜工房樂譜',
                'UTTypeConformsTo': ['public.data', 'public.content'],
                'UTTypeIconFile': 'JianpuStudio.icns',
                'UTTypeTagSpecification': {'public.filename-extension': ['jps']},
            }],
        })
