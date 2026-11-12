# Changelog

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
