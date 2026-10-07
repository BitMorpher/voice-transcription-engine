# Getting started

This walkthrough prepares a recording on your computer, requests its transcript, and shows where to read the result. It then explains how to group more recordings. The example filenames are placeholders: supply your own authorized local media. No recordings are included in the repository.

## Install and check the tools

You need Python 3.14+, uv 0.12.23+, FFmpeg and ffprobe. FFmpeg reads/converts media; ffprobe checks its streams. Install those system tools through your trusted package manager if they are missing.

From the repository root:

```bash
uv python install 3.14.8
uv sync --locked
ffmpeg -version
ffprobe -version
uv run --locked interview transcribe --help
```

`uv sync --locked` creates the project's `.venv` and installs the exact dependencies in `uv.lock`. `uv run --locked` runs a command inside that environment; you do not need to activate it. It may download Python/packages during setup. It does not install FFmpeg or set up an API account. See [setup details](setup.md) for runtime-only installation and notebooks.

Store your recording under ignored `private/input/`, such as `private/input/example.mp4`, or outside all Git repositories. Quote paths containing spaces. Inputs must be local regular files: a website URL, Drive link, or symlink is not a recording input.

## Prepare one recording locally

```bash
uv run --locked interview transcribe --prepare-audio \
  --input private/input/example.mp4 --output-folder private/output
```

This step checks the media and writes a mono 16 kHz WAV under a job directory inside `private/output/`. It needs no API key and makes no OpenAI requests. The original recording is untouched. For video, it extracts the first audio stream; it does not combine alternate language tracks.

The terminal shows the current step and recording progress. Preparation can be slow for a long video. An activity message means the command is still running; it does not mean the provider has completed anything.

## Request the original transcript

Transcription sends the prepared audio to OpenAI and can incur charges. Supply your own API key in the current environment. An interactive prompt avoids storing it in shell history:

```bash
read -rs OPENAI_API_KEY
export OPENAI_API_KEY
```

Enter the key when prompted; input is hidden in bash/zsh. `.env` files are not automatically loaded. Keep keys out of tracked files, notebooks, and chat.

Continue the local job with the same input and output:

```bash
uv run --locked interview transcribe --pipeline \
  --input private/input/example.mp4 --output-folder private/output --resume
```

`--pipeline` means prepare audio and transcribe it. `--resume` verifies the already prepared WAV and reuses it. You can also run `--pipeline` directly on a new recording without doing the local-only step first.

Long audio is sent in ordered request sections. Only after every section succeeds does the tool publish the full `transcription.txt`. The original automatic text is not polished or summarized by this command. Speech recognition can still get words wrong, so listen to the recording before treating it as an exact quotation.

## Open the result

The directory name is an opaque job ID, keeping private filenames out of terminal events. Look under the output folder you chose:

```text
private/output/<job-id>/
  audio.wav
  transcription.txt
  manifest.json
private/output/execution-logs/
  execution-<run-id>.jsonl
```

For the single recording in this walkthrough, locate the text with:

```bash
find private/output -type f -name transcription.txt
```

Open the returned file locally in your editor. `manifest.json` records saved stage results and checksums used by resume; leave it unchanged. If you have processed several recordings, each job has its own transcript. For ordered interviews and batches, use the [output guide](pipeline-guide.md#find-your-results) to choose the combined result rather than a part transcript.

## Add polish and review

The author workflow selects steps by name. Here it adds a layout polish and a report while reusing matching raw work:

```bash
uv run --locked interview transcribe --author-workflow \
  --input private/input/example.mp4 --output-folder private/output \
  --steps raw,polish,review --resume
```

| Step | What you get |
| --- | --- |
| `raw` | Original automatic transcript. Always retained. |
| `polish` | A separate file with punctuation, capitalization, and paragraph layout changes. Words remain in their original order. |
| `review` | A JSON report and Excel workbook flagging passages that need a person's attention. Review reads raw text. |
| `chapters` | Optional source-linked drafts; requires a chapter style and complete review. |

Polish and review send transcript text to OpenAI and can incur additional charges. These outputs do not replace `transcription.txt`. A complete report with no findings is different from an incomplete/failed report; neither certifies truth or publication suitability. Read the [pipeline guide](pipeline-guide.md#review-before-drafting-chapters) before requesting chapters.

To review without a polished derivative, select `--steps raw,review`. To request only the raw transcript through this workflow, select `--steps raw`.

Polish and review keep Astra/high defaults. `--text-profile balanced` is an explicit
comparison candidate: Sol 6.1/low polish and Sol 6.1/medium review. Override either model
or effort separately when needed, for example retaining Astra review with
`--review-model gpt-6-astra --review-reasoning-effort high`. Review settings also apply to
narrative chapter arrangement. Begin with the offline [text comparison](text-comparison.md);
only a deliberately scoped paid comparison can establish real latency, usage and nuanced
review quality for your material.

## Choose the right input

**A folder of independent recordings.** Each supported file becomes a separate job, sorted by filename. Subfolders are not scanned, and files are not combined:

```bash
uv run --locked interview transcribe --pipeline \
  --input-folder private/input --output-folder private/folder-output
```

**Several recordings from one interview.** Create `private/config/interview-a.json` with an ordered list:

```json
{
  "version": 1,
  "interview_id": "interview-a",
  "parts": [
    {"id": "opening", "path": "../input/opening.mp4", "media_type": "video"},
    {"id": "continuation", "path": "../input/continuation.wav", "media_type": "audio"}
  ]
}
```

The paths are relative to this JSON file. The array order is the interview order, irrespective of filenames. `id` is a stable reference, not a sorting key. Replace the paths with existing media before running:

```bash
uv run --locked interview transcribe --author-workflow \
  --recordings-list private/config/interview-a.json --steps raw \
  --output-folder private/interview-output
```

This retains each recording's raw text and writes one combined transcript under `interviews/`. It inserts two newline characters between parts, preserves the text within each part, and records boundaries in `part_boundaries.txt`. It does not remove overlap or invent missing speech. See [ordered recordings](ordered-interviews.md) for the full schema.

**Several independent interviews.** Create a separate recordings list for each interview. An interview containing one file still uses a list with one part. For example, `private/config/interview-b.json` can contain:

```json
{
  "version": 1,
  "interview_id": "interview-b",
  "parts": [
    {"id": "recording", "path": "../input/another-interview.m4a", "media_type": "audio"}
  ]
}
```

Then create `private/config/batch-plan.json`:

```json
{
  "version": 1,
  "interviews": [
    {"id": "entry-a", "manifest": "interview-a.json", "status": "ready"},
    {"id": "entry-b", "manifest": "interview-b.json", "status": "ready"}
  ]
}
```

`manifest` is the schema's name for an interview's recordings list. These paths are relative to the batch plan. Each plan entry is an independent interview; the tool never merges them. The IDs are private selection keys, and progress uses their original plan positions.

## Prepare and run a batch

Check the JSON and source metadata, then copy selected recordings into a **fresh** batch folder:

```bash
uv run --locked interview batch inventory --batch-plan private/config/batch-plan.json
uv run --locked interview batch check --batch-plan private/config/batch-plan.json
uv run --locked interview batch prepare --batch-plan private/config/batch-plan.json \
  --batch-folder private/batches/example --copy-local-files
uv run --locked interview batch verify --batch-folder private/batches/example
```

Inventory/check do not read media content or call OpenAI. Check also confirms FFmpeg/ffprobe availability. Prepare reads, copies, and hashes sources; verify reads the staged copies and compares their saved checksums. Preparation does not make provider calls. If macOS files are cloud placeholders, preparation requires `--download-cloud-files` to allow their download; otherwise download them yourself before preparing.

Request transcripts, then text review once raw is complete:

```bash
uv run --locked interview batch run --batch-folder private/batches/example \
  --step raw --send-to-openai
uv run --locked interview batch run --batch-folder private/batches/example \
  --step review --send-to-openai
uv run --locked interview batch status --batch-folder private/batches/example
```

Batch `review` includes polish and review for each ready requested output family. It does not retranscribe missing audio to fix a prerequisite. Run starts one interview at a time by default; add `--parallel-interviews 2` when you deliberately want two independent interviews to overlap. Parts inside each interview stay ordered.

Repeat a matching batch run command to resume automatically; `interview batch` has no `--resume` option. `status` shows saved history, without freshly checking output integrity. See [batch orchestration](batch-orchestration.md) for selections, blockers, parallelism, and explicit chapter approval.

## If a command stops

Read the fixed failure message and next action. Earlier validated artifacts and request checkpoints remain available. Keep source paths, models, hints, and section durations the same when resuming. A failed transcription can reuse saved successful sections; a failed review can reuse validated text requests.

If completed output was changed or no longer matches its recorded configuration, the tool refuses to trust or overwrite it. Retain it and use a fresh output folder for a deliberate new run. Changing only editing/review model or effort selects a retained text version in the same output folder and preserves matching raw/audio; review-only changes can reuse validated polish. The default remains Astra/high; the explicit `--text-profile balanced` selects Sol 6.1/low polish and Sol 6.1/medium review candidate. Compare it using the [isolated text guide](text-comparison.md) before full paid processing. A failed batch preparation still needs a fresh batch folder. Starting again may repeat paid requests; do not use a fresh folder as an automatic response to a timeout.

Use `--progress plain` for readable scrolling messages or `--progress json` for scripts. See [progress and recovery](pipeline-guide.md#read-the-progress-display) for the meaning of bars, interruption, and blocked work.
