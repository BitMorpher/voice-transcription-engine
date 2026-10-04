# Voice Transcription Engine

Convert video to audio locally, then transcribe it with OpenAI's hosted Whisper API using one CLI. The default output is the original API transcription, with no AI rewriting. Existing WAV, MP3, and M4A folder commands and output naming remain supported.

## Requirements and setup

- Python 3.9 or newer.
- FFmpeg **and ffprobe** available on `PATH`. Check with `ffmpeg -version` and `ffprobe -version`. Install them yourself through your trusted package manager if missing.
- The `openai` Python SDK for transcription; extraction alone uses only Python's standard library and FFmpeg.
- Your own OpenAI account, API key, quota, and network access for transcription. Hosted API use can incur charges; this project does not run a local Whisper model.

In a virtual environment, install the CLI with your preferred package manager:

```bash
uv venv
uv pip install .
# Development only:
uv pip install -r requirements-dev.txt
```

The equivalent `python -m pip install .` works in an existing virtual environment. These commands install dependencies; review them before running. No installer or credential setup runs automatically.

The legacy experimental notebook additionally needs PyDub (`uv pip install '.[notebook]'`). The CLI no longer needs local Whisper, Torch, NumPy, or SciPy.

Set `OPENAI_API_KEY` yourself in the current process environment, preferably through your secret manager. An interactive terminal prompt avoids putting the key into shell history:

```bash
read -rs OPENAI_API_KEY
export OPENAI_API_KEY
```

This prompt syntax works in bash/zsh. Never paste a real key into a command, notebook, tracked file, or chat. `.env.example` contains a placeholder; `.env` files are ignored and **are not automatically loaded**. The CLI never creates persistent credentials. SDK environment settings, including a custom base URL, remain user-managed.

## One command: video/audio → WAV → transcription

Keep source media outside the checkout, or under ignored `private/input/`. Only local regular files are accepted; symlinks and network media inputs are rejected.

```bash
voice-transcribe --pipeline --input private/input/synthetic.mp4 --output-folder private/output
# Process supported files in a mixed folder, nonrecursively:
voice-transcribe --pipeline --input-folder private/input --output-folder private/output
# Continue a previous run, verifying completed artifacts before skipping them:
voice-transcribe --pipeline --input-folder private/input --output-folder private/output --resume
```

Without installing the CLI, run the same options with either `python src/cli.py` or `python -m src.cli` from the checkout.

Videos: `.mp4`, `.mov`, `.mkv`, `.webm`, `.avi`, `.m4v`. Audio: `.wav`, `.mp3`, `.m4a`. Extensions are case-insensitive; FFprobe checks for an audio stream and FFmpeg must be able to decode the container/codecs. Files without audio or corrupt media fail clearly. Unsupported files in folders are skipped; subfolders are not traversed. Unsupported single files and empty input folders fail.

All media is normalized to a mono, 16 kHz, 16-bit PCM WAV. For video, the **first audio stream** is selected; alternate languages/tracks are not combined. Normalization may change fidelity and stereo information. Source files are never modified. Source metadata, chapter tags, subtitles, artwork, and video streams are not copied. FFmpeg operations have a configurable time limit (`--media-timeout 3600` by default); FFprobe is limited to 30 seconds. Very large prepared WAV files at or above 4 GiB are rejected rather than risking WAV size overflow; split those sources into smaller recordings first.

## Extract audio only, entirely locally

```bash
voice-transcribe --extract-only --input private/input/synthetic.mp4 --output-folder private/output
# Transcribe the same source later, reusing the verified extraction:
voice-transcribe --pipeline --input private/input/synthetic.mp4 --output-folder private/output --resume
```

Extraction does not import the OpenAI SDK, require a key, or make API calls. `--extract-only` also accepts supported audio files for normalization. It cannot be combined with enhancement.

## Outputs, resumability, and failures

Pipeline output is stored under an opaque job ID:

```text
private/output/<job-id>/
  audio.wav                     # local intermediate
  transcription.txt             # original API transcript
  manifest.json                 # version, source checksum, model, stage checksums/status
  derivative_readability.txt    # only with explicit enhancement
```

Job IDs hash the source basename and entire file contents; they contain no plaintext names or paths. Different extensions with the same stem cannot collide in ordinary use. Identical content and basename reuse the same job even if moved; changing either creates a new job. Hashes can still link known source files and should be treated as private. Generated directories use owner-only permissions (0700); artifacts use 0600 on supported local filesystems. Storage is not encrypted.

The default output directory is ignored `private/output`. In a Git checkout, the CLI refuses output locations outside `private/` or `data/`. These trees are ignored in this repository. Keep artifacts in those trees or outside **all** repositories; an unrelated checkout may have different ignore rules. Original inputs and common generated formats are also ignored as a second layer. Gitignore is not access control, and forced staging can bypass it.

- Runs never overwrite an existing audio/transcript artifact. A repeat run requires `--resume`.
- Resume checks the manifest version, source checksum, model, and SHA-256 of each complete artifact. Missing artifacts can be regenerated; existing artifacts with a missing, invalid, or mismatched record are conflicts. Use a fresh output folder for conflicting/tampered artifacts or changed processing configuration.
- Completed conversion and transcription stages are skipped separately. A failed enhancement can resume without retranscribing. A failed transcription retains the prepared audio and retries transcription. **API chunks are not checkpointed**: retrying a failed transcription stage can repeat successful chunk requests and charges.
- Text is published atomically only after every chunk succeeds. Any failed or malformed chunk fails the whole transcription stage; there is no full-file fallback, error text in the transcript, or silent successful partial transcript.
- The original audio is streamed into exact PCM frame chunks of at most 20 MiB each, below the documented 25 MB upload limit. Every frame, including the final short tail, is processed in order. Fixed boundaries can split speech mid-sentence and affect recognition quality; check the transcript against the recording.
- A private lock prevents concurrent processing of the same job. If a process is killed, verify that it has stopped before manually deleting its job's `.lock`. A crash between publishing an artifact and recording its checksum produces a safe conflict; use a fresh output directory.
- JSON progress reports show item indices, opaque IDs, stage statuses, and sanitized guidance. Errors report a nonzero exit status and processing continues for other files. No transcript, source name/path, key, raw provider error, FFmpeg diagnostic, or traceback is logged. Identify failing source items by their position in the sorted supported input list; inspect private artifacts locally. There is no unsafe debug switch.

## Existing audio folder workflow

```bash
python src/cli.py --input_folder private/input --output_folder private/audio-transcripts
```

This mode processes WAV/MP3/M4A files and preserves `<stem>_transcription.txt` names. Video files remain skipped unless you select pipeline mode. Existing output files, including same-stem collisions, fail rather than overwrite. Resume manifests apply only to pipeline mode.

Legacy `--enhance_for_reading` and `--format_as_interview` remain explicit opt-ins. The interview flag is limited to legacy audio mode; it requests formatting existing dialogue, without fabricated questions or inferred identities. It may still produce inaccurate content and requires human verification.

Pipeline mode supports an optional readability derivative:

```bash
voice-transcribe --pipeline --input private/input/synthetic.mp4 --enhance-for-reading
```

Enhancement sends the transcript to `gpt-4.1-mini` as an additional paid request. Derivatives carry an AI label and never replace the original transcription. Incomplete responses (including token-limit truncation) fail; the complete original remains saved. Long transcripts may exceed the enhancement limit; retain the original rather than accepting a truncated derivative. The CLI does not claim that AI rewriting is faithful or that Whisper recognition is error-free.

## Privacy boundaries

Conversion runs on your machine. Transcription sends the **normalized audio** to OpenAI; this can contain voices, names, and other sensitive spoken content even after container metadata is stripped. The multipart filename is generic `audio.wav`. Optional enhancement separately sends transcript text. Review your authorization to process the material and OpenAI's current data controls before using real recordings. This tool makes no zero-retention promise and cannot prevent disclosures present in the audio or transcript itself. It does not download from Drive, publish artifacts, or upload to GitHub.

Keep API keys and source/output folders private; delete retained media, transcripts, manifests, and backups according to your own retention policy. Temporary normalization files are removed on normal completion/error; abrupt termination can leave private temporary directories. SDK/network debug logging is suppressed by the transcriber to avoid accidental credential/payload logging. No real recordings, personal transcripts, keys, or identifying fixtures belong in this public repository.

Official references: [OpenAI transcription formats and limits](https://developers.openai.com/api/docs/guides/speech-to-text), [OpenAI API data controls](https://developers.openai.com/api/docs/guides/your-data), and [FFmpeg stream/metadata mapping](https://ffmpeg.org/ffmpeg.html).

## Offline verification

```bash
python -m pytest tests/ -q
```

Tests generate synthetic tones and color video in temporary folders, mock all provider responses, and block Python network connections. Coverage includes actual FFmpeg conversion, metadata removal, WAV/MP3/M4A compatibility, exact chunk coverage, failed chunks, missing tools/key, corrupt/no-audio input, privacy of logs, output conflicts, and resume checks. FFmpeg-dependent tests skip if binaries are absent; no tests use real media or make paid OpenAI calls.

Modules: `src/cli.py` manages commands, `src/media.py` prepares local audio, `src/pipeline.py` tracks stages, `src/transcriber.py` streams bounded API chunks, and `src/private_output.py` writes private artifacts. Packaging now exposes the existing flat modules through `voice-transcribe`; this feature does not migrate the legacy project to a new package layout.
