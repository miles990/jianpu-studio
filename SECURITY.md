# Security

Do not post secrets or exploit samples containing private files in public issues. Use GitHub private vulnerability reporting when enabled, or a private maintainer contact listed on their profile.

The application runs local music-processing engines. Its source filter and bounded XML/ZIP readers are defense in depth, not a sandbox. Raw LilyPond/Scheme code is deliberately not supported. Do not expose rendering/recognition as an internet service without OS-level sandboxing, filesystem/network restrictions and CPU/memory quotas.

Runtime downloads are pinned and SHA-256 verified. Build/release jobs need minimal GitHub permissions; publishing uses only the job-scoped token. No API key or user credential belongs in this repository. The app has no telemetry or score upload.
