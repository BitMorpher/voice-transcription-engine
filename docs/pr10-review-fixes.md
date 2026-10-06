# PR #10 review fixes

For current option names, every public parameter, valid combinations and parallel-interview examples, see the [command and parameter guide](cli-reference.md).

Five review findings against `e64a271` were reproduced using synthetic recordings and mocked providers. The initial reproductions had 12 failures across all five findings, with two unchanged bare-CR cases passing. No real recordings or provider requests were used.

- [Source binding before processing](https://github.com/BitMorpher/voice-transcription-engine/pull/10#discussion_r4179317343): every part conversion/transcription call carries its prevalidated source checksum. The pipeline rejects a mismatch before creating another job or converting new bytes, and rechecks immediately before ASR. A regression changes part 2 during part 1's request: only part 1 is transcribed, its result remains intact, and no combined output is published.
- [Exact newline bytes](https://github.com/BitMorpher/voice-transcription-engine/pull/10#discussion_r4179317357): the shared UTF-8 writer disables platform newline translation. LF, CRLF, bare CR, Unicode and symbols retain exact bytes for exclusive writes and replacement metadata writes. Tests simulate Windows translation and verify part/combined hashes, polish/review/chapter generation and zero-call resume. Native Windows execution was not available.
- [Chapter finding citations](https://github.com/BitMorpher/voice-transcription-engine/pull/10#discussion_r4179317370): chapter binding recomputes copied finding references as well as passage/quotation/omission references. Missing or altered finding citations fail resume even when the artifact checksum is updated; no provider calls or overwrites occur.
- [Safe polish diagnostics](https://github.com/BitMorpher/voice-transcription-engine/pull/10#discussion_r4179317386): the exact sanitized `TranscriptionError` type retains fidelity/access/quota retry guidance. Unexpected exceptions and subclasses still receive a fixed privacy-safe fallback. CLI regressions check useful errors, retained raw text, and absence of private provider messages or paths.
- [XLSX citation length](https://github.com/BitMorpher/voice-transcription-engine/pull/10#discussion_r4179317405): recording citation strings pass the existing XML/UTF-16 cell-size guard before reaching OpenPyXL. A 3,000-line source's oversized citation list fails without publishing a truncated workbook. A 2,730-line list roundtrips all 32,758 characters, and leading-equals source/part strings stay literal, never formulas. Oversized cells are rejected rather than split automatically.

## Compatibility and pilot coordination

CLI flags, version 1 input/cached manifests, private output structure, models and dependencies remain unchanged. Single-file callers need not pass the new optional `Pipeline.process(expected_source_sha256=...)` argument; ordered interviews supply it internally. Valid existing caches remain reusable. Invalid citations or altered artifacts fail closed and are not repaired or overwritten automatically. Previously translated Windows outputs require inspection/fresh output storage rather than checksum edits.

The declared package version is still `0.1.0`, so that version alone does not identify which fixes are installed. Coordinate a pilot against the exact tested Git commit or its wheel. Task 8's pilot script, environment, recording originals and previous outputs were not accessed or changed.

A launcher regression exposed an environment-specific macOS issue: this task's editable-install `.pth` file had `UF_HIDDEN` set, which CPython 3.14 deliberately skips. Clearing that flag restored the launcher, but sync reinstated the hidden file. A fresh normal wheel installation avoids this editable-path issue. This is separate from the five source fixes; no packaging layout or dependency migration was made.

## Validation

- Locked default suite in the existing separate environment: **466 passed, 1 expected optional-notebook skip**.
- Locked notebook suite in a fresh normal wheel environment (`--extra notebook --no-editable`): **467 passed**.
- Twenty-two new regression cases, including all five reproduced findings, are included in those totals.
- Ruff, whitespace and lock checks, runtime dependency compatibility, pinned wheel/source-distribution builds, installed-wheel synthetic workflow/resume/reorder, source/distribution credential and private-artifact scans passed. All 744 lock archive URLs remain public PyPI URLs; notebook outputs remain clear.

These are offline contract checks, not ASR accuracy or publication-suitability validation. Test/build logs and synthetic smoke scripts remain ignored under `private/`; generated distributions remain ignored under `dist/`. The reproduction log is retained separately from passing validation logs. No review comments or thread resolutions are posted by these fixes.


## Subsequent portable orchestration

This document records the historical feature validation above. Current installed commands, ordered selection/staging and optional parallel interviews, safe durable progress logs and transport controls are documented in [batch orchestration](batch-orchestration.md) and the [current validation guide](batch-validation.md). Reproduce the current checks from the checkout:

```bash
uv lock --check
uv run --locked pytest -q
uv run --locked --extra notebook pytest -q
uv run --locked ruff check .
uv build
uv run --locked voice-transcribe --help
uv run --locked voice-batch --help
```
