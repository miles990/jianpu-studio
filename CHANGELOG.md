# Changelog

## 0.3.0

- Default to a single readable, editable score surface with original-image comparison available from the top.
- Generate PDF only when print preview or PDF output is requested; export PDF after preparing the latest score, and prepare MIDI playback independently without generating a PDF. F5 now opens print preview.
- Select entire measures from their number or blank area; add insert-before/after/end and multi-measure insertion, clear, delete, and duplicate actions.
- Distinguish clearing measures to rests from removing their duration and shifting later measures earlier.
- Add a fill-measure mode that consumes available rests without moving later measures or overwriting existing pitched notes; report insufficient space.
- Preserve undo/redo and selection for measure operations, and remove affected slur, tie, and repeat relationships with a visible notice.
- Add Insert, Ctrl+Insert, Ctrl+B, and Ctrl+Delete measure shortcuts, and document the difference between editable notation and read-only LilyPond PDF output.
- Extend the official-source UI research and refresh the Traditional Chinese user documentation for the measure workflow.

## 0.2.1

- 首個整合新版小節工作區、二胡記號與程式內更新的發布版本，包含下列 0.2.0／0.1.3 變更。
- 修正 Windows CI 對無主控台 GUI 程式結束狀態的判定，等待實際程序結束，保留升級後診斷。
- 0.1.3／0.2.0 未完成發布關卡，因此不提供這兩版安裝包；舊版使用者直接升級至 0.2.1。

## 0.2.0（整合版，未發布安裝包）

- Replace the card-based editor with a measure-based score view, compact workspace controls, score zoom, bar navigation, and focused editing, preview, and photo-review views.
- Add explicit insert/edit modes, before/after/end insertion, multi-selection, measure selection, copy/cut/paste/duplicate, and selection-aware undo/redo.
- Apply property edits directly while preserving other mixed values; add EOP-style numeric entry, Space mode switching, Q/W/E/R/A/S durations, and Enter playback.
- Add visual erhu fingering, bowing, slides, dynamics, articulations, slurs, same-pitch ties, and complete-measure repeats with notation checks.
- Add A4/Letter paper settings, score sizes 16–30, and automatic or 1–8 measures per line.
- Keep editing available during PDF rendering, render changed scores before playback, and add photo zoom and a separate diagnostics tab.
- Add advanced-source snippets for pickups, tuplets, grace notes, slurs, repeats, line breaks, and additional parts; these remain source-editing workflows.
- Refresh the Traditional Chinese README and user guide; generate readable offline HTML with semantic lists, tables, and code blocks without third-party runtime dependencies.

## 0.1.3（更新功能開發版，未發布安裝包）

- Add in-app stable-release checks, cancellable downloads, size/SHA-256 verification, and updates to the existing Windows installation scope and directory.
- Prompt for unsaved music before applying an update; restart the app when installation finishes.
- Add upgrade preservation tests for custom Unicode paths, projects and settings.
- Run Linux and Windows checks in parallel; build full release packages on tags or manual dispatch, and cancel superseded branch checks.

## 0.1.2

- Retry low-resolution staff images once using Audiveris' measured interline spacing, with bounded scaling and no changes to the original image.
- Show actionable Chinese recognition errors and retain full diagnostics with a copy button.
- Coalesce photo-preview adjustments and render a reduced preview while retaining full resolution for recognition.
- Make editing controls scrollable on small displays and hide the navigation sidebar when the window is narrow.
- Add six regression tests for adaptive recognition, cancellation, retry limits, and image-size bounds.

## 0.1.1

- Add per-user / all-users installation choices with correct elevation and installation directories.
- Reject protected destinations before copying files in per-user mode; guide users to the writable default.
- Enable UTF-8 paths in the Audiveris launcher manifest so Java can load from Chinese installation directories.
- Verify both installer modes and complete OMR from an installed Chinese path in Windows CI.
- Windows 10 now requires version 1903 or newer for per-process UTF-8 path support.

## 0.1.0

- Initial Traditional Chinese Windows desktop editor.
- Visual monophonic editing, advanced jianpu-ly source, PDF/MIDI output.
- Offline Audiveris photo recognition with preparation and review.
- MusicXML/MXL import, project persistence and recovery.
- Bundled Windows installer/portable build, checksum verification and CI.
