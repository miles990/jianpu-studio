# Contributing

Please open an issue describing the musical input, expected output and actual output. Use a small score/photo you have permission to share. Do not upload personal information or private sheet music.

Use Python 3.12, install `.[dev]`, run `ruff check src tests scripts` and `pytest -q`. Changes to engraving or OMR must be tested against the real tools, not only mocks. Preserve upstream jianpu-ly unchanged; propose fixes upstream or document an explicit patch separately. New visual importer features must prove they do not silently drop musical content.

Keep the beginner interface in Traditional Chinese. Explain errors in terms of notes and measures. Update documentation and third-party notices with runtime changes. Pull requests should describe the user-visible behavior and the tests run.
