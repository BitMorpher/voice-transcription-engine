# Ordered recordings from one interview

For a first run, start with [getting started](getting-started.md); the [pipeline guide](pipeline-guide.md) explains steps, outputs, and technical terms.

For current option names, every public parameter, valid combinations and parallel-interview examples, see the [command and parameter guide](cli-reference.md).

Folder batch mode processes separate recordings as independent jobs, sorted by filename. It does **not** combine them into an interview. Use `--author-workflow --recordings-list` when recordings are continuations of the same interview. Their order comes only from the manifest array, never from filenames, timestamps, numbering or guessed missing parts.

Keep the manifest and media under ignored `private/`, or outside the repository. A synthetic `private/input/interview_manifest.json` example:

```json
{
  "version": 1,
  "interview_id": "synthetic-interview",
  "parts": [
    {"id": "opening", "path": "Part10.mp4", "media_type": "video"},
    {"id": "continuation", "path": "Part2.wav", "media_type": "audio"},
    {"id": "closing", "path": "unnumbered.m4a", "media_type": "auto"}
  ]
}
```

This intentionally plays Part10 **before** Part2. Array position defines sequence; `id` is a stable reference, not a sorting key. Paths are relative to the manifest's directory, or absolute local paths. Ordinary `../` references are canonicalized. URLs, symlinks, duplicate file references (including hard links), duplicate IDs, empty/missing files and unsupported formats are rejected. IDs use 1–64 ASCII letters, numbers, underscores or hyphens, beginning with a letter or number. `media_type` may be omitted (defaults to `auto`); otherwise choose `audio`, `video` or `auto` per part. Existing supported formats and first-audio-stream conversion apply, so audio and video may be mixed.

Version 1 requires exactly `version`, `interview_id` and `parts`; each part requires `id` and `path`, with optional `media_type`. Unknown fields, duplicate JSON keys, unsupported versions and a separate `order` field are errors. The CLI does not guess how to migrate an older schema.

## Commands

From the checkout root after `uv sync --locked`:

```bash
# Raw transcription only: one combined interview, no editorial model calls.
uv run --locked voice-transcribe --author-workflow \
  --recordings-list private/input/interview_manifest.json \
  --steps raw --output-folder private/ordered-output

# Default raw + faithful polish + author review (JSON and XLSX).
uv run --locked voice-transcribe --author-workflow \
  --recordings-list private/input/interview_manifest.json \
  --output-folder private/ordered-output --resume

# Add both chapter styles; review always runs before chapter drafting.
uv run --locked voice-transcribe --author-workflow \
  --recordings-list private/input/interview_manifest.json \
  --steps raw,polish,review,chapters --chapter-style both \
  --output-folder private/ordered-output --resume
```

The manifest is mutually exclusive with `--input`/`--input-folder` and requires `--author-workflow`. Set media types in the manifest, rather than a global `--media-type audio/video`. `--prepare-audio` is separate from this workflow. Model, context/glossary, language hints, chunk duration, timeout, narrative person and explicit unresolved-high draft override retain their existing meanings. Context/hints apply to every part. See [the author workflow guide](author-workflow.md) for fidelity, review coverage and draft gates.

The default single-file/folder commands remain unchanged. A multipart invocation reports one processed interview; progress and errors expose fixed stage messages and opaque hashes, never filenames, IDs, transcript content or provider payloads.

## Outputs and provenance

Under your output directory:

- `parts/<opaque-cache>/<opaque-job>/` retains normalized audio, each **separate raw part transcript**, and its checksum manifest. Source media is never changed or moved.
- `interviews/<opaque-generation>/transcription.txt` is the exact UTF-8 concatenation of part transcripts in supplied order, with exactly two inserted LF characters between parts. CRLF, Unicode, symbols and all text inside each part remain untouched. No overlap is removed and no gaps, speaker roles, transitions or missing speech are inferred.
- `part_boundaries.txt` labels each part's exact combined character range and raw transcript reference. Blank lines in the raw text alone are not reliable part labels: consult this index.
- `provenance.json` records the interview ID, ordered canonical source paths/hashes, part IDs, part raw paths/hashes, combined ranges, local line segments and explicit separator spans. **It is a private artifact containing source filenames**, not something to commit or share automatically.
- One optional readability derivative, one author review JSON/XLSX bundle, and one selected set of chapter drafts operate on the **combined interview**. Review and chapters remain bound to raw text, not the polished derivative. There is no chapter per recording.

The combined raw transcript is **automated and unverified**. Listen to every recording. The boundary index labels this explicitly; reports and drafts retain their human-review warnings. A pause means continuation; it does not establish missing content. Editorial stages preserve existing fidelity contract 3 and unresolved-high gates. They cannot verify ASR accuracy or the truth of testimony.

Offsets are zero-based Python Unicode **character** positions with exclusive ends. Each review finding/segment and chapter passage/omission maps to recording part, local segment IDs and local character offsets; a span crossing a boundary has separate references for both recordings and the inserted separator. The XLSX includes `Recording parts` and `Recording references` sheets alongside the existing review sheets. Chapter text prints local recording references; JSON also maps exact quotation spans. Part segment IDs are scoped to their part.

These are **text offsets, not audio timestamps**. The current ASR interface returns text without word timing; no local audio seek position or global recording time across pauses is invented. Source paths stay in private provenance/workbooks and are not sent to editorial models. Only normalized audio and existing ASR hints reach ASR; editorial models receive transcript content as before.

## Validation, failure and resume

Before paid requests, validate the whole schema, source paths/order/duplicates/media types, existing cache manifests and output conflicts. Every recording is then decoded and normalized locally before **any** ASR call, so a corrupt later recording cannot charge for earlier parts in that attempt. Source checksums are rechecked before transcription and combination. An empty part transcription fails without a complete raw part artifact.

A failed part leaves completed parts intact for `--resume`. No combined interview, review or chapters are published until all required raw parts are complete and verified. Combination publishes a complete private directory atomically. Failed author review attempts remain labeled failed/incomplete; chapter drafting stays blocked. Unresolved high-priority findings retain the existing explicit override requirement.

`--resume` verifies checksums and settings before reuse. Part caches bind canonical source path, source bytes, ASR model/hints/chunk settings and contract version. Reordered, added, removed or renamed parts change the ordered manifest fingerprint; changed source bytes or editorial settings/prompts also change the combined generation. A new generation is written without overwriting prior outputs. Verified unchanged part ASR can still be reused. Editorial configuration changes do not charge again for unchanged ASR; ASR settings changes require new part ASR. Canonical manifests ignore JSON whitespace/key ordering and normalize relative paths; only semantic changes invalidate order.

Within the same generation, adding optional stages can reuse raw text. Selected chapter settings and the high-priority override are included in generation identity. This means changing chapter selection/override may write a new combined/review generation while reusing part ASR. Tampered, deleted complete artifacts, incompatible cache manifests and unexpected outputs fail closed; use a fresh output directory to recover without overwriting anything. No automatic cache migration or cleanup occurs. Validated nonempty ASR responses within a failed part are checkpointed and reused on matching resume. Validated polish, review, and narrative requests are also checkpointed. Failed or never-saved requests may repeat charges.

One output-root lock prevents two invocations from writing the same interview output root; independent batch entries have separate roots and can overlap with `--parallel-interviews`. Parts within an interview remain serial, and existing per-part locks remain in force. A stale lock must be inspected locally before manual removal. Fresh outputs use owner-only permissions and repository-local outputs are restricted to ignored `private/` or `data/` trees. No real interview data or paid API calls are needed for the synthetic test suite.


## Batch selection and execution monitoring

For independent interviews with optional parallel processing, private plan/preflight/staging examples and separate human-approved chapter gates, see [batch orchestration](batch-orchestration.md). Installed commands provide an updating terminal panel by default, readable messages with `--progress plain`, or safe stage/part/chunk events with `--progress json`; redirected auto output remains JSON. Exclusive local JSONL logs and idle heartbeats are retained in every display mode. Source paths, user IDs, hints and transcript/provider text are excluded. Use matching source/settings with `--resume`; the batch coordinator resumes automatically. Validated original ASR, speaker-pass, polish, review, and narrative requests are checkpointed. See [recovery controls](recovery-controls.md).

```bash
uv run --locked voice-transcribe --author-workflow \
  --recordings-list private/input/interview_manifest.json --steps raw \
  --output-folder private/ordered-output --resume \
  --status-interval 30 --request-timeout 120 --request-retries 2
uv run --locked voice-batch verify --batch-folder private/batches/demo-001
uv run --locked voice-batch run --batch-folder private/batches/demo-001 \
  --select entry-a --step review --send-to-openai
uv run --locked voice-batch status --batch-folder private/batches/demo-001
```


When the batch chapter coordinator creates a new chapter generation, it transfers the exact gate-approved review JSON/XLSX bytes after locked source/bundle checks and skips the provider review request. Conflicting or changed approved reviews fail closed. This batch approval transfer is distinct from the direct workflow's generation behavior described above; see [the human-approved batch chapter gate](batch-orchestration.md#raw-review-and-human-approved-chapters).

## Batch prerequisites and safe failure detail

For `voice-batch` review/chapters, each requested output family passes its own prerequisites. A blocked family does not prevent another eligible family from proceeding; it receives no approval from that family. Shared original part integrity, complete source-bound review, high-finding rules and actual human chapter approval remain required. Text phases never buy missing audio to satisfy a gate. Original-only review omits interview mode and the speaker configuration. See [batch orchestration](batch-orchestration.md) for mixed complete/blocked status and [recovery controls](recovery-controls.md) for timeout phases and the rule to stop after a failed diagnostic. The direct transcription workflow retains its own stage/dependency behavior.
