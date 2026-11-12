# Third-party notices and source access

Jianpu Studio is independent of the upstream projects. Their names identify the engines being used and do not imply endorsement. This application is MIT; output scores are not automatically placed under the application's license.

| Component | Pinned version | License | Source |
| --- | --- | --- | --- |
| jianpu-ly, Silas S. Brown | b3c2bbb7de423b7c9956204898e6ac1a7ca50c62 | Apache-2.0 | [Exact source](https://github.com/ssb22/jianpu-ly/tree/b3c2bbb7de423b7c9956204898e6ac1a7ca50c62), vendored unchanged under src/jianpu_studio/vendor |
| GNU LilyPond | 2.24.4 | GPL-3.0-or-later | [Release source tarball](https://gitlab.com/api/v4/projects/lilypond%2Flilypond/packages/generic/lilypond/2.24.4/lilypond-2.24.4.tar.gz), [build recipes and dependency sources](https://gitlab.com/lilypond/lilypond/-/tree/v2.24.4/release/binaries) |
| Audiveris | 5.11.0 | AGPL-3.0 | [Exact source and build scripts](https://github.com/Audiveris/audiveris/tree/5.11.0), [source archive](https://github.com/Audiveris/audiveris/archive/refs/tags/5.11.0.tar.gz) |
| Qt / PySide6 / Shiboken | 6.8.3 | LGPL-3.0 and component-specific licenses | [Qt source archives](https://download.qt.io/archive/qt/6.8/6.8.3/), [Qt for Python source](https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.8.3-src/) |
| Python | 3.12 (builder-selected patch) | PSF | [Python source](https://www.python.org/downloads/source/) |
| Pillow | 11.3.0 | MIT-CMU | [Source](https://github.com/python-pillow/Pillow/tree/11.3.0) |
| defusedxml | 0.7.1 | PSF | [Source](https://github.com/tiran/defusedxml/tree/v0.7.1) |
| Noto Serif CJK TC | f8d157532fbfaeda587e826d4cd5b21a49186f7c | SIL Open Font License 1.1 | [Source and license](https://github.com/notofonts/noto-cjk/tree/f8d157532fbfaeda587e826d4cd5b21a49186f7c/Serif) |
| Tesseract English legacy + LSTM data | tessdata 4.1.0 / 4767ea922bcc460e70b87b1d303ebdfed0897da8 | Apache-2.0 | [Exact model data and license](https://github.com/tesseract-ocr/tessdata/tree/4767ea922bcc460e70b87b1d303ebdfed0897da8) |
| PyInstaller bootloader | 6.16.0 | GPL-2.0-or-later with bootloader exception | [Source](https://github.com/pyinstaller/pyinstaller/tree/v6.16.0) |
| Inno Setup Traditional Chinese messages, GoneTone and contributors | is-6_7_1 | Inno Setup license (see packaging/LICENSE-Inno-Setup.txt) | [Exact translation source](https://github.com/jrsoftware/issrc/blob/is-6_7_1/Files/Languages/Unofficial/ChineseTraditional.isl) |

LilyPond and Audiveris are independently executable programs invoked through command-line interfaces. LilyPond is unmodified. The Audiveris Windows executable receives an application-manifest patch enabling the UTF-8 active code page for Unicode installation paths; the complete reproducible patch is in scripts/patch_windows_launcher.py. Its Java bytecode and runtime are unchanged. Their bundled dependencies keep upstream notices. LilyPond's `runtime/lilypond/licenses` includes notices for Guile, Ghostscript, FreeType, Fontconfig, GLib, Pango, Cairo, GNU libraries, fonts and other components. Audiveris' Java runtime has its notices in `runtime/audiveris/runtime/legal` (or `lib/runtime/legal`, depending on packaging); Java libraries and resources remain in the upstream layout. Upstream build recipes specify their corresponding source revisions. The Java runtime and Tesseract natives come from the official Audiveris distribution, not the host computer.

Qt/PySide is distributed as replaceable dynamic libraries in `_internal`, not statically linked. Recipients may replace or debug these libraries under the LGPL; this project does not impose additional restrictions. Installer removal leaves the user's separately saved score files alone. Copies of package licenses are collected into `licenses/` during assembly. The application includes no proprietary fonts, sample songs, or cloud services. The demonstration melody is a traditional public-domain tune; the recognition fixture is generated from that melody.

For redistribution, preserve this file, all upstream license/legal directories, the complete application source and build scripts, and access to the exact corresponding upstream sources above. Follow each component's license; do not strip notices when repackaging. Runtime checksums are in `packaging/runtime-lock.json` and installed file checksums in `MANIFEST.sha256`.
