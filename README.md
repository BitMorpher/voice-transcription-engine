# Voice Transcription Engine

Convert video to audio locally, then transcribe it with OpenAI's hosted Transcriptions API using one CLI. The default output is the original API transcription, with no AI rewriting. Existing WAV, MP3, and M4A folder commands and output naming remain supported.

## Requirements and setup

- Python **3.14 or newer**; the development/runtime pin is **3.14.8** in `.python-version`. Python 3.9 is no longer supported. The 3.14 floor follows this project's current environment standard; it is not a claim that the SDK itself requires 3.14.
- [uv](https://docs.astral.sh/uv/getting-started/installation/) **0.12.23 or newer**. Older versions may not know the pinned Python release.
- FFmpeg **and ffprobe** available on `PATH`. Check with `ffmpeg -version` and `ffprobe -version`. They are external system prerequisites, not Python packages; install them yourself through your trusted package manager if missing.
- Your own OpenAI account, API key, quota, and network access for transcription. Hosted API use can incur charges; this project does not run a local Whisper model.

From the checkout root:

```bash
uv python install 3.14.8
uv sync --locked
uv run --locked voice-transcribe --help
```

`uv sync --locked` creates the ignored `.venv`, installs the editable CLI, runtime dependencies and the default `dev` group using committed `uv.lock`. The Python pin selects the standard CPython runtime, not a free-threaded build. Setup may download Python and packages; it never installs FFmpeg or configures credentials. Once synced, use `uv run --locked` for commands without activating the environment. For a runtime-only installation, use `uv sync --locked --no-dev` and `uv run --locked --no-dev voice-transcribe --help` (plain `uv run` would reinstall the default development group).

`pyproject.toml` is the only dependency declaration: `openai` and `openpyxl` are runtime dependencies; `dev` contains pytest and Ruff. OpenPyXL writes the author review workbook. Optional `notebook` contains PyDub, `audioop-lts`, ipykernel and JupyterLab. `uv.lock` records exact versions, public PyPI locations and hashes for all groups/extras. Build-backend versions are pinned separately in `[build-system]`, since uv's project lock does not lock isolated build requirements. FFmpeg is not managed by the lock. The CLI does not need PyDub, local Whisper, Torch, NumPy or SciPy.

The existing experimental notebook is available separately:

```bash
uv sync --locked --extra notebook
uv run --locked --extra notebook jupyter lab notebook/development.ipynb
```

`audioop-lts` provides the module PyDub needs after Python [removed `audioop` in 3.13](https://docs.python.org/3/library/audioop.html). These notebook dependencies are excluded from the default CLI install. The blank notebook input must be supplied locally; keep any outputs/media and saved notebook results private. Clear notebook outputs before committing changes.

The old `setup.py` and hand-maintained requirements files have been replaced. Standard Python installers can still install this PEP 517/621 project with `python -m pip install .` in a compatible virtual environment, but that command does not consume `uv.lock`. If a pip requirements export is needed, generate it from the lock into ignored storage:

```bash
mkdir -p private
uv export --locked --no-dev --no-emit-project --format requirements.txt --output-file private/requirements.txt
```

To deliberately update dependencies, edit/add them with uv, run `uv lock`, review the lock diff and run the checks below. `uv lock --check` and `--locked` refuse a stale lock instead of silently updating it. See the official [uv project sync documentation](https://docs.astral.sh/uv/concepts/projects/sync/) for group/extra selection.

Set `OPENAI_API_KEY` yourself in the current process environment, preferably through your secret manager. An interactive terminal prompt avoids putting the key into shell history:

```bash
read -rs OPENAI_API_KEY
export OPENAI_API_KEY
```

This prompt syntax works in bash/zsh. Never paste a real key into a command, notebook, tracked file, or chat. `.env.example` contains a placeholder; `.env` files are ignored and **are not automatically loaded**. The CLI never creates persistent credentials. SDK environment settings, including a custom base URL, remain user-managed.

## One command: video/audio → WAV → transcription

Keep source media outside the checkout, or under ignored `private/input/`. Only local regular files are accepted; symlinks and network media inputs are rejected. Empty `--input`, `--input_folder` and `--input-folder` values fail before constructing a path, reading hints or initializing tools/providers; they never select the current directory. Whitespace in a nonempty path is preserved.

```bash
uv run --locked voice-transcribe --pipeline --input private/input/synthetic.mp4 --output-folder private/output
# Process supported files in a mixed folder, nonrecursively:
uv run --locked voice-transcribe --pipeline --input-folder private/input --output-folder private/output
# Continue a previous run, verifying completed artifacts before skipping them:
uv run --locked voice-transcribe --pipeline --input-folder private/input --output-folder private/output --resume
```

Without installing the CLI, run the same options with either `uv run --locked python src/cli.py` or `uv run --locked python -m src.cli` from the checkout.

Videos: `.mp4`, `.mov`, `.mkv`, `.webm`, `.avi`, `.m4v`. Audio: `.wav`, `.mp3`, `.m4a`. Extensions are case-insensitive; FFprobe checks for an audio stream and FFmpeg must be able to decode the container/codecs. Files without audio or corrupt media fail clearly. Unsupported files in folders are skipped; subfolders are not traversed. Unsupported single files and empty input folders fail.

All media is normalized to a mono, 16 kHz, 16-bit PCM WAV. For video, the **first audio stream** is selected; alternate languages/tracks are not combined. Normalization may change fidelity and stereo information. Source files are never modified. Source metadata, chapter tags, subtitles, artwork, and video streams are not copied. FFmpeg operations have a configurable time limit (`--media-timeout 3600` by default); FFprobe is limited to 30 seconds. Very large prepared WAV files at or above 4 GiB are rejected rather than risking WAV size overflow; split those sources into smaller recordings first.

## Extract audio only, entirely locally

```bash
uv run --locked voice-transcribe --extract-only --input private/input/synthetic.mp4 --output-folder private/output
# Transcribe the same source later, reusing the verified extraction:
uv run --locked voice-transcribe --pipeline --input private/input/synthetic.mp4 --output-folder private/output --resume
```

Extraction does not import the OpenAI SDK, require a key, or make API calls. `--extract-only` also accepts supported audio files for normalization. It cannot be combined with enhancement.

## Multiple recordings from one interview

Use an explicit ordered JSON manifest with `--workflow --interview-manifest`; folder batches remain independent jobs. Mixed audio/video parts keep separate raw transcripts and combine in the exact supplied sequence, followed by one interview-wide polish/review/chapter workflow. See [ordered interview usage and resume behavior](docs/ordered-interviews.md).

```bash
uv run --locked voice-transcribe --workflow \
  --interview-manifest private/input/interview_manifest.json \
  --stages raw,polish,review --chapters both --output-folder private/ordered-output
```

## Author review and chapter comparison

Use the same CLI with `--workflow` and a local audio/video path. Default stages retain the immutable raw transcript, create a separate faithful punctuation/layout polish, then review the **raw source** into JSON and an Excel workbook. Chapter generation is optional:

```bash
uv run --locked voice-transcribe --workflow \
  --input private/input/synthetic.mp4 --media-type auto \
  --output-folder private/author-review

uv run --locked voice-transcribe --workflow \
  --input private/input/synthetic.mp4 --media-type video \
  --stages raw,review --chapters both \
  --output-folder private/author-comparison
```

Flags use a low/medium/high rubric, exact verified raw excerpts, stable IDs and source references. They prompt nuanced human review; criticism is not automatically high priority, false, or unsuitable for publication. Failed/incomplete reports are distinct from a complete report with no findings. Unresolved high findings block chapters unless `--draft-with-unresolved-high` explicitly requests visibly warned drafts; an incomplete review always blocks drafting.

Interview drafts retain testimony excerpts without invented questions or speaker identities. Narrative drafts conservatively group source words in their original order; third-person mode frames exact testimony rather than inventing a story. Every passage carries provenance and linked findings, with explicit omissions, uncertainties and editorial changes. All drafts require human review and recording verification. Workbook disposition/reviewer edits are a human record, not imported approval; copy the workbook before editing because bundle changes invalidate resume.

See the [complete author workflow guide](docs/author-workflow.md) for stage/style options, the review rubric, the high-priority gate and retry examples, Excel/JSON usage, provenance, integrity, limitations, and architecture. Input files are local, but transcription/polish/review/narrative requests use the hosted OpenAI API and can incur charges.

## Outputs, resumability, and failures

Pipeline output is stored under an opaque job ID:

```text
private/output/<job-id>/
  audio.wav                     # local intermediate
  transcription.txt             # original API transcript
  manifest.json                 # version, source/configuration hashes, model, stage status/checksums
  derivative_readability.txt    # only with explicit enhancement
```

Job IDs hash the source basename and entire file contents; they contain no plaintext names or paths. Different extensions with the same stem cannot collide in ordinary use. Identical content and basename reuse the same job even if moved; changing either creates a new job. Hashes can still link known source files and should be treated as private. Generated directories use owner-only permissions (0700); artifacts use 0600 on supported local filesystems. Storage is not encrypted.

The default output directory is ignored `private/output`. In a Git checkout, the CLI refuses output locations outside `private/` or `data/`. These trees are ignored in this repository. Keep artifacts in those trees or outside **all** repositories; an unrelated checkout may have different ignore rules. Original inputs and common generated formats are also ignored as a second layer. Gitignore is not access control, and forced staging can bypass it.

- Runs never overwrite an existing audio/transcript artifact. A repeat run requires `--resume`.
- Resume checks manifest version 2, the source checksum, ASR model/hint/chunk fingerprints, editing configuration, and SHA-256 of each complete artifact. Enhancement verification also binds the editing contract/settings to the exact raw-transcript SHA-256; a regenerated transcript with different bytes cannot reuse an existing derivative, even when its words are equivalent. Missing artifacts can be regenerated; existing artifacts with a missing, invalid, or mismatched record are conflicts. Use a fresh output folder for conflicting/tampered artifacts or changed processing configuration. Completed raw transcripts cannot be replaced by changing model or hints. If only extraction or a failed ASR stage exists, new hints/model can be supplied with `--resume` without redoing verified conversion.
- Earlier manifests: version 1 requires a fresh output folder; retain its original transcripts. Version 2 conversion/raw-transcription records remain compatible. Older enhancement records either lack an input binding or used a weaker editing contract for Unicode marks/symbols. Existing derivatives are conflicts, never automatically trusted or overwritten; use a fresh output folder to retain them. If the derivative is absent, it can be regenerated against verified raw text with the current contract.
- Completed conversion and transcription stages are skipped separately. A failed enhancement can resume without retranscribing. A failed transcription retains the prepared audio and retries transcription. **API chunks are not checkpointed**: retrying a failed transcription stage can repeat successful chunk requests and charges.
- Text is published atomically only after every chunk succeeds. Any failed or malformed chunk fails the whole transcription stage; there is no full-file fallback, error text in the transcript, or silent successful partial transcript.
- The original audio is streamed into exact PCM frame chunks capped at both 20 MiB and five minutes by default, below the documented 25 MB upload limit. The duration cap is an application choice, configurable with `--audio-chunk-seconds` from 1 to 600 seconds, rather than a claim about the model's duration limit. Every frame, including the final short tail, is processed in order. Fixed boundaries can split speech mid-sentence and affect recognition quality; check the transcript against the recording.
- A private lock prevents concurrent processing of the same job. If a process is killed, verify that it has stopped before manually deleting its job's `.lock`. A crash between publishing an artifact and recording its checksum produces a safe conflict; use a fresh output directory.
- JSON progress reports show item indices, opaque IDs, stage statuses, and sanitized guidance. All parser errors use fixed diagnostics and declared option names, never supplied values; usage always identifies the CLI as `voice-transcribe`. Recognized flags with accidental `=value`, ambiguous/unknown options, invalid numbers and missing/conflicting options receive safe guidance and `--help`. Errors report a nonzero exit status and processing continues for other files. No transcript, source name/path, key, raw provider error, FFmpeg diagnostic, or traceback is logged. Identify failing source items by their position in the sorted supported input list; inspect private artifacts locally. There is no unsafe debug switch.

## Existing audio folder workflow

```bash
uv run --locked python src/cli.py --input_folder private/input --output_folder private/audio-transcripts
```

This mode processes WAV/MP3/M4A files and preserves `<stem>_transcription.txt` names. Video files remain skipped unless you select pipeline mode. Existing output files, including same-stem collisions, fail rather than overwrite. Resume manifests apply only to pipeline mode.

## Models and user-supplied transcription hints

The quality-first default is **`gpt-transcribe`**, the model recommended by current OpenAI documentation for general-purpose file transcription. Optional faithful editing defaults to **`gpt-6-astra`** with high reasoning, because OpenAI currently identifies it as its most capable model and quality is the priority here. This is a documentation-based selection, not an empirical quality claim or benchmark on your recordings. No real audio or paid calls were used to evaluate these models.

```bash
uv run --locked voice-transcribe --pipeline --input private/input/synthetic.mp4 --model gpt-transcribe
# Optional known context and literal terms, supplied by you:
uv run --locked voice-transcribe --pipeline --input private/input/synthetic.mp4 \
  --context-file private/hints/context.txt --glossary-file private/hints/glossary.txt \
  --language en --language fr
```

The context file is UTF-8 text about the actual recording. The glossary is UTF-8, one literal expected term per nonblank line; duplicate terms are removed. Include only terms you have reason to expect. Language hints use lowercase ISO 639 codes; repeat `--language` for multilingual/code-switched audio. Current docs support ISO 639-1 and selected ISO 639-3 codes; syntax is checked locally and unsupported codes can be rejected by the API. No glossary, language, context, speaker identity, or previous-chunk prompt is invented or inferred from filenames. With no hint flags, none are sent.

For `gpt-transcribe`, context maps to `prompt`, and `keywords`/`languages` use the documented Python `extra_body` fields. The CLI requests JSON text output; it does not send Whisper timestamp parameters, subtitles, diarization options, or assume speaker labels. The source API text remains unchanged inside the ordered raw transcript. Exact frame coverage does not prove the model recognized every word; review ASR omissions/errors against the recording.

Application safety limits: context ≤8192 UTF-8 bytes, at most 100 glossary terms of ≤256 bytes each, at most 16 language hints, and hint files ≤64 KiB. Store real hints under ignored `private/hints/` or outside all repositories. Hints are sent to OpenAI, so they may contain sensitive information. Only a configuration hash, not their text or file paths, is persisted in the private manifest or reported in logs.

`--model` also accepts `whisper-1`, `gpt-4o-transcribe`, and `gpt-4o-mini-transcribe` for explicit legacy compatibility. These support context plus one ISO 639-1 `language`; this CLI rejects glossary and multiple-language options for them instead of sending incompatible fields. OpenAI's [2026-08-26 deprecation notice](https://developers.openai.com/api/docs/deprecations#2026-08-26-transcription-models) schedules removal of those legacy transcription models on **February 26, 2027**. There is no automatic fallback to a deprecated model when the new model is inaccessible. Model/account access and limits must be checked by the user.

## Optional faithful text editing

```bash
uv run --locked voice-transcribe --pipeline --input private/input/synthetic.mp4 --enhance-for-reading
# Explicit alternative from the current documented model family:
uv run --locked voice-transcribe --pipeline --input private/input/synthetic.mp4 \
  --enhance-for-reading --editing-model gpt-6.1-sol
```

`--editing-model` accepts `gpt-6-astra` (default) or `gpt-6.1-sol`. Editing remains opt-in and sends transcript chunks as additional paid Chat Completions requests. Both use high reasoning, structured JSON output, no unsupported temperature setting, an input-sized completion budget of 16,384–32,768 tokens including reasoning, and `store=false`. This retention setting does not waive OpenAI's other data-processing controls.

Editing permits **punctuation, capitalization, and paragraph layout only**. It must preserve repetitions, disfluencies, numbers, uncertainty markers, and every word and symbol in its original order. The code checks each output and the final reassembly against the source's case-insensitive Unicode word sequence and ordered symbol tokens. Canonical NFC normalization accepts equivalent composed/decomposed spellings; all Unicode mark categories and zero-width joining controls are preserved in comparisons. Every Unicode symbol category (currency, math, modifier and other symbols) contributes separate ordered tokens, so changing, deleting, adding or moving currency signs, operators, emoji or emoji modifiers fails. Changes to vowel signs, accents or joining controls also fail; no compatibility normalization is applied. The text itself is not normalized or rewritten by this comparison. Added questions, answers, speaker labels/roles, paraphrases, omissions, or reordered words fail the editing stage even if the provider reports successful completion. A mechanical word check is conservative and cannot prove semantic equivalence: punctuation can change interpretation, and tokenization can reject otherwise reasonable changes in some scripts. Human verification remains necessary.

Long transcripts are partitioned into contiguous chunks of at most 6000 UTF-8 bytes, preferring whitespace boundaries and preserving every character in the source partition. Each response must return the expected chunk index, text, and a boolean speaker-uncertainty flag. Chunks are reassembled in order with boundary whitespace restored; there is no overlap, deduplication, summarization, or successful partial derivative. Refusals, malformed JSON, wrong chunk indices, non-stop completion reasons (including token exhaustion), or a later chunk failure fail the entire derivative. There is no automatic rewriting retry or silent 2048-token cutoff. Editing chunks are not checkpointed, so a failed stage may repeat paid editing requests on retry.

Derivatives carry an AI label, explicitly mark speaker identities/turn boundaries as unverified, and flag chunks where the editor reports attribution uncertainty. No speaker identity or turn is inferred. Editing reads a single verified raw snapshot and rechecks its byte checksum before publishing a derivative; detected raw changes fail without publishing. The private job lock protects against concurrent pipeline runs, not arbitrary external file edits. The raw `transcription.txt` is never overwritten by the pipeline. An editing failure leaves the raw transcript intact. Changing editing model cannot overwrite an existing derivative; use a fresh output folder.

Legacy `--enhance_for_reading` remains supported. `--format_as_interview` is retained only as a legacy audio-mode alias for the same faithful layout operation and existing filename; it no longer asks for interview reconstruction or speaker-role assignment. It does not turn a monologue into an interview.

Official selection/compatibility references: [GPT-Transcribe](https://developers.openai.com/api/docs/models/gpt-transcribe), [ASR context and languages](https://developers.openai.com/api/docs/guides/speech-to-text), [GPT-6 Astra](https://developers.openai.com/api/docs/models/gpt-6-astra), and [GPT-6 migration parameters](https://developers.openai.com/api/docs/guides/latest-model).

## Privacy boundaries

Conversion runs on your machine. Transcription sends the **normalized audio** to OpenAI; this can contain voices, names, and other sensitive spoken content even after container metadata is stripped. The multipart filename is generic `audio.wav`. Optional context/glossary/language hints are also sent; faithful editing, author review, and narrative arrangement separately send transcript text. Review your authorization to process the material and OpenAI's current data controls before using real recordings. This tool makes no zero-retention promise and cannot prevent disclosures present in the audio or transcript itself. It does not download from Drive, publish artifacts, or upload to GitHub.

Keep API keys and source/output folders private; delete retained media, transcripts, manifests, and backups according to your own retention policy. Temporary normalization files are removed on normal completion/error; abrupt termination can leave private temporary directories. SDK/network debug logging, including the current SDK's HTTP transports, is suppressed by the transcriber to avoid accidental credential/payload logging. No real recordings, personal transcripts, keys, or identifying fixtures belong in this public repository.

Official references: [OpenAI transcription formats and limits](https://developers.openai.com/api/docs/guides/speech-to-text), [OpenAI API data controls](https://developers.openai.com/api/docs/guides/your-data), and [FFmpeg stream/metadata mapping](https://ffmpeg.org/ffmpeg.html).

## Offline verification

```bash
uv lock --check
uv sync --locked
uv run --locked pytest -q
uv run --locked ruff check .
uv build
uv run --locked voice-transcribe --help
# Include the optional notebook compatibility roundtrip:
uv sync --locked --extra notebook
uv run --locked --extra notebook pytest -q
```

Tests generate synthetic tones and color video in temporary folders, mock all provider responses, and block Python network connections. Coverage includes actual FFmpeg conversion, metadata removal, WAV/MP3/M4A compatibility, exact byte/duration chunk coverage, model selection/capability gates, user-supplied hints, long-text reassembly, faithfulness checks, failed chunks, truncation/refusal handling, missing tools/key, corrupt/no-audio input, privacy of logs, output conflicts, and configuration-aware resume checks, raw-checksum-bound derivative recovery, parser-value redaction, meaningful Unicode marks, symbol identity/order, canonical-equivalent spellings, prior editing-contract invalidation and empty-input safety. The default suite skips the optional PyDub test; installing the notebook extra exercises a synthetic M4A chunk roundtrip. FFmpeg-dependent tests skip if binaries are absent; no tests use real media or make paid OpenAI calls. SDK contract tests use a local mock transport to check real serialization, response parsing and privacy of transport logs.

Builds produce ignored `dist/` wheel and source archives using the pinned backend. The source archive includes the lock, Python pin and complete offline tests. Source archives may include operating-system ownership metadata; keep them private until inspected. Review archive contents before sharing; no generated media, transcripts, keys, local environment or personal paths belong in a distribution. A clean environment can be checked without disturbing `.venv` using `UV_PROJECT_ENVIRONMENT=private/clean-venv uv sync --locked`. The lock covers declared dependencies across supported Python versions; the validated runtime is CPython 3.14.8 on macOS arm64, not a full operating-system/Python matrix.

Modules: `src/cli.py` manages commands, `src/media.py` prepares local audio, `src/pipeline.py` tracks stages, `src/transcriber.py` streams bounded API chunks, `src/private_output.py` writes private artifacts, `src/model_config.py` validates model/hint settings, and `src/text_editing.py` checks bounded faithful edits. Author stages use `src/author_workflow.py`, `src/author_review.py`, `src/review_export.py`, `src/chapters.py`, and packaged versioned prompts; see the [architecture table](docs/author-workflow.md#implementation-and-verification). Packaging exposes the existing flat modules through `voice-transcribe`; this feature does not migrate the legacy project to a new package layout.
