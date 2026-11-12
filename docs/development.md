# Architecture and verification

Python 3.12 + PySide6 native widgets. The application is offline; there is no web server, account, telemetry, API key, or cloud OCR.

```
Visual Score (.jps) → deterministic jianpu text
Source editor (.jly) ────────────────────────→ isolated jianpu-ly worker
                                               ↓ .ly
Photo → Pillow → Audiveris → MusicXML ───────→ LilyPond → PDF + MIDI
                              ↓                  ↓
                 conservative visual import   Qt PDF preview
```

`model.py` uses rational durations and a versioned JSON schema. `musicxml.py` validates bounded ZIP/XML inputs with defusedxml; visual import accepts only representable monophonic measures and falls back to upstream conversion for complex music. Both converters preserve the editable source in the project. Upstream jianpu-ly is vendored unchanged at `b3c2bbb7de423b7c9956204898e6ac1a7ca50c62`.

`engine.py` executes tools without a shell, in separate per-job directories, with cancellation and timeouts (render 120 seconds; OMR 600 seconds). Conversion runs in a fresh worker process to isolate upstream globals. The frozen executable supports the same `--worker` contract. The UI remains responsive during jobs; edits are disabled until the job completes. Revision checks prevent exporting an outdated preview.

The app restricts raw LilyPond/Scheme extensions. This is defense in depth, **not an operating system sandbox**. Treat imported files as untrusted and keep the bundled engines updated. Do not turn the renderer into a public service without real OS isolation and resource limits.

## Tests

```bash
python -m pip install -e '.[dev]'
python scripts/fetch_runtime.py --platform linux
QT_QPA_PLATFORM=offscreen JIANPU_LILYPOND="$PWD/vendor-runtime/lilypond/bin/lilypond" pytest -q
ruff check src tests scripts
```

Tests cover rhythmic boundaries, round trips, malformed projects, safe source handling, MXL paths/entities, conservative import, cancellable processes, real PDF/MIDI generation, Unicode paths, and GUI editing/undo/save.

Windows CI additionally builds PyInstaller, runs the frozen converter, invokes the bundled LilyPond and Audiveris on an original generated fixture, compiles the recognized score, and tests silent installer/uninstaller execution. This is not a substitute for broad manual testing across Windows hardware, audio devices, printers, high-DPI configurations, and real-world photographs. Recognition accuracy on the clean test fixture does not establish an accuracy percentage for arbitrary photos.

## Build Windows packages

Use Windows x64 with Python 3.12 and Inno Setup 6.

```powershell
pip install -e ".[dev]"
python scripts/fetch_runtime.py --platform windows
pyinstaller --noconfirm packaging/JianpuStudio.spec
python scripts/assemble.py
python scripts/smoke_bundle.py dist/JianpuStudio --omr
& "${env:ProgramFiles(x86)}/Inno Setup 6/ISCC.exe" packaging/installer.iss
```

`runtime-lock.json` pins URLs and SHA-256 values. The build extracts Audiveris' MSI without installing it; its Java runtime, native libraries, classifier resources, and licenses are copied together. All necessary components are included in the final installer. Tesseract needs the **legacy-capable `tessdata` 4.1.0 model**, not the LSTM-only `tessdata_fast` model. A Traditional Chinese Noto font is placed in LilyPond's private font directory; no system font installation is needed.

The one-folder bundle permits replacing Qt/PySide dynamic libraries. App updates never intentionally remove user-created scores. No code-signing credential is configured. Before a stable release, configure Authenticode signing and expand manual Windows acceptance testing. Version 0.1.1 is an initial release.

## Release

After reviewing CI, tag `v0.1.1` and push the tag. The workflow rebuilds and tests the exact tagged code, then publishes the installer, portable archive and SHA-256 manifest. Main-branch builds are downloadable as Actions artifacts. Keep version numbers in `pyproject.toml`, `__init__.py`, `installer.iss`, and the workflow artifact names aligned. For upstream updates, update the lock file, licenses/source references, regression tests and fixture validation together.
