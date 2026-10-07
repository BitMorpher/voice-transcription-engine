# Processing contracts and privacy

Start with the [getting started guide](getting-started.md) and [pipeline guide](pipeline-guide.md). This reference describes the precise media, output, validation and privacy contracts.

## Audio preparation

All media is normalized to a mono, 16 kHz, 16-bit PCM WAV. For video, the **first audio stream** is selected; alternate languages/tracks are not combined. Normalization may change fidelity and stereo information. Source files are never modified. Source metadata, chapter tags, subtitles, artwork, and video streams are not copied. FFmpeg operations have a configurable time limit (`--media-timeout 3600` by default); FFprobe is limited to 30 seconds. Very large prepared WAV files at or above 4 GiB are rejected rather than risking WAV size overflow; split those sources into smaller recordings first.

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
- Resume checks manifest version 2, the source checksum, ASR model/hint/chunk fingerprints, editing configuration, and SHA-256 of each complete artifact. Enhancement verification also binds the editing contract/settings to the exact raw-transcript SHA-256; a regenerated transcript with different bytes cannot reuse an existing derivative, even when its words are equivalent. Text configurations have separate retained stage records in `derivative_versions`; selecting another editing/review configuration does not replace completed raw or prior derivatives. The first polish keeps its existing filename; additional versions use the artifact path recorded in the selected manifest stage. Missing artifacts can be regenerated only when no complete record claims them; a missing, invalid, or mismatched completed artifact is a conflict. Use a fresh output folder for conflicting/tampered artifacts. Completed raw transcripts cannot be replaced by changing model or hints. If only extraction or a failed ASR stage exists, new hints/model can be supplied with `--resume` without redoing verified conversion.
- Earlier manifests: version 1 requires a fresh output folder; retain its original transcripts. Version 2 conversion/raw-transcription records remain compatible. Older enhancement records either lack an input binding or used a weaker editing contract for Unicode marks/symbols. Existing derivatives are conflicts, never automatically trusted or overwritten; use a fresh output folder to retain them. If the derivative is absent, it can be regenerated against verified raw text with the current contract.
- Completed conversion and transcription stages are skipped separately. A failed enhancement can resume without retranscribing. A failed transcription retains the prepared audio and retries transcription. **Validated nonempty ASR chunks and structurally valid interview diarization responses are checkpointed** in pipeline/workflow mode. Matching retries reuse them; older unsaved in-memory responses cannot be recovered. See [recovery controls](recovery-controls.md).
- Text is published atomically only after every chunk succeeds. Any failed or malformed chunk fails the whole transcription stage; there is no full-file fallback, error text in the transcript, or silent successful partial transcript.
- The original audio is streamed into exact PCM frame chunks capped at both 20 MiB and five minutes by default, below the documented 25 MB upload limit. The duration cap is an application choice, configurable with `--audio-chunk-seconds` from 1 to 600 seconds, rather than a claim about the model's duration limit. Every frame, including the final short tail, is processed in order. Fixed boundaries can split speech mid-sentence and affect recognition quality; check the transcript against the recording.
- A private lock prevents concurrent processing of the same job. If a process is killed, verify that it has stopped before manually deleting its job's `.lock`. A crash between publishing an artifact and recording its checksum produces a safe conflict; use a fresh output directory.
- Structured progress events (`--progress json`, redirected auto output, and private JSONL logs) show item indices, execution IDs, stage/part/chunk statuses, elapsed time and fixed sanitized guidance. The live panel/plain messages render the same safe events in simple words. All parser errors use fixed diagnostics and declared option names, never supplied values; usage identifies the fixed command as `interview transcribe` (or `voice-transcribe` for the compatibility entrypoint). Recognized flags with accidental `=value`, ambiguous/unknown options, invalid numbers and missing/conflicting options receive safe guidance and `--help`. Errors report a nonzero exit status. Local failures remain isolated; provider admission stops after consecutive failures (default 2), definite account/configuration failures, or explicit start/request limits. No transcript, source name/path, key, raw provider error, FFmpeg diagnostic, or traceback is logged. Identify failing source items by their position in the sorted supported input list; inspect private artifacts locally. There is no unsafe debug switch.

## Existing audio folder workflow

```bash
uv run --locked interview transcribe --input_folder private/input --output_folder private/audio-transcripts
```

This mode processes WAV/MP3/M4A files and preserves `<stem>_transcription.txt` names. Video files remain skipped unless you select pipeline mode. Existing output files, including same-stem collisions, fail rather than overwrite. Resume manifests apply only to pipeline mode.

## Models and user-supplied transcription hints

The configured default transcription model is **`gpt-transcribe`**. Optional faithful editing defaults to **`gpt-6-astra`** with high reasoning. These defaults prioritize the project's documented quality goal; they are not an empirical quality claim or benchmark on your recordings. No real audio or paid calls were used to evaluate these models. Check current model availability and pricing before processing real recordings.

```bash
uv run --locked interview transcribe --pipeline --input private/input/synthetic.mp4 --transcription-model gpt-transcribe
# Optional known context and literal terms, supplied by you:
uv run --locked interview transcribe --pipeline --input private/input/synthetic.mp4 \
  --context-file private/hints/context.txt --glossary-file private/hints/glossary.txt \
  --language en --language fr
```

The context file is UTF-8 text about the actual recording. The glossary is UTF-8, one literal expected term per nonblank line; duplicate terms are removed. Include only terms you have reason to expect. Language hints use lowercase ISO 639 codes; repeat `--language` for multilingual/code-switched audio. Current docs support ISO 639-1 and selected ISO 639-3 codes; syntax is checked locally and unsupported codes can be rejected by the API. No glossary, language, context, speaker identity, or previous-chunk prompt is invented or inferred from filenames. With no hint flags, none are sent.

For `gpt-transcribe`, context maps to `prompt`, and `keywords`/`languages` use the documented Python `extra_body` fields. The CLI requests JSON text output; it does not send Whisper timestamp parameters, subtitles, diarization options, or assume speaker labels. The source API text remains unchanged inside the ordered raw transcript. Exact frame coverage does not prove the model recognized every word; review ASR omissions/errors against the recording.

Application safety limits: context ≤8192 UTF-8 bytes, at most 100 glossary terms of ≤256 bytes each, at most 16 language hints, and hint files ≤64 KiB. Store real hints under ignored `private/hints/` or outside all repositories. Hints are sent to OpenAI, so they may contain sensitive information. Only a configuration hash, not their text or file paths, is persisted in the private manifest or reported in logs.

`--transcription-model` also accepts `whisper-1`, `gpt-4o-transcribe`, and `gpt-4o-mini-transcribe` for explicit legacy compatibility. These support context plus one ISO 639-1 `language`; this CLI rejects glossary and multiple-language options for them instead of sending incompatible fields. OpenAI's [2026-08-26 deprecation notice](https://developers.openai.com/api/docs/deprecations#2026-08-26-transcription-models) schedules removal of those legacy transcription models on **February 26, 2027**. There is no automatic fallback to a deprecated model when the new model is inaccessible. Model/account access and limits must be checked by the user.

## Optional faithful text editing

```bash
uv run --locked interview transcribe --pipeline --input private/input/synthetic.mp4 --polish-text
# Explicit alternative from the current documented model family:
uv run --locked interview transcribe --pipeline --input private/input/synthetic.mp4 \
  --polish-text --editing-model gpt-6.1-sol --editing-reasoning-effort low
```

`--editing-model` accepts `gpt-6-astra` (default) or `gpt-6.1-sol`. Editing remains opt-in and sends transcript chunks as additional paid Chat Completions requests. `--editing-reasoning-effort` selects low, medium or high (default high); `--review-reasoning-effort` independently selects review and narrative-arrangement effort. The explicit `--text-profile balanced` is shorthand for Sol 6.1/low polish and Sol 6.1/medium review candidate; explicit fields override it. Neither model's review quality or latency has been established on real transcripts by this change.

Requests use structured JSON output, no unsupported temperature setting, and `store=false`. Original editing completion budgets scale with source bytes, including allowance for reasoning and JSON: low has an 8192-token floor, medium 12,288, high 16,384; the cap is 32,768. Grouped attributed polish additionally allows for IDs/JSON escaping and caps at 65,536. These are safe maximum completion allowances, not output targets or measured billed tokens. Review keeps its 32,768-token allowance and strict exact-source/coverage validation. Truncated or non-stop outputs fail rather than publishing partial text. `store=false` does not waive OpenAI's other data-processing controls.

Editing permits **punctuation, capitalization, and paragraph layout only**. It must preserve repetitions, disfluencies, numbers, uncertainty markers, and every word and symbol in its original order. The code checks each output and the final reassembly against the source's case-insensitive Unicode word sequence and ordered symbol tokens. Canonical NFC normalization accepts equivalent composed/decomposed spellings; all Unicode mark categories and zero-width joining controls are preserved in comparisons. Every Unicode symbol category (currency, math, modifier and other symbols) contributes separate ordered tokens, so changing, deleting, adding or moving currency signs, operators, emoji or emoji modifiers fails. Changes to vowel signs, accents or joining controls also fail; no compatibility normalization is applied. The text itself is not normalized or rewritten by this comparison. Added questions, answers, speaker labels/roles, paraphrases, omissions, or reordered words fail the editing stage even if the provider reports successful completion. A mechanical word check is conservative and cannot prove semantic equivalence: punctuation can change interpretation, and tokenization can reject otherwise reasonable changes in some scripts. Human verification remains necessary.

Long transcripts are partitioned into contiguous chunks of at most 6000 UTF-8 bytes, preferring whitespace boundaries and preserving every character in the source partition. Each response must return the expected chunk index, text, and a boolean speaker-uncertainty flag. Chunks are reassembled in order with boundary whitespace restored; there is no overlap, deduplication, summarization, or successful partial derivative. Refusals, malformed JSON, wrong chunk indices, non-stop completion reasons (including token exhaustion), or a later chunk failure fail the entire derivative. There is no silent 2048-token cutoff. Validated editing requests are checkpointed in pipeline/workflow mode; matching resume reuses them. Eligible schema/coverage and polish preservation failures can receive one additional strictly validated correction request when retries are enabled. See [recovery controls](recovery-controls.md#validated-text-resumption).

Derivatives carry an AI label, explicitly mark speaker identities/turn boundaries as unverified, and flag chunks where the editor reports attribution uncertainty. No speaker identity or turn is inferred. Editing reads a single verified raw snapshot and rechecks its byte checksum before publishing a derivative; detected raw changes fail without publishing. The private job lock protects against concurrent pipeline runs, not arbitrary external file edits. The raw `transcription.txt` is never overwritten by the pipeline. An editing failure leaves the raw transcript intact. Changing editing model or effort selects a retained text version; it cannot overwrite an existing derivative. Legacy compatible artifacts require their original source/settings hashes and current fidelity validation before reuse.

Use hyphenated spellings in new commands. Deprecated underscore spellings remain compatible, with no scheduled removal. `--model` and `--interview-model` also remain aliases for `--transcription-model` and `--speaker-model`. Legacy `--enhance_for_reading` remains supported. `--format_as_interview` is retained only as a legacy audio-mode alias for the same faithful layout operation and existing filename; it no longer asks for interview reconstruction or speaker-role assignment. It does not turn a monologue into an interview.

Official selection/compatibility references: [GPT-Transcribe](https://developers.openai.com/api/docs/models/gpt-transcribe), [ASR context and languages](https://developers.openai.com/api/docs/guides/speech-to-text), [GPT-6 Astra](https://developers.openai.com/api/docs/models/gpt-6-astra), [GPT-6.1 Sol](https://developers.openai.com/api/docs/models/gpt-6.1-sol), [reasoning effort](https://developers.openai.com/api/docs/guides/reasoning), and [model selection](https://developers.openai.com/api/docs/guides/model-selection).

## Privacy boundaries

Conversion runs on your machine. Transcription sends the **normalized audio** to OpenAI; this can contain voices, names, and other sensitive spoken content even after container metadata is stripped. The multipart filename is generic `audio.wav`. Optional context/glossary/language hints are also sent; faithful editing, author review, and narrative arrangement separately send transcript text. Review your authorization to process the material and OpenAI's current data controls before using real recordings. This tool makes no zero-retention promise and cannot prevent disclosures present in the audio or transcript itself. It does not download from Drive, publish artifacts, or upload to GitHub.

Keep API keys and source/output folders private; delete retained media, transcripts, manifests, and backups according to your own retention policy. Temporary normalization files are removed on normal completion/error; abrupt termination can leave private temporary directories. SDK/network debug logging, including the current SDK's HTTP transports, is suppressed by the transcriber to avoid accidental credential/payload logging. No real recordings, personal transcripts, keys, or identifying fixtures belong in this public repository.

Official references: [OpenAI transcription formats and limits](https://developers.openai.com/api/docs/guides/speech-to-text), [OpenAI API data controls](https://developers.openai.com/api/docs/guides/your-data), and [FFmpeg stream/metadata mapping](https://ffmpeg.org/ffmpeg.html).
