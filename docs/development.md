# Architecture and verification

Python 3.12 + PySide6 native widgets. The application is offline; there is no web server, account, telemetry, API key, or cloud OCR.

```text
Visual Score (.jps) → score_view (immediate editable display)
        └──────────→ deterministic jianpu text → isolated jianpu-ly worker
                                               ↓ .ly
                                            LilyPond
                                               ├→ MIDI (playback / export)
                                               └→ PDF + MIDI (print / PDF export)
MusicXML / MXL → conservative visual import (reject unsupported content)
```

`model.py` uses 48 integer ticks per quarter note, rational durations, and a versioned JSON schema. This represents undotted 3:2 triplets and dotted 32nd notes without rounding. Triplet splitting preserves the original duration; complete three-note groups are validated before structural edits. `musicxml.py` validates bounded ZIP/XML inputs with defusedxml; visual import accepts only representable monophonic measures. Unsupported content is rejected before replacing the current score. Upstream jianpu-ly is vendored unchanged at `b3c2bbb7de423b7c9956204898e6ac1a7ca50c62`.

Version 0.3.1 removes the source editor and source-file open/export actions. Legacy source-mode projects are accepted only when their source exactly matches the generated visual score (apart from surrounding whitespace); otherwise opening is refused without changing the file or current project. Incompatible recovery files are backed up byte-for-byte before a new recovery file can be written. If that backup fails, automatic recovery writes and cleanup are disabled for the session to preserve the only copy. The model and worker retain source support internally for schema compatibility and backend tests.

Opening a file from Windows passes an initial path to the main window. macOS Finder instead sends a `QFileOpenEvent` (also to a running instance); `platform_support.FileOpenRouter` queues it until no modal dialog, blocking task, update or close is in progress, then uses the same guarded `open_file`. One startup callback completes recovery first, then opens that file through the unsaved-work guard. Separate timers would be unsafe because a recovery dialog can run a nested event loop. Blocking imports disable both the note editor and the always-visible score settings; background rendering leaves them editable.

`engine.py` executes tools without a shell, in separate per-job directories, with cancellation and timeouts (render 120 seconds; OMR 600 seconds). Conversion runs in a fresh worker process to isolate upstream globals. The frozen executable supports the same `--worker` contract. On macOS, cancellation kills the tool's process group (`start_new_session` + `killpg`); Windows uses `taskkill /T`. Normal score editing does not request a PDF. Playback and MIDI export use `render(create_pdf=False)` with LilyPond `-dno-print-pages`; PDF output is requested only for print preview, PDF export, or opening the print file. Rendering runs without locking editing. A single task queue coalesces output requests; revision checks discard stale results and prevent exporting an outdated preview. Imports temporarily lock the editor. `score_view.py` paints visible measures, while immutable notes let undo snapshots share unchanged note data.

## Platforms

All platforms use LilyPond 2.26.0 (2.24 has no Apple Silicon build). jianpu-ly still emits 2.20/2.24-era text, so `worker.adapt_output` adjusts it: it removes jianpu-ly's `set-global-fonts` block (removed in 2.25; jianpu-ly adds it whenever `/Library/Fonts/Arial Unicode.ttf` exists, i.e. on most Macs), writes `tempoWholesPerMinute = #21` instead of a moment on 2.25+ (otherwise LilyPond ignores jianpu-ly's default 84 BPM), and sets fonts with `property-defaults.fonts.serif/sans` (available since 2.24). The font chains start with the bundled Noto Serif CJK TC, then Windows (MingLiU, Microsoft JhengHei/YaHei), macOS (Songti, Heiti, PingFang) and Linux (Noto CJK) faces. The bundled TC font covers all 6,763 GB2312 characters, so Simplified titles and lyrics never lose glyphs; a Simplified font (+24 MB) is not bundled, so a few characters keep Traditional glyph shapes. The system PingFang font is not visible to LilyPond's fontconfig on macOS; Songti TC is.

| | Windows | macOS | Linux |
| --- | --- | --- | --- |
| LilyPond build | mingw-x86_64 | darwin-arm64 (requires macOS 26) / darwin-x86_64 (10.15+) | linux-x86_64 |
| Program resources (`app_root()`) | beside `JianpuStudio.exe` | `JianpuStudio.app/Contents/Resources` | source tree |
| MIDI preview | winmm MCI sequencer | AudioToolbox `MusicPlayer` (ctypes) | not supported; export MIDI |
| Updates | verified download + silent installer | version check, opens the release page | none |
| UI font | Microsoft JhengHei | PingFang TC | Noto Sans CJK TC |

The macOS player loads the MIDI through `MusicSequenceFileLoad`, computes the longest track length (`kSequenceTrackProperty_TrackLength`) because `MusicPlayer` never stops by itself, and reports `playing = False` (disposing the player and sequence) once the time passes the end plus a short release tail. Windows keeps its previous behaviour.

GUI tests on macOS: a modal `exec()` started outside `QApplication.exec()` (as pytest-qt tests do) leaves Qt 6.8's Core Foundation event dispatcher without timer or queued-signal delivery for later `qWait()` calls. `tests/conftest.py` runs one short real event loop after each test on macOS. The running application is not affected (nested dialogs run inside `QApplication.exec()`; checked with standalone scripts).

Qt PDF reads an owned `QBuffer` containing the PDF bytes rather than holding the output filename open. The application closes the document and buffer before replacing a preview, so Windows can remove old job directories without a lingering PDF file lock. The buffer must remain alive while the document uses it.

External PDF readers receive a separate print copy from `printing.py`, under the user's application-data cache. Copies are named by content hash and reused without overwriting open files. Best-effort cleanup removes only managed copies older than seven days; locked copies are left alone. Closing the editor or replacing a render never removes the PDF an external reader is using.

The photo-recognition pipeline (Pillow → Audiveris → MusicXML) remains in the backend and bundle tests. Version 0.3.0 removes its user-interface entry points, original-image comparison, and recognized-result export; ordinary MusicXML import remains available.

The app restricts raw LilyPond/Scheme extensions. This is defense in depth, **not an operating system sandbox**. Treat imported files as untrusted and keep the bundled engines updated. Do not turn the renderer into a public service without real OS isolation and resource limits.

## Tests

```bash
python -m pip install -e '.[dev]'
python scripts/fetch_runtime.py --platform linux   # or: --platform macos [--arch arm64|x86_64]
QT_QPA_PLATFORM=offscreen JIANPU_LILYPOND="$PWD/vendor-runtime/lilypond/bin/lilypond" pytest -q
ruff check src tests scripts
python scripts/check_version.py
python scripts/render_docs.py --check
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

The one-folder bundle permits replacing Qt/PySide dynamic libraries. App updates never intentionally remove user-created scores. No code-signing credential is configured. Published installers currently remain unsigned; README states this. Authenticode signing and broader hardware acceptance testing remain future work.

## Build macOS packages

Use macOS with Python 3.12. Build each architecture on a matching Mac (no universal build: the arm64 LilyPond build requires macOS 26, the Intel build runs on 10.15+; the app's `LSMinimumSystemVersion` is 26.0 for arm64 and 12.0 for x86_64, the latter set by PySide6).

```bash
pip install -e '.[dev]'
python scripts/fetch_runtime.py --platform macos
python scripts/build_macos.py
python scripts/smoke_bundle.py dist/JianpuStudio.app --gui
```

`build_macos.py` generates the `.icns` from `assets/icon.png` (sips + iconutil), runs PyInstaller (`JianpuStudio.app` with bundle id `io.github.miles990.jianpustudio`, `.jps` document type, high-resolution flag), copies the engines, notices and docs into `Contents/Resources` (`assemble.py --target`), ad-hoc signs and verifies the bundle, and creates `dist/JianpuStudio-<version>-macOS-<arch>.dmg` containing `簡譜工房.app` and an Applications link. There is no Developer ID signature or notarization: on first launch users must allow the app in System Settings → Privacy & Security. Measured on an Apple Silicon Mac (macOS 27.2, arm64, 0.3.1): app 265 MB on disk, DMG 110 MB; LilyPond is 152 MB of the app.

Tag builds on CI run the full test suite on `macos-26` (arm64) and `macos-15-intel` (x86_64), build both DMGs, run the GUI self-test, frozen worker and bundled LilyPond from the mounted DMG, and add the DMGs to the release and the combined `SHA256SUMS.txt`.

## Release

After reviewing CI, tag the matching version (for example `v0.3.0`) and push the tag. The workflow rebuilds and tests the exact tagged code, then publishes the installer, portable archive and SHA-256 manifest. Main-branch runs execute regression checks without rebuilding installer archives. Keep version numbers in `pyproject.toml`, `__init__.py`, and `installer.iss` aligned; artifact names derive from that version. For upstream updates, update the lock file, licenses/source references, regression tests and fixture validation together.

## 開發與發佈流程（trunk-based）

主線為 `main`，採小批次、可驗證變更；不維護長期功能分支。並行工作先分配檔案所有權，依賴介面先協調，完成的小目標各自測試後由整合者提交、推送。

- `main`／PR：Linux 與 Windows 測試並行；同一分支有新提交時取消過期檢查。文件提交仍可檢查一致性，但不重複壓縮安裝包。
- 版本 tag／手動完整建置：執行凍結後 GUI、真實 LilyPond、一般與低解析度 OMR、中文路徑、安裝及移除、舊版原路徑升級及重開，再產生 ZIP、Setup 與 SHA-256。
- 版本一致性先檢查：Python、pyproject、Inno Setup 與 tag 必須一致。正式 tag 不移動、不覆寫已發布的版本。
- Release 只發布該次完整驗證的同一組成品，不另行重建未測的安裝檔。主線測試與 Windows 打包都必須成功。
- 每個版本先定可驗收範圍；阻礙該範圍的錯誤在當版修正，其餘能力列入後續版本，README 不標示為已完成。
- UI 改動驗證真實操作、不同視窗尺寸與截圖；效能主張附環境、測量方法與原始結果，不用單一時限測試取代量測。

這些流程參考 [DORA 的 trunk-based development](https://dora.dev/capabilities/trunk-based-development/)、[小批次交付](https://dora.dev/capabilities/working-in-small-batches/) 與 [GitHub Actions concurrency](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency)，並依本專案的 Windows 安裝、離線引擎與一般使用者需求調整。

## AI 協作方式

`AGENTS.md` 保留短而穩定的專案規則；README 描述使用者契約，研究、驗證與架構各有獨立文件。每個並行任務指定輸入、輸出、檔案範圍及驗收，整合者統一處理 Git 與版本發布。高風險處（例如更新與背景工作）增加獨立審查；審查發現必須回到可重現測試或具體修正。

完成回報需附實際測試、畫面或產物證據；未完成能力留在明確的版本範圍中。優先消除反覆卡住的環境或流程問題，而不是靠更長的提示重複嘗試。

參考：[OpenAI Harness engineering](https://openai.com/index/harness-engineering/)、[Codex 工作流迭代](https://developers.openai.com/cookbook/examples/codex/iterating-development-workflows-with-codex)。本專案保留使用者要求的 trunk-based 與自主交付，不額外引入逐步人工核准。
