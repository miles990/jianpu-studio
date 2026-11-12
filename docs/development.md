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

## 開發與發佈流程（trunk-based）

主線為 `main`，採小批次、可驗證變更；不維護長期功能分支。並行工作先分配檔案所有權，依賴介面先協調，完成的小目標各自測試後由整合者提交、推送。

- `main`／PR：Linux 與 Windows 測試並行；同一分支有新提交時取消過期檢查。文件提交仍可檢查一致性，但不重複壓縮安裝包。
- 版本 tag／手動完整建置：執行凍結後 GUI、真實 LilyPond、一般與低解析度 OMR、中文路徑、安裝及移除，再產生 ZIP、Setup 與 SHA-256。
- 版本一致性先檢查：Python、pyproject、Inno Setup 與 tag 必須一致。正式 tag 不移動、不覆寫已發布的版本。
- Release 只發布該次完整驗證的同一組成品，不另行重建未測的安裝檔。主線測試與 Windows 打包都必須成功。
- 每個版本先定可驗收範圍；阻礙該範圍的錯誤在當版修正，其餘能力列入後續版本，README 不標示為已完成。
- UI 改動驗證真實操作、不同視窗尺寸與截圖；效能主張附環境、測量方法與原始結果，不用單一時限測試取代量測。

這些流程參考 [DORA 的 trunk-based development](https://dora.dev/capabilities/trunk-based-development/)、[小批次交付](https://dora.dev/capabilities/working-in-small-batches/) 與 [GitHub Actions concurrency](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency)，並依本專案的 Windows 安裝、離線引擎與一般使用者需求調整。

## AI 協作方式

`AGENTS.md` 保留短而穩定的專案規則；README 描述使用者契約，研究、驗證與架構各有獨立文件。每個並行任務指定輸入、輸出、檔案範圍及驗收，整合者統一處理 Git 與版本發布。高風險處（例如更新與背景工作）增加獨立審查；審查發現必須回到可重現測試或具體修正。

完成回報需附實際測試、畫面或產物證據；未完成能力留在明確的版本範圍中。優先消除反覆卡住的環境或流程問題，而不是靠更長的提示重複嘗試。

參考：[OpenAI Harness engineering](https://openai.com/index/harness-engineering/)、[Codex 工作流迭代](https://developers.openai.com/cookbook/examples/codex/iterating-development-workflows-with-codex)。本專案保留使用者要求的 trunk-based 與自主交付，不額外引入逐步人工核准。
