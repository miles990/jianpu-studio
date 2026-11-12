# Working on Jianpu Studio

Read README.md for the user-facing contract, docs/development.md for commands and release gates, and docs/uiux-research.md for the design rationale. Inspect `git status` before editing. User instructions override this guide.

## Delivery

- Work trunk-based on `main` in small, runnable increments. Do not create long-lived feature branches or move published tags.
- Agree on the acceptance criteria for the current version. Keep the existing usable release available while larger work is in progress.
- A coordinator owns Git mutations and releases. Parallel agents own disjoint files or read-only reviews; communicate interface changes before editing consumers. Never stash, reset, or overwrite another agent's work.
- Commit and push completed milestones after relevant checks. Do not commit placeholders or expose unfinished actions as working features.
- The coordinator reviews the combined diff and runs integration checks. An agent saying “done” is not test evidence.

## Product constraints

- Traditional Chinese UI for ordinary Windows users familiar with EOP; the main use case is erhu numbered notation.
- Keep common note editing visible, advanced notation discoverable, and exact insert/replace/selection behavior clear.
- Preserve projects, recovery, undo/redo, installation paths and user settings. Support Chinese paths and spaces.
- Distinguish editable draft, real PDF output, and original photo. OMR needs proofreading; do not promise arbitrary handwritten/numeric-image recognition.
- Never edit vendored upstream code casually or strip licenses. Keep external engine processes isolated, cancellable and bounded.
- Keep README concise and user-oriented. Technical details, research and limitations belong in linked docs; document only capabilities actually released.

## Validation

Use the repository virtual environment when present. On Linux:

```sh
QT_QPA_PLATFORM=offscreen JIANPU_LILYPOND=/path/to/lilypond .venv/bin/pytest -q
.venv/bin/ruff check src tests scripts
python scripts/check_version.py
```

Run targeted tests during implementation, then the full relevant regression suite before integration. UI changes need real interactions and screenshots at normal and small window sizes; inspect the images. Engine changes need real LilyPond/OMR output. Updates need installation-scope detection, integrity, cancellation, unsaved-work protection and upgrade preservation tests. Do not use screenshot appearance as proof of musical correctness.

Record performance environment and measurements; do not invent speedups or compare different workloads. Keep scratch outputs in `work/`; only intentional screenshots/docs belong in Git.

## Release

Tags trigger the complete Windows build, frozen GUI/engine tests, installation/uninstallation and upgrade gates. Publish only those tested artifacts, verify the public download and SHA-256, and provide the actual release link. CI success without a downloadable release is not a completed delivery. No extra approval is needed for work already authorized by the user.

This guidance applies the project-specific parts of OpenAI's agent-workflow guidance and DORA small-batch CI practices; it is not a claim that any single process is universally best.
