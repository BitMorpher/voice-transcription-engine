# How the pipeline works

A pipeline is a sequence of steps: prepare audio, transcribe it, then optionally polish, review, and draft chapters. Each step saves a separate artifact and verifies its inputs. A later failure preserves validated earlier work.

## Understand each step

```text
local audio/video → prepared WAV → original raw transcript
                                       ├→ optional layout polish
                                       └→ review report → optional chapter drafts
```

Polish and review are separate uses of the raw transcript. Review does not rely on the polished file. Chapters are also bound to the raw text and its exact review report.

| Step | What happens | Where it runs | Main result |
| --- | --- | --- | --- |
| Prepare audio | Check decodable audio, extract a video's first audio stream, normalize to mono 16 kHz WAV. Original media stays untouched. | Your computer, using FFmpeg/ffprobe. | `audio.wav` |
| Raw transcription (`raw`) | Send ordered audio sections, validate each returned response, and join the exact text only when all sections succeed. | Hosted OpenAI API; can incur charges. | `transcription.txt` |
| Layout polish (`polish`) | Change punctuation, capitalization, and layout in a separate derivative, preserving words and symbols in order. | Hosted OpenAI API plus local validation; additional charges are possible. | `derivative_readability.txt` |
| Author review (`review`) | Inspect raw text in sections and flag exact passages for human attention. Complete coverage is checked locally. | Hosted OpenAI API plus local validation; additional charges are possible. | `review_report.json` and `.xlsx` |
| Chapter drafts (`chapters`) | Create source-linked interview excerpts or conservative narrative arrangement after the review gate. | Interview excerpts are deterministic local work; narrative arrangement can use the hosted API. | Chapter JSON and selected text files. |

The default `--pipeline` command prepares and transcribes only. `--author-workflow` defaults to raw, polish, review. Use `--steps` to choose its optional steps. Their execution order follows dependencies, irrespective of the order written in the list. Chapters require a `--chapter-style` selection.

The default transcription model is `gpt-transcribe`; polish and review retain `gpt-6-astra` with high reasoning. These are configured defaults, not a benchmark on your recordings. The explicit `--text-profile balanced` selects Sol 6.1/low polish and Sol 6.1/medium review candidate. `--editing-model`, `--editing-reasoning-effort`, `--review-model`, and `--review-reasoning-effort` override each setting separately; review settings also apply to narrative chapters. The [option reference](cli-reference.md) lists allowed combinations, and the [text comparison guide](text-comparison.md) explains how to assess review quality before a full run.

## Group recordings correctly

| Input structure | Processing unit | Order and combination |
| --- | --- | --- |
| One file | One job. | Audio requests run in recording order. |
| Folder | One independent job per supported file. | Sorted by filename; transcripts stay separate. |
| Ordered recordings list | One interview containing several parts. | JSON array order; exact part transcripts joined with two newlines between them. |
| Batch plan | Several independent interviews, each with its own ordered list. | Plan order identifies entries; parallel completion can occur in a different order. Interviews never combine with each other. |

**Part** means a source recording inside an ordered interview. **Section** means a bounded audio or text chunk sent in one request. Long recordings have several sections; text processing can split the same interview differently. A section count is specific to its current step.

By default, audio sections are capped at five minutes and 20 MiB; the byte cap can split a section earlier. Every PCM frame, including a short final tail, is included. Boundaries can cut across speech, and full audio coverage cannot prove that the model recognized every word. Exact text checks likewise establish correspondence to automatic text, not to the actual recording or to factual truth.

Batch actions have different jobs:

| Batch action | Meaning |
| --- | --- |
| `inventory` / `check` | Read plan/list JSON and filesystem metadata; check also finds FFmpeg/ffprobe. No media-content reads or OpenAI calls. |
| `prepare` | Copy and hash recordings into a fresh private folder after `--copy-local-files`. |
| `verify` | Compare staged copies and saved records; no original files or OpenAI calls needed. |
| `run --step raw` | Prepare and transcribe staged interviews; optionally add speaker separation. |
| `run --step review` | Polish/review eligible raw outputs; missing raw is blocked rather than purchased again. |
| `run --step chapters` | Draft only from each eligible output's exact complete, human-reviewed report. |
| `status` | Show saved execution history and latest recorded step outcomes. This does not freshly verify files. |

## Read the progress display

Default `--progress auto` uses an updating panel on a capable interactive terminal. It keeps the current step visible while lasting messages, verified reuse, failures, and final results stay above the panel. Parallel batch interviews have separate active rows.

The display answers: which recording or interview is active, which step is running, how much of a known section/part set has completed, and how long this execution has been running. Direct commands show “inputs finished”: a file is one input, while one ordered interview list is one input containing several recording parts. Batches show “interviews finished” and the current recording part inside each interview. A batch label such as “Interview 1 of 4 (plan item 9)” identifies the first selected interview and its original plan position. Paths, names, private IDs, and transcript content are omitted.

- A section bar advances after a response completes successfully or a saved response is verified and reused. A started request does not advance it.
- Part, recording, and interview counts describe finished processing units, not elapsed-time estimates. The overall finished count can include failed, blocked, interrupted, or not-started entries; inspect their outcome counts before calling the run successful.
- A complete section bar applies to that current step. Later polish, review, or chapter work may still remain, and another step can have a different section total.
- When the total is unknown, the display shows activity and known counts without a percentage. No overall finishing time is estimated.
- A waiting heartbeat means the coordinator is alive. It does not measure upload bytes, provider reasoning progress, or remote completion.

Choose another display when useful:

```bash
# Readable messages for a terminal that should not redraw a panel.
uv run --locked interview transcribe --pipeline --input private/input/example.wav \
  --output-folder private/output --resume --progress plain
# Structured events for a script or redirected output.
uv run --locked interview batch status --batch-folder private/batches/example --progress json
```

`auto` keeps JSON events when output is redirected or live terminal control is unavailable. `plain` prints readable scrolling messages. `json` prints sanitized JSON lines. `--status-interval` controls idle heartbeat frequency; it does not change request timeouts.

Private execution logs use JSONL in every display mode, normally under output/batch `execution-logs/`. `--logs-folder` selects a private alternative. Logs contain safe counters, configuration enums, fixed error categories, and guidance. They do not contain source paths, transcript text, speaker names, hints, credentials, or raw errors. Inventory/check/status are console-only unless a log destination is supplied.

Machine events use technical field names for compatibility. `conversion` means prepare audio, `transcription` means create original text, `enhancement` means polish layout, `author_review` means review text, and `diarization` means separate voices. `family: original` identifies source transcription; `family: attributed` identifies the separate speaker-labelled output. See the [reference](cli-reference.md#outputs-progress-costs-and-compatibility) for event fields.

## Find your results

You choose the root with `--output-folder` or `--batch-folder`. Job and generation directories use opaque IDs so console events can stay private. Reusing the same source/settings can select the same cache, while changed settings can produce another generation. Several directories do not mean several interviews were combined.

| Run type | Where to look |
| --- | --- |
| Single-file/folder pipeline | `<output-folder>/<job-id>/transcription.txt`; `manifest.json` beside it tracks stages and their saved artifact paths. |
| Ordered interview | `<output-folder>/interviews/<generation-id>/transcription.txt` is the combined result. `parts/` retains each separate recording's audio/raw work. |
| Batch | `<batch-folder>/item-NNNN/output/` contains that interview's part and combined trees. `NNNN` is its original one-based plan position, even if selection skips other entries. |
| Optional speaker outputs | Separate `attributed/` namespaces; the source transcription remains alongside them. Consult the [speaker output guide](interview-attribution.md). |

Find the combined transcripts for the walkthrough's ordered output or prepared batch:

```bash
find private/interview-output/interviews -type f -name transcription.txt
find private/batches/example -path '*/interviews/*/transcription.txt'
```

Inspect those paths locally; shell output can contain private information. For repeated generations, consult the matching private `manifest.json` for its configuration and recorded stage artifact paths. Terminal events and `interview batch status` identify execution/position and outcomes rather than disclosing source filenames.

| Artifact | How to use it |
| --- | --- |
| `transcription.txt` | Read the original automatic text and compare it with the recording. |
| `part_boundaries.txt` / `provenance.json` | Trace combined excerpts to their recording parts. Provenance includes private source paths and IDs. |
| `derivative_readability.txt` | Read the optional layout polish; retain raw as the reference. |
| `author_review_<id>/review_report.json` and `.xlsx` | Inspect review status, findings, exact source excerpts, and coverage. Copy the workbook before adding human notes. |
| `chapters_<id>/chapter_drafts.json` and chapter text files | Inspect draft provenance, omissions, uncertainties, and linked findings before editorial use. |
| `execution-logs/execution-<id>.jsonl` | Inspect safe execution events when diagnosing a stopped run. |
| Batch `summary-<id>.json` | Inspect saved per-entry and per-output results in original plan order. |

Keep machine-generated manifests, raw text, reports, and staged files unchanged. Their checksums are part of resume and chapter gates. Copy files before annotation. Owner-only permissions apply on supported filesystems; files are not encrypted.

## Resume or start a new run

**Resume** continues the same processing with matching source paths, models, hints, and section durations. Direct pipeline/workflow commands require `--resume`. Batch run always resumes automatically. The tool validates saved stages and request checkpoints before skipping them, then requests missing work. A reused section consumes no new provider operation.

```bash
uv run --locked interview transcribe --pipeline --input private/input/example.mp4 \
  --output-folder private/output --resume
uv run --locked interview batch run --batch-folder private/batches/example \
  --step raw --send-to-openai
```

Validated audio, speaker-pass, polish, review, and narrative requests are checkpointed in pipeline/workflow and batch processing. A checkpoint is a saved valid response bound to its exact input/settings. It is not a completed transcript or report by itself. Every required section must pass before a complete output is published. Responses held only in memory by older versions cannot be recovered.

**Choose a new text configuration in the same folder** when changing editing/review model or effort: separate retained versions preserve verified raw/audio and earlier text artifacts. Review-only changes reuse matching validated polish. Keep ASR, speaker settings and source paths unchanged to reuse raw work, and keep human approval tied to the exact selected review. **Restart in a fresh folder** when completed artifacts conflict with their records or when deliberately changing raw processing. Preserve the earlier folder to investigate or compare it. A new folder can repeat paid work; it is not the routine remedy for a network failure. Batch preparation always requires a new destination, including after partial preparation.

**Interrupted work** retains completed artifacts and valid responses. Ctrl+C/SIGTERM stops new scheduling; already admitted requests/local work may take time to finish during cleanup. A returned valid response is saved. Cancellation cannot prove that remote work stopped or avoided a charge. Do not remove a lock until you have verified that the old process has stopped.

`--request-timeout` bounds individual provider I/O waits, not the full interview. `--request-retries` can repeat remote requests and charges. `--max-requests` and `--max-run-seconds` are optional diagnostic limits on new provider starts, require retries `0`, and are not dollar caps or hard cancellation deadlines. Normal full runs omit these diagnostic caps. See [recovery controls](recovery-controls.md) for exact guarantees.

## Review before drafting chapters

A review report flags issues for a person to assess: unclear wording, uncertain attribution, disputed statements, sensitive information, or serious allegations. High/medium/low priority is an attention rubric, not a truth verdict. Criticism alone is not automatically a high finding.

Check the report's **status and coverage**, then its findings. `complete` with zero findings means every required section passed review validation; `incomplete` or `failed` with zero findings does not mean the text is clear. Automated review can miss issues in any case.

For batches, chapter drafting requires explicit `--select` IDs, `--human-reviewed`, an intact complete review for each requested output family, and no high findings. Human approval applies to the exact saved bundle; workbook edits are not imported as approval. Keep notes in a workbook copy. A completed source review does not approve speaker-labelled chapters.

```bash
# Run only after listening to recordings and examining this entry's exact report.
uv run --locked interview batch run --batch-folder private/batches/example \
  --step chapters --select entry-a --human-reviewed \
  --chapter-style both --send-to-openai
```

Direct author workflow chapters also require complete review. High findings block drafts unless the separate direct-only `--draft-with-unresolved-high` option explicitly requests warned drafts. It never bypasses incomplete review. Chapters retain source links, omissions, and uncertainty; they are drafts requiring recording verification and human editorial review. See [author workflow](author-workflow.md).

## Speaker separation is an additional pass

`--separate-speakers` requests another audio pass that separates voices and creates a separate speaker-labelled output family. It does not enhance the existing raw transcript in place. This adds audio requests and selected downstream text work.

A label such as `A` identifies a voice only within its specific request. Labels can reset in the next section or recording. Display names do not identify voices. Listen before confirming a mapping such as `PART:REQUEST:LABEL=interviewer`; unmatched voices remain unidentified. The direct workflow requires both display names, while batch names are optional. See [speaker attribution](interview-attribution.md) for complete commands and limitations.

Source and speaker-labelled outputs have independent prerequisites and review gates. One can finish while the other is blocked. A command with any requested unfinished/blocked work exits nonzero while retaining successful outputs. Review and chapters do not silently purchase missing audio to pass their gates.

## Words you may see in technical files

| Term | Plain-language meaning |
| --- | --- |
| ASR | Automatic speech recognition: the original speech-to-text pass. |
| Diarization | Separate voices; it does not establish people's identities. |
| Manifest | JSON instructions or a saved record: an input recordings list declares order; an output manifest records settings, stages, and checksums. |
| Provenance | A record of where output text came from. It can include private source paths. |
| Checksum / hash | A fingerprint used to detect changes in bytes. It is not encryption or a signature proving authorship. |
| Cache / checkpoint | Validated saved work that a matching resume can reuse. |
| Family | One independently saved output version: original source text or attributed speaker text. |
| Hydration | Downloading a cloud-placeholder file so its bytes are available locally. The option is `--download-cloud-files`. |
| Provider | The hosted service receiving an audio/text request, OpenAI in this CLI. |
| JSONL | A text file containing one JSON event per line. |
