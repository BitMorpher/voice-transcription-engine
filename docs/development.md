# Development and offline verification

```bash
uv lock --check
uv sync --locked
uv run --locked pytest -q
uv run --locked ruff check .
uv build
uv run --locked interview --help
uv run --locked interview transcribe --help
uv run --locked interview batch --help
uv run --locked voice-transcribe --help
uv run --locked voice-batch --help
# Include the optional notebook compatibility roundtrip:
uv sync --locked --extra notebook
uv run --locked --extra notebook pytest -q
```

Tests generate synthetic tones and color video in temporary folders, mock all provider responses, and block Python network connections. Coverage includes actual FFmpeg conversion, metadata removal, WAV/MP3/M4A compatibility, exact byte/duration chunk coverage, model selection/capability gates, user-supplied hints, long-text reassembly, faithfulness checks, failed chunks, truncation/refusal handling, missing tools/key, corrupt/no-audio input, privacy of logs, output conflicts, and configuration-aware resume checks, raw-checksum-bound derivative recovery, parser-value redaction, meaningful Unicode marks, symbol identity/order, canonical-equivalent spellings, prior editing-contract invalidation and empty-input safety. The default suite skips the optional PyDub test; installing the notebook extra exercises a synthetic M4A chunk roundtrip. FFmpeg-dependent tests skip if binaries are absent; no tests use real media or make paid OpenAI calls. SDK contract tests use a local mock transport to check real serialization, response parsing and privacy of transport logs.

Builds produce ignored `dist/` wheel and source archives using the pinned backend. The source archive includes the lock, Python pin and complete offline tests. Source archives may include operating-system ownership metadata; keep them private until inspected. Review archive contents before sharing; no generated media, transcripts, keys, local environment or personal paths belong in a distribution. A clean environment can be checked without disturbing `.venv` using `UV_PROJECT_ENVIRONMENT=private/clean-venv uv sync --locked`. The lock covers declared dependencies across supported Python versions; the validated runtime is CPython 3.14.8 on macOS arm64, not a full operating-system/Python matrix.

Modules: `src/studio_cli.py` dispatches the unified `interview transcribe` and `interview batch` commands without changing their options or state; `src/cli.py` manages commands, `src/media.py` prepares local audio, `src/pipeline.py` tracks stages, `src/transcriber.py` streams bounded API chunks, `src/private_output.py` writes private artifacts, `src/model_config.py` validates model/hint settings, and `src/text_editing.py` checks bounded faithful edits. Author stages use `src/author_workflow.py`, `src/author_review.py`, `src/review_export.py`, `src/chapters.py`, and packaged versioned prompts; see the [architecture table](author-workflow.md#implementation-and-verification). The wheel packages these source modules under `voice_transcription_engine`; installed entrypoints use relative imports and packaged prompts. `src/batch/` separates plan validation, staging/integrity, phase gates and CLI orchestration; `src/progress.py` handles allowlisted execution logs and idle heartbeats. `src/terminal_progress.py` turns those safe events into readable messages and a live status panel.

The distribution is `interview-studio`; the `voice_transcription_engine` import namespace is intentionally preserved for installed integrations and prompts. `tests/test_studio_cli.py` covers dispatch, help, private diagnostics and child mode gates. Installed-wheel tests exercise both old and new commands, including legacy-created output reuse without extra provider calls. Historical validation notes retain the commands used at the time. See [migration](migration.md) for installation collisions and path handling.
