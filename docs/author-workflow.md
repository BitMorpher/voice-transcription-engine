# Author workflow: local media to source-bound review and chapter drafts

For current option names, every public parameter, valid combinations and parallel-interview examples, see the [command and parameter guide](cli-reference.md).

The author workflow takes a local audio or video recording, retains the automatic raw transcript, optionally produces a lightly polished derivative, reviews the raw text for passages needing human attention, and produces selectable chapter drafts. It is intended to help an author compare treatments of devotional oral history while preserving testimony and uncertainty.

The raw transcript is the original automatic API result, assembled in audio-chunk order. It is never overwritten by polishing, review, or chapter generation. Automatic transcription can omit or misrecognize words, names, languages, and speaker turns. Check it against the recording before relying on an excerpt as spoken verbatim. Exact-source checks below establish correspondence to this automatic text; they do not establish correspondence to the recording, truth, or suitability for publication.

## Setup and one entry point

Follow the [README setup instructions](../README.md#requirements-and-setup): Python 3.14.8, uv, FFmpeg/ffprobe, the locked dependencies, and a user-supplied `OPENAI_API_KEY` are required for the full workflow. The examples below use hypothetical synthetic files; no real recording is included or processed by the offline tests.

```bash
# Audio: default stages are raw, polish, review. No chapters by default.
uv run --locked voice-transcribe --workflow \
  --input private/input/synthetic.wav --media-type audio \
  --output-folder private/author-default

# Video: prepare its first audio stream locally, then use the same stages.
uv run --locked voice-transcribe --workflow \
  --input private/input/synthetic.mp4 --media-type video \
  --output-folder private/author-video

# Auto-detect supported extensions; scan a mixed folder nonrecursively.
uv run --locked voice-transcribe --workflow \
  --input private/input --media-type auto \
  --output-folder private/author-folder
```

`--input` accepts one local regular media file or a folder. Audio extensions are `.wav`, `.mp3`, `.m4a`; video extensions are `.mp4`, `.mov`, `.mkv`, `.webm`, `.avi`, `.m4v`. Extensions are case-insensitive. `auto` selects by supported extension; FFprobe and FFmpeg still validate that the file has decodable audio. An explicit `audio` or `video` type rejects a mismatching supported input, including in a mixed folder. Unsupported folder entries are skipped; subfolders are not traversed. Empty inputs, symlinks, unsupported single files, corrupt media, and videos without audio fail.

Both audio and video are normalized to mono 16 kHz, 16-bit PCM WAV. Video uses the first audio stream, without combining alternate tracks. Normalization can change fidelity and stereo information; the original media is retained untouched. Network URLs and Drive links are not supported input paths.

## Select stages and styles

| Option | Behavior |
| --- | --- |
| `--workflow` | Select the author workflow through the existing `voice-transcribe` CLI. |
| `--media-type auto\|audio\|video` | Validate the local media selection; default `auto`. |
| `--stages raw,polish,review` | Default selection. Accepts a comma-separated subset of `raw`, `polish`, `review`, `chapters`, without duplicates or spaces. Raw is always retained even if omitted from the list. |
| `--chapters none\|interview\|narrative\|both` | Default `none`. Selecting a style also enables review and chapter generation, even if they were omitted from `--stages`. The `chapters` stage requires a style selection. |
| `--narrative-person first\|third` | Default `first`; controls conservative testimony framing, described below. |
| `--author-model gpt-6-astra` | Review and narrative-arrangement model. Also accepts `gpt-6.1-sol`; default Astra with high reasoning. |
| `--editing-model gpt-6-astra` | Separate polishing model; also accepts `gpt-6.1-sol`. |
| `--transcription-model gpt-transcribe` | ASR model. Existing model and hint options remain available; see the README. |
| `--resume` | Verify source, configuration, prompts, and artifact checksums before skipping complete stages. |
| `--draft-with-unresolved-high` | Explicitly permit visibly labeled chapter drafts despite unresolved high-priority findings. Requires a chapter style. Does not bypass a failed or incomplete review. |

Stages execute in dependency order: conversion, raw transcription, optional polish, review, then chapters. Listing them in another order does not change that order. Raw-only processing needs no author-model request:

```bash
uv run --locked voice-transcribe --workflow \
  --input private/input/synthetic.wav --stages raw \
  --output-folder private/author-raw

# Review raw text without creating a polished derivative.
uv run --locked voice-transcribe --workflow \
  --input private/input/synthetic.wav --stages raw,review \
  --output-folder private/author-review

# Create a separate polish and review report, retaining the raw text.
uv run --locked voice-transcribe --workflow \
  --input private/input/synthetic.wav --stages raw,polish,review \
  --output-folder private/author-polish-review
```

Workflow polishing changes only punctuation, capitalization, and paragraph layout. Editing contract 3 checks the ordered case-insensitive Unicode word and symbol sequence, preserving repetitions, disfluencies, numbers, meaningful marks, join controls, and symbol identity/order. Equivalent NFC spellings may compare equal; the raw bytes remain unchanged. Added questions, speaker identities, paraphrases, and word omissions fail validation. Punctuation can still alter interpretation, so the derivative needs human review. Review and chapters use the verified raw snapshot, never the polished derivative as their sole source.

## Review rubric and human decisions

Review flags identify questions for the author. They are not proof of an allegation, a finding that criticism is false, an instruction to censor testimony, or institutional approval. Negative opinions and criticism of a person or institution do not automatically receive high priority. The model selects a constrained reason code, and local templates supply the category, priority, neutral rationale, author question, and suggested action.

| Priority | Typical reason | Suggested human work |
| --- | --- | --- |
| Low | Personal opinion, ambiguous wording, or unclear scope. For example, “I didn't like this person” alone is a subjective perspective. | Clarify what the speaker meant and whether context fairly represents the perspective. |
| Medium | Contextual interpersonal/institutional criticism, uncertain attribution, or a disputed/unverified factual statement. “This person was mean to me” needs context; it is not automatically a serious allegation. | Check attribution, surrounding context, and evidence; retain uncertainty or disputed status where appropriate. |
| High | Concrete serious allegations about an identifiable person/institution, sensitive personal disclosures, or concrete wording carrying strong reputational risk. | Deliberately review exact wording, context, attribution, consent, relevance, and available corroboration before publication. |

Categories are `criticism`, `allegation`, `sensitive_personal_information`, `disputed_fact`, `uncertain_attribution`, and `wording_ambiguity`. The context can change what warrants a flag. Identifiability must come from the source; the reviewer does not invent people, evidence, timestamps, or identities.

Each JSON finding contains a stable `finding_id`, an exact raw `excerpt`, zero-based Unicode character `start` and end-exclusive `end`, overlapping `segment_ids`, category, severity, reason code, rationale, author question, suggested author action, `disposition` initially `Unreviewed`, and a blank `reviewer`. These are Python character offsets, not UTF-8 byte positions, audio times, or Excel character counts. Given the same raw bytes, span, and category, the finding ID stays stable; a changed source gives a different identity. An invalid or invented excerpt/reference is rejected, not repaired into a plausible quotation.

The source segments preserve every character and original line ending as contiguous `seg-000001`, `seg-000002`, … records. Review partitions the full raw text into cores of at most 6000 UTF-8 bytes. Neighboring cores provide boundary context, so a request can contain three cores. Each successful response must acknowledge the exact core range. Findings must intersect that core and match an exact substring of the supplied context. Identical span/category findings from overlapping context are deduplicated, retaining the higher priority; different spans remain separate. Full coverage means validated model acknowledgement of every core, not a guarantee that every relevant issue was detected.

Reports distinguish these outcomes:

| Report status | Meaning |
| --- | --- |
| `complete`, findings empty | Every core was acknowledged and validated; this model run flagged no passages. Human review is still required. |
| `complete`, findings present | Every core was acknowledged and validated; inspect each finding in its raw context. |
| `incomplete` | At least one core succeeded and at least one failed. Valid findings are retained; unreviewed ranges remain visible. |
| `failed` | No core succeeded. An empty findings list does not mean a clean review. |

Refusal, token exhaustion/non-stop completion, malformed JSON, schema drift, incorrect coverage, wrong offsets, or invented excerpts fail that core. The workflow retains the report attempt and blocks chapter drafting when review is incomplete or failed. It never infers a pass from a parse failure. Transcript content is untrusted data: versioned prompts explicitly reject embedded instructions, role claims, suppression requests, and tool requests. Structural and exact-source validation provide additional checks, but cannot prove correct model judgment or that a model ignored every semantic manipulation.

## Use the Excel and JSON reports

Each published review bundle contains `review_report.json` and `review_report.xlsx`. JSON is the canonical machine-readable report. The workbook has four sheets:

- **Overview:** report status and meaning, counts, coverage, source/model/prompt identity, rubric, and usage guidance.
- **Findings:** sortable/filterable findings, exact excerpts and references, neutral rationale/questions/actions, and highlighted disposition/reviewer fields.
- **Source segments:** the entire raw source with segment IDs and exact offsets; long segments are split into contiguous pieces for Excel cell limits.
- **Coverage:** each core and context range, completion status, and fixed privacy-safe failure guidance.

All source/model string cells are explicitly literal text, including leading `=`, `+`, `-`, and `@`; no source string becomes an Excel formula. Excel-illegal control characters are represented as a labeled lossless JSON string. Decode that string to recover exact text; otherwise source cells remain literal raw text. Oversized fields fail safely instead of silently truncating.

Make a private copy of the workbook before entering human dispositions and reviewer names. The saved bundle is checksum-bound: editing its workbook or JSON invalidates CLI resume. Workbook decisions are a human record and are **not imported by the CLI**; choosing `Retain`, `Exclude`, or another disposition does not silently change the raw text or approve drafting. This workflow does not implement an institutional approval decision or automated removal. The source-bound library API can accept a deliberately human-reviewed report; its resolved high finding must have both a recognized disposition and a nonblank reviewer. The CLI provides no human-review import option in this version.

## Compare chapter drafts

```bash
# Both styles, after review; default gate blocks unresolved high findings.
uv run --locked voice-transcribe --workflow \
  --input private/input/synthetic.mp4 --stages raw,polish,review \
  --chapters both --output-folder private/author-chapters

# Inspect the saved report first. Explicitly request warned drafts on retry.
uv run --locked voice-transcribe --workflow \
  --input private/input/synthetic.mp4 --stages raw,polish,review \
  --chapters both --draft-with-unresolved-high \
  --output-folder private/author-chapters --resume

# Conservative third-person testimony framing, with the ordinary high gate.
uv run --locked voice-transcribe --workflow \
  --input private/input/synthetic.wav --stages raw,review \
  --chapters narrative --narrative-person third \
  --output-folder private/author-third-person
```

Every chapter is labeled **HUMAN REVIEW REQUIRED** and an inspectable editorial draft. A complete source-bound review is required first. Unresolved high findings block drafting by default; the explicit override adds prominent warnings and unresolved finding IDs. Low/medium unresolved flags also remain visible. An override allows inspection; it does not resolve a flag or declare the result ready for publication. Failed/incomplete review always blocks drafting, including with the override.

**Interview style** renders deterministic exact raw testimony excerpts in original order. It does not call the model to invent questions or infer interviewer/respondent roles. Source questions are retained as testimony without guessing who asked them. Speaker identities and turn boundaries remain unassigned. This is a conservative comparison draft, not a reconstructed Q&A interview.

**Narrative style** asks the model to group adjacent source units in original order. First-person mode preserves the source voice; it does not rewrite third-person source into first-person or invent a narrator identity. A source-preserving passage may adapt punctuation, capitalization, and paragraph layout but must preserve every source word and symbol in order. Such prose is labeled as adapted, not an exact quotation. Third-person mode adds a neutral “The source testimony states” frame to exact testimony; it preserves pronouns and requires verbatim source excerpts. It does not create scenes, chronology, dialogue, memories, spiritual facts, transitions, motives, or attributed speech.

Every source unit must appear exactly once in a passage or an explicit omission. Each passage records a stable passage ID, exact raw start/end, segment IDs, overlapping review finding IDs, kind, and unverified attribution label. Exact excerpts include `quote_start` and `quote_end` anchors checked against the raw source (with only surrounding whitespace removed). Adapted prose receives the same Unicode word/symbol checks as polishing. Invalid references, wording, order, duplicate/missing units, refusal, or truncated output fail the whole chapter stage; no partial chapter is published.

Omissions are limited to `repetition`, `fragment`, or `author_selection_needed`. They retain source offsets, segment IDs, and linked findings in both JSON and text; inspect them before using a draft. Review flags alone never authorize omission. All findings, unresolved flags, coverage counts/omissions, uncertainties, and editorial-change descriptions remain in the draft artifacts. Allegations remain source testimony rather than being silently recast as established facts. The arrangement is deliberately conservative; writing a more creative chapter requires a separate human editorial process.

## Artifacts, integrity, and retry

```text
private/author-chapters/<opaque-job-id>/
  audio.wav
  transcription.txt
  derivative_readability.txt             # if polish selected
  manifest.json                         # version 2, extended stage records
  author_review_<uuid>/
    review_report.json
    review_report.xlsx
  chapters_<uuid>/
    chapter_drafts.json
    chapter_interview.txt                # if selected
    chapter_narrative.txt                # if selected
```

The manifest adds `author_review` and `chapters` records to the existing version 2 format. These include stage status, exact raw SHA-256, configuration fingerprint, relative artifact paths and SHA-256 checksums; chapter records also bind the saved review JSON checksum. Reports record schema/prompt versions, model/settings identity, and the source hash. Prompt text/content changes participate in fingerprints, as do model, chunking, styles/person, and override settings. Chapter JSON separately records a hash of the canonical parsed review object; the manifest records the exact review file-byte hash. These hashes serve different integrity checks and need not match.

Complete bundles are published by an atomic directory rename with a unique UUID. Failed/incomplete review attempts remain inspectable in their own bundles; retry publishes a new attempt without overwriting the old report. A failed chapter request retains raw text and the review; a chapter generation failure publishes no partial draft bundle. Manifest updates are atomic. Owner-only directory/file permissions apply on supported local filesystems; this is not encrypted storage.

Use the same source, output directory, model/hint settings, stage settings, and `--resume` to skip verified stages. You can add previously unrun stages, or retry failed stages while retaining verified earlier work. A completed stage with changed prompts/settings, changed/missing bundle artifacts, a symlink, or a mismatched raw hash is a conflict. Use a fresh output directory to compare a changed completed configuration. Do not hand-edit manifests or raw text to bypass checks. Original raw and existing derivatives/bundles are never replaced.

Validated original ASR and interview speaker-pass responses are checkpointed and reused on matching resume. Polishing, review and narrative arrangement do not checkpoint individual requests and can repeat earlier successful requests/charges after a failed stage. Review retries review the complete source again rather than trusting an incomplete report. CLI progress includes only item positions, opaque IDs, stage statuses, and fixed guidance; it omits raw text, names, private paths, credentials, provider payloads, and tracebacks. Failure returns a nonzero exit code while other input files can continue. The job lock prevents concurrent workflow runs; arbitrary external edits are checked by source snapshots/checksums but are not prevented by the lock.

## Local extraction and existing commands

Extraction remains a separate entirely local operation; `--workflow --extract-only` is rejected. Resume can reuse a verified extraction later:

```bash
uv run --locked voice-transcribe --extract-only \
  --input private/input/synthetic.mp4 --output-folder private/prepared

uv run --locked voice-transcribe --workflow \
  --input private/input/synthetic.mp4 --stages raw,review \
  --output-folder private/prepared --resume
```

Existing `--pipeline`, `--input_folder`/`--input-folder`, `--output_folder`/`--output-folder`, `--enhance_for_reading`/`--enhance-for-reading`, and legacy audio folder output names remain compatible. Legacy `--format_as_interview` is still the faithful dialogue-layout alias in audio mode; it is distinct from the new source-bound chapter option and is unavailable in pipeline/workflow mode. Author-specific options require `--workflow`.

## Privacy and hosted processing

The **input path is local**, and FFmpeg conversion/extraction runs on the computer. The full workflow sends normalized audio and supplied ASR hints to the hosted OpenAI API. Polish, review, and narrative arrangement send transcript text in additional paid requests. Interview rendering is local after review. Review requests include neighboring context; chapter requests include source units. Neither choosing `--media-type` nor choosing a private output directory makes these hosted stages local inference.

Text requests use structured JSON output, high reasoning, bounded completion budgets, and `store=false`. Local validation still handles refusals, non-stop completions and invalid responses. `store=false` does not waive provider data controls or promise zero retention. See official [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs), [current model parameters](https://developers.openai.com/api/docs/guides/latest-model), [speech-to-text](https://developers.openai.com/api/docs/guides/speech-to-text), and [API data controls](https://developers.openai.com/api/docs/guides/your-data).

All media, reports, drafts, manifests, review copies, and credentials belong under ignored `private/` or `data/`, or outside all repositories. The CLI refuses checkout output outside those designated trees. Hashes can link known sources; treat them as private too. Ignore rules do not prevent forced Git staging or sharing. This feature neither accesses private Drive files nor publishes/uploads results to Drive or GitHub. No real recordings or paid model calls are used by the offline test suite.

## Implementation and verification

The workflow extends the existing small modules rather than introducing a service or orchestration framework:

| Module | Responsibility |
| --- | --- |
| `src/cli.py` | One entry point, local input/media validation, stage/style options, sanitized progress. |
| `src/media.py`, `src/transcriber.py` | Local WAV preparation and bounded hosted ASR requests. |
| `src/model_config.py`, `src/text_editing.py` | Validated model/hint settings and faithful editing contract. |
| `src/pipeline.py` | Existing version 2 manifest, job lock, artifact checksums, dependency order and resume. |
| `src/author_workflow.py` | Raw snapshots, fingerprints, author-stage dependencies, retained review attempts, atomic bundles. |
| `src/author_review.py` | Segmentation, core/context coverage, schema/source validation, reason templates and stable finding IDs. |
| `src/review_export.py` | Literal-cell, lossless, privately written XLSX review report. |
| `src/chapters.py` | Complete review/high gate, deterministic interview excerpts, constrained narrative arrangement and provenance. |
| `src/prompts/*_v1.txt` | Packaged, versioned review and chapter contracts, including untrusted-source instructions. |

Run the [offline verification commands](../README.md#offline-verification). Provider responses are mocked; synthetic media exercises FFmpeg without using personal content. Relevant regression coverage includes audio/video and stage selections, complete/empty/incomplete/failed review, long-source boundaries and duplicate findings, bad references/invented excerpts, refusals/truncation, embedded transcript instructions, high-priority gating and explicit override, chapter coverage/wording, resume/settings/source integrity, and Excel roundtrip/literal-cell handling. Tests establish implemented contracts under those cases; they do not benchmark recognition accuracy, issue detection quality, factual judgment, or publication readiness on real testimony.

## Continuations recorded in separate files

Folder batch mode creates independent jobs. For several recordings from one interview, supply [an ordered interview manifest](ordered-interviews.md) with `--workflow --interview-manifest`. It retains raw parts, combines them without rewriting in explicit order, and runs these author stages once on the combined interview, with recording-level text provenance in JSON/XLSX and chapter citations.


## Batch selection and execution monitoring

For independent interviews with optional parallel processing, private plan/preflight/staging examples and separate human-approved chapter gates, see [batch orchestration](batch-orchestration.md). Installed commands now emit flushed safe stage/part/chunk events and elapsed idle heartbeats, with exclusive local JSONL logs. Source paths, user IDs, hints and transcript/provider text are excluded. Use the engine's matching source/settings with `--resume`; the batch coordinator adds resume automatically. Validated original ASR and interview speaker-pass chunks are checkpointed; text editing/review/chapter requests are not. See [recovery controls](recovery-controls.md).

```bash
uv run --locked voice-transcribe --workflow \
  --interview-manifest private/input/interview_manifest.json --stages raw \
  --output-folder private/ordered-output --resume \
  --heartbeat-seconds 30 --provider-timeout 120 --provider-retries 2
uv run --locked voice-batch verify --batch private/batches/demo-001
uv run --locked voice-batch run --batch private/batches/demo-001 \
  --select entry-a --phase review --send-to-openai
uv run --locked voice-batch status --batch private/batches/demo-001
```


For batch chapters, the explicit human gate now binds the exact approved review bundle used for drafting. `src/review_reuse.py` verifies and transfers that bundle byte for byte into a chapter generation before author stages; a new chapter configuration does not request a fresh review after approval. Changed/conflicting bundles stop drafting. Narrative completion/property/refusal/truncation errors emit terminal safe `completion` events, while schema/source-coverage failures emit `validation` events for the failed chunk.
