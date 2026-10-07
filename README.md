# Interview Studio

Turn local audio or video recordings into transcripts, then optionally polish their layout, review passages that need human attention, and draft chapters. Interview Studio keeps the original automatic transcript alongside every derived output, including optional speaker-attributed transcripts. Interview-technique coaching is planned and is not implemented.

Audio preparation runs on your computer. Transcription and optional text processing use the hosted OpenAI API and can incur charges. This project does not run a local speech model.

Formerly **Voice Transcription Engine**. Use `interview transcribe` and `interview batch`; the existing `voice-transcribe` and `voice-batch` commands remain supported with the same options. See the [migration guide](docs/migration.md) for safe installation and compatibility.

## Start here

- [Getting started](docs/getting-started.md): install, prepare one recording locally, transcribe it, and find the output.
- [How the pipeline works](docs/pipeline-guide.md): steps, costs, progress, outputs, recovery, and human review.
- [Command and option reference](docs/cli-reference.md): every option, default, supported combination, and older spelling.

Choose the input structure before running a command:

| Your recordings | Use | Result |
| --- | --- | --- |
| One audio/video file | `interview transcribe --pipeline --input ...` | One original transcript. |
| A folder of unrelated recordings | `interview transcribe --pipeline --input-folder ...` | One separate job per supported file. |
| Several recordings from **one interview** | `interview transcribe --author-workflow --recordings-list ...` | Separate part transcripts and one combined interview in your declared order. |
| Several **independent interviews**, each with one or more recordings | `interview batch` with `--batch-plan` | Prepared copies, separate interview results, and optional parallel processing. |

A folder does not combine its files into one interview. An ordered recordings list is a small JSON file whose array defines the order. A batch plan is a JSON file listing independent interviews. The [getting started guide](docs/getting-started.md#choose-the-right-input) includes examples of both files.

## Requirements and setup

Use Python **3.14+** (the project pins **3.14.8**), [uv](https://docs.astral.sh/uv/getting-started/installation/) **0.12.23+**, and FFmpeg **and ffprobe** on `PATH`. Hosted processing also requires your own OpenAI API key, model access, quota, and network connection.

From the checkout root:

```bash
uv python install 3.14.8
uv sync --locked
ffmpeg -version
ffprobe -version
uv run --locked interview transcribe --help
uv run --locked interview batch --help
```

Setup can download Python and packages; it does not install FFmpeg or configure credentials. `--locked` uses the committed dependency versions. See [setup](docs/setup.md) for credentials, runtime-only installs, notebooks, and dependency maintenance.

## One command: video/audio → WAV → transcription

Keep real recordings under ignored `private/input/` or outside all repositories. Replace the example path with your own local recording:

```bash
# Prepare audio locally and request the original transcript.
uv run --locked interview transcribe --pipeline \
  --input private/input/example.mp4 --output-folder private/output
```

For a local-only first step, use `--prepare-audio`. To continue the same job later, add `--resume`:

```bash
uv run --locked interview transcribe --prepare-audio \
  --input private/input/example.mp4 --output-folder private/output
uv run --locked interview transcribe --pipeline \
  --input private/input/example.mp4 --output-folder private/output --resume
```

Supported audio: WAV, MP3, M4A. Supported video: MP4, MOV, MKV, WebM, AVI, M4V. Folder scans skip unsupported files and do not enter subfolders. Videos need a decodable audio stream; preparation uses the first audio stream and leaves the source untouched. Media URLs and symlinks are rejected.

## Know what is happening

On a capable interactive terminal, the command keeps a status panel in place while useful milestones and failures remain above it. The panel shows the current step, elapsed time, recording or interview counts, and progress through known recording parts or request sections. Parallel batch interviews have separate active rows.

Bars count completed or verified reused work. A request in progress does not count as completed, and a section bar reaching its end does not mean every later pipeline step has finished. When a total is unknown, the command shows activity without an invented percentage or completion estimate.

`--progress plain` prints readable scrolling messages. `--progress json` prints machine-readable events; default `auto` also preserves JSON when output is redirected or the terminal cannot support the live panel. Private execution logs remain JSONL in every display mode. See [reading progress](docs/pipeline-guide.md#read-the-progress-display).

## Add polish and author review

The author workflow defaults to `raw,polish,review`; it does not create chapters unless you select a chapter style:

```bash
uv run --locked interview transcribe --author-workflow \
  --input private/input/example.wav --output-folder private/author-output
# Same workflow, but review raw text without a polished derivative:
uv run --locked interview transcribe --author-workflow \
  --input private/input/example.wav --steps raw,review \
  --output-folder private/review-output
```

Polish changes punctuation, capitalization, and layout in a separate file. Review reads the raw text and produces JSON and an Excel workbook. It flags questions for a person to investigate; it does not verify facts or automatically approve publication. Chapter drafting requires a complete review, with additional gates described in the [author workflow guide](docs/author-workflow.md).

Existing commands keep `gpt-6-astra` with high reasoning for polish and review. For the recommended starting point when comparing quality and cost, explicitly select `--text-profile balanced`: `gpt-6.1-sol` with low reasoning for polish and medium reasoning for review. The review setting is a candidate to assess against Astra, not a proven quality or speed improvement. Separate flags let you retain Astra review while using Sol polish:

```bash
uv run --locked interview transcribe --author-workflow \
  --recordings-list private/config/session-a.json --output-folder private/author-output \
  --resume --text-profile balanced --review-model gpt-6-astra --review-reasoning-effort high
```

Explicit model/effort flags override the profile regardless of flag order. `--review-model` and `--review-reasoning-effort` also configure narrative chapter arrangement; interview chapter excerpts use no model request. Start with the isolated [text comparison](docs/text-comparison.md) before another full paid run. Its offline synthetic mode makes no provider calls.

## Process independent interviews

Prepare a fresh private batch folder once, then run one step at a time. Copying sources and sending material to OpenAI each require their named opt-in flags:

```bash
uv run --locked interview batch check --batch-plan private/config/batch-plan.json
uv run --locked interview batch prepare --batch-plan private/config/batch-plan.json \
  --batch-folder private/batches/example --copy-local-files
uv run --locked interview batch run --batch-folder private/batches/example \
  --step raw --send-to-openai
uv run --locked interview batch run --batch-folder private/batches/example \
  --step review --send-to-openai
uv run --locked interview batch status --batch-folder private/batches/example
```

Batch runs resume automatically. Add `--parallel-interviews 2` to overlap two independent interviews after confirming successful processing and your account limits. Their recordings stay in order. Parallelism increases concurrent work; it does not set a rate or spending limit. Read the [batch guide](docs/batch-orchestration.md) for selection, copying, verification, and chapter approval.

## Find outputs and recover

Pipeline output lives in `private/output/<opaque-job-id>/`; the original text is `transcription.txt`. Ordered interviews use `parts/` for individual recordings and `interviews/` for combined results. Batch output lives under `item-NNNN/output/`, where the number is the interview's original position in the plan. The [pipeline guide](docs/pipeline-guide.md#find-your-results) explains the output files.

`interview transcribe --resume` checks sources, settings, and saved files before reusing completed work. Repeat a matching `interview batch run` command to resume its batch. Changed or damaged completed artifacts cause a conflict; retain them and use a fresh output folder for a deliberate restart. A failed later step keeps earlier validated work. Do not edit manifests or machine-generated review bundles to bypass checks.

## Privacy and further guides

Sources and outputs are private. Audio/text/hints selected for hosted processing are sent to OpenAI; the tool makes no zero-retention promise. Terminal events and execution logs omit source names, paths, transcript text, speaker names, credentials, and raw provider errors. Private output artifacts can contain identifying information. Owner-only file permissions apply where supported; storage is not encrypted. See [processing contracts and privacy](docs/processing-details.md) for the precise boundaries.

- [Ordered recordings](docs/ordered-interviews.md): JSON schema, part order, combined text, and provenance.
- [Speaker separation](docs/interview-attribution.md): the additional audio pass and confirmed speaker mappings. Voice labels do not establish identities.
- [Batch media and speaker configuration](docs/batch-media-attribution.md): per-interview settings and extensionless video staging.
- [Recovery controls](docs/recovery-controls.md): saved request checkpoints, failures, retries, and diagnostic limits.
- [Development and verification](docs/development.md): offline tests, packaging, and module responsibilities.

New examples use clearer option names such as `--recordings-list`, `--prepare-audio`, and `--request-timeout`. Existing commands retain their behavior and output naming; the [option reference](docs/cli-reference.md#older-option-spellings) maps every alias.
