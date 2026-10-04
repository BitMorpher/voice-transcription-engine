# Ordered interview implementation validation

Implemented locally on `feat/ordered-interview`, from fresh remote `main` at `49e8f383c1604026220e6bf9448876db309916fa` (merged PR #9). Remote main was reconfirmed at the same commit on 2026-10-04. Inspection found folder batch mode created independent filename-sorted jobs; it had no ordered multipart interview feature.

The new entrypoint is `voice-transcribe --workflow --interview-manifest`. See [the manifest, commands and limitations](ordered-interviews.md). The implementation adds two focused modules and reuses existing media conversion, part pipeline, faithful editing, author review/export and chapter stages. No new dependency, Python pin or lock change was introduced.

## Final checks

- CPython 3.14.8 and uv 0.12.23; FFmpeg/ffprobe available.
- `uv run --locked --extra notebook pytest -q`: **445 passed**.
- Separate environment: `UV_PROJECT_ENVIRONMENT=private/default-env uv run --locked pytest -q`: **444 passed, 1 expected optional-notebook skip**.
- Multipart suite: **66 passed**, included in both full suites. It covers explicit nonlexical order, mixed audio/video, exact Unicode/symbol/CRLF retention, part and combined source hashes, local segment/character citations, boundary-spanning findings and chunk coverage, duplicate IDs/JSON keys/files/hardlinks, invalid versions/paths/media, corrupt/audio-less media, empty/error ASR, failed polish, source changes during ASR, failed-part resume, reordered/added/removed/renamed/changed parts, model/hint/prompt changes, tampered raw/audio/manifests/provenance/author bundles, symlinks/output collisions/locks, unresolved-high blocking/override, literal XLSX source and installed CLI help. Existing single-file/folder compatibility and fidelity contract 3 checks remain in the full suite.
- Ruff, `git diff --check`, offline `uv lock --check`, and locked runtime dependency compatibility passed.
- Wheel and source distribution built with the pinned backend. Both contain the new modules and packaged prompts.
- Installed the wheel into an ignored clean environment with exact locked runtime dependencies, then ran Python in isolated mode outside source imports. Synthetic audio/video, exact raw text, combined polish, review JSON/XLSX, both chapter styles, verified resume and reordered-generation behavior passed with mocked providers and blocked outbound sockets. Reordering reused both original part ASR results.
- The reported pilot `cli` import failure did not reproduce in a fresh editable install or the installed wheel. A launcher-help regression runs outside the checkout. No unrelated packaging migration was needed.
- Public source/tests/docs and wheel/source-distribution members were scanned for credential, email, machine-path and private-media/artifact patterns. No such patterns or private files were found; all 744 locked archive URLs use public PyPI hosts. Notebook code outputs and execution counts remain clear. These are scoped checks, not a general security guarantee. Source archives retain ordinary local tar ownership metadata and should be inspected before any future sharing.

## Scope and limits

All recordings and provider responses used for validation were synthetic. Tests clear API keys and block network sockets. No real interview reads, copies, moves, uploads or paid provider calls were performed. Previous task checkouts, pilot scripts, original recordings and outputs were preserved. No Drive integration, push, PR, merge or publication was performed.

Raw ASR is automated and unverified and still needs listening. Recording references are local Unicode text offsets and segments, not audio timestamps; no continuous timeline across pauses is fabricated. Review coverage is model acknowledgement, not proof of exhaustive detection. Editorial wording contracts do not verify truth, ASR accuracy or publication suitability. Workbook decisions are not imported as approval.

Temporary environments, test/build logs, wheel smoke script and generated distributions remain ignored under `private/` or `dist/`. There are no remaining implementation blockers. User/provider/model accuracy testing on actual interviews is outside this offline validation.
