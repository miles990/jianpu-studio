# Changelog

## 0.1.3

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
