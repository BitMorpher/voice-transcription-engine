# Process multiple independent interviews

For a first run, start with [getting started](getting-started.md); the [pipeline guide](pipeline-guide.md) explains steps, outputs, and technical terms.

`interview batch` processes independent interviews serially by default. Use `run --parallel-interviews 2` to overlap two complete interview groups; their own recordings stay ordered. Each interview has an explicit ordered manifest and may contain one audio file, one video file, or multiple mixed recordings. `interview transcribe` remains the single interview/file coordinator. Both commands are installed in the `voice_transcription_engine` package and work outside the source checkout. Python 3.14+, the declared Python dependencies, FFmpeg and ffprobe on `PATH`, and your own environment settings are the prerequisites. No checkout pin, account name, device path, shell launcher, or platform package-manager path is configured in a batch plan.

From a checkout:

```bash
uv sync --locked
uv run --locked interview batch --help
uv run --locked interview transcribe --help
```

For an independent environment, install a locally built wheel with your standard installer:

```bash
uv build
uv venv --python 3.14 private/runtime
uv pip install --python private/runtime/bin/python dist/interview_studio-0.1.0-py3-none-any.whl
# Activate that environment and use interview batch / interview transcribe from any working directory.
# On Windows, use private/runtime/Scripts/python.exe and the corresponding activation command.
```

Keep private plans, manifests, media, hints, staging and outputs outside Git or under this repository's ignored `private/` tree. A plan is user input, not repository configuration. JSON paths are relative to the containing JSON file; absolute local paths also work. Arbitrary symlinks, URL inputs, duplicate source identities, duplicate JSON keys and invalid schema fields are rejected. Supported audio is WAV/MP3/M4A; video is MP4/MOV/MKV/WebM/AVI/M4V. Extensionless files require explicit `media_type: "video"`; inventory/check accept their metadata, while prepare must validate a copied local container before assigning a staged suffix. See [batch media and attribution](batch-media-attribution.md). Do not put personal IDs in public examples.

See [saving progress and bounding provider starts](recovery-controls.md) for atomic request checkpoints, independent diarization chunks, a one-operation diagnostic and chronological per-family status.

## Choose deliberate parallelism

`--parallel-interviews N` is a positive whole number with default `1`. Start with `2` for overlap, then inspect safe progress and your provider limits before deliberately increasing it. It counts complete interview groups, not recordings, chunks or requests per minute. Preparation and verification remain serial. Existing output isolation, part order, cache checks and human gates apply inside every group.

```bash
uv run --locked interview batch run --batch-folder private/batches/demo-001 \
  --step raw --parallel-interviews 2 --send-to-openai --request-retries 0
```

One shared request allowance, start deadline and endpoint/model failure streak covers all groups and families. Parallelism does not create a per-worker allowance, provider rate limiter or dollar budget. Already admitted operations can return after a provider stop and their valid checkpoints are retained. Local failures remain isolated. Logs can interleave by item; saved summaries retain original plan order. Ctrl+C/SIGTERM stops scheduling and drains active workers before releasing locks, which can take time. See the [complete command guide](cli-reference.md) for every public parameter, compatible combinations and examples by goal.

## Synthetic plan shape

Create `private/config/session-a.json` locally, with your own media files at the stated paths:

```json
{
  "version": 1,
  "interview_id": "session-a",
  "parts": [
    {"id": "part-1", "path": "../input/synthetic-a.wav", "media_type": "audio"},
    {"id": "part-2", "path": "../input/synthetic-b.mp4", "media_type": "video"}
  ]
}
```

For an independent single recording, create `private/config/session-b.json` with one part. Order comes exclusively from each manifest's array. Create `private/config/batch-plan.json`:

```json
{
  "version": 1,
  "interviews": [
    {"id": "entry-a", "manifest": "session-a.json", "status": "ready"},
    {"id": "entry-b", "manifest": "session-b.json", "status": "blocked"}
  ]
}
```

`status` defaults to `ready`. `blocked` entries remain excluded from processing; their presence in a selected operation produces a nonzero result. All manifest sources must still have valid readable metadata. Change readiness only in the external plan before preparing a fresh batch. IDs are selection keys and remain private. Console/log/summary rows identify entries by their **one-based position in the original plan**, never by their IDs or filenames.

## Selection, preflight and staging

```bash
# JSON/schema and filesystem metadata only: does not read/hash/probe/hydrate media.
uv run --locked interview batch inventory --batch-plan private/config/batch-plan.json
# Also check FFmpeg/ffprobe availability; still no media-content or provider reads.
uv run --locked interview batch check --batch-plan private/config/batch-plan.json --select entry-a
# Repeated selection runs in plan order, not argument order. Exclusion is exact.
uv run --locked interview batch inventory --batch-plan private/config/batch-plan.json \
  --select entry-b --select entry-a --exclude entry-b
# Explicit source reads and verified local copies, into a NEW directory:
uv run --locked interview batch prepare --batch-plan private/config/batch-plan.json \
  --select entry-a --batch-folder private/batches/demo-001 --copy-local-files
uv run --locked interview batch verify --batch-folder private/batches/demo-001
```

Metadata preflight is not codec validation. The ordered engine decodes **all parts of each selected interview before its first ASR call**. An invalid interview does not prevent later independent selected interviews from being attempted. `prepare` never calls providers. Extensionless video staging uses a bounded local ffprobe on an exact private copy, not a filename guess or an original rename. Supported named media retains the existing staging path and verification contract. It copies into generic numbered filenames, hashes originals and copies, and checks original metadata/content before accepting the staging ledger. Originals are never modified. macOS dataless sources need explicit `--download-cloud-files` on `prepare`; inventory/check only inspect their metadata. On other systems a file open may trigger remote filesystem reads; keep staging on a filesystem whose behavior you control.

Preparation writes an exclusive immutable plan snapshot, selected IDs, per-entry manifest and staging ledger. Partial staging is retained for inspection; use a fresh batch directory after a failed prepare. Never edit staged manifests, ledgers or media. `verify` checks their recorded bytes and hashes without accessing original recordings; originals may subsequently be unavailable. The stage snapshot/checksums protect accidental modification, not an attacker who can rewrite both records and hashes.

## Raw, review and human-approved chapters

```bash
# Explicit opt-in to provider calls. Defaults: gpt-transcribe, 300-second ASR chunks.
uv run --locked interview batch run --batch-folder private/batches/demo-001 \
  --step raw --send-to-openai
# Raw must already be complete for these sources and ASR settings.
uv run --locked interview batch run --batch-folder private/batches/demo-001 \
  --step review --send-to-openai
# Inspect raw recordings and the private review reports separately before this command.
uv run --locked interview batch run --batch-folder private/batches/demo-001 \
  --step chapters --select entry-a --human-reviewed --send-to-openai \
  --chapter-style both --narrative-person first
# Resume is automatic for batch processing; intact complete stages are reused.
uv run --locked interview batch run --batch-folder private/batches/demo-001 \
  --select entry-a --step review --send-to-openai
uv run --locked interview batch status --batch-folder private/batches/demo-001
```

The batch chapter phase requires explicit IDs, `--human-reviewed`, and a complete source/settings-bound review with **no high findings**. The flag records the caller's decision; it does not infer approval from an Excel edit. Preserve machine bundles and keep human notes in separate copies. There is no batch unresolved-high override. Review failure, incomplete coverage, altered raw/review files, or changed binding prevents chapters. The direct engine retains its separately documented draft override.

Batch chapters reuse the **exact review JSON and XLSX bytes accepted by the human gate**. A new chapter configuration may create a new ordered generation; its review bundle is copied from the approved generation, not regenerated by the provider. The gate captures manifest/raw/provenance/review identities, and the copy is revalidated under the interview lock before author operations. Changed approval data or a conflicting review already present in the target generation fails closed without requesting a new review or silently replacing that bundle. The chapter manifest's review SHA binds drafts to the approved JSON. Reusing the same bundle does not remove the explicit `--human-reviewed`/selection requirement.

Raw transcripts retain exact part bytes and separators. Part caches keep their source/ASR bindings, and derivative settings have independent retained versions. Changing review settings can generate another ordered generation while reusing unchanged ASR and matching validated polish. Changing chapter settings can reuse the exact gate-approved review bundle. Attributed raw/audio jobs remain unchanged when only text settings change; selected polish/review versions remain separate and intact. Validated original ASR, speaker-pass, grouped polish, review, and narrative requests are saved as private atomic checkpoints and reused on matching resume. Failed or invalid responses can still repeat charges. There is no automatic rollback, destructive migration or deletion. Model quality/accuracy and publication suitability still require human review.

Models, hints and transport bounds are caller options:

```bash
uv run --locked interview batch run --batch-folder private/batches/demo-001 \
  --step raw --select entry-a --send-to-openai \
  --transcription-model gpt-transcribe --audio-chunk-seconds 300 \
  --context-file private/hints/context.txt --glossary-file private/hints/glossary.txt \
  --language en --request-timeout 120 --request-retries 2 --status-interval 30
```

Use the same ASR model, hints and chunk size in subsequent phases. Editing/review defaults remain `gpt-6-astra` with high reasoning. Explicit `--text-profile balanced` selects Sol 6.1/low polish and Sol 6.1/medium review candidate. Override separately with `--editing-model`/`--editing-reasoning-effort` and `--review-model`/`--review-reasoning-effort`; explicit fields win regardless of flag order. Review model and effort also configure narrative chapters, so a chapter gate must match the chosen reviewed configuration. Assess the candidate using the [text comparison](text-comparison.md) before another full run. `--request-timeout` is the SDK's positive finite request timeout; `--request-retries` is 0–5, default 2. These are transport settings and do not invalidate verified content caches. SDK timeouts/retries are not a whole-job wall-clock limit; retries and sequential chunks extend elapsed time. `--media-timeout` bounds individual FFmpeg operations, default 3600 seconds. Credentials and custom provider endpoint settings remain in the caller's environment.

## Logs, progress and troubleshooting

Installed commands default to an updating panel on a capable interactive terminal. `--progress plain` selects readable scrolling messages; `--progress json` selects flushed newline-delimited JSON. Redirected auto output remains JSON for scripts and CI. Bars count completed or verified reused work with a known total; they do not estimate overall elapsed-time completion. See [reading progress](pipeline-guide.md#read-the-progress-display).

Each underlying event includes an opaque **execution** UUID, monotonic sequence and elapsed seconds. `scope` distinguishes batch counters from an inner interview summary; batch `phase` identifies the requested operation. Events include safe stage status, numbered item/part/chunk counters, known chunk totals where available, processed/failure counters, and `cache_reused: true` for verified skipped stages. Review reports per-core starts and completions; elapsed idle heartbeats continue while a provider or local operation is pending. A heartbeat proves the coordinator is alive, not that the request has succeeded or that a provider is making progress.

`interview transcribe` defaults to `<output>/execution-logs/execution-<run>.jsonl`. Batch prepare/verify/run default to `<batch>/execution-logs/`; all use exclusive run files. Inventory/check/status write console events only unless `--logs-folder` is given. Local logs contain the same allowlisted events as console output, never plan IDs, private paths, transcript text, hints, arbitrary exception strings, raw provider responses or credentials. Source provenance, plan snapshots and author artifacts are separately private content files. Files use mode 0600 and directories 0700 where the filesystem enforces those modes; storage is not encrypted.

```bash
# Single video preparation, local only, with a chosen heartbeat/log directory:
uv run --locked interview transcribe --prepare-audio --input private/input/synthetic-b.mp4 \
  --output-folder private/extracted --logs-folder private/logs --status-interval 10
# Replay the same safe JSONL events after a failed attempt:
cat private/logs/execution-*.jsonl
# Batch failure counters from retained summaries:
uv run --locked interview batch status --batch-folder private/batches/demo-001
```

Safe failures include fixed actionable setup/gate guidance; stage/item counters locate the failed entry via the external plan. Processing continues after local failures. Provider admission stops after consecutive operation failures (default 2), definite account/configuration failures, or explicit request/start-deadline limits; later selected entries are unattempted. See [recovery controls](recovery-controls.md). Exit 0 means all selected entries succeeded, exit 1 means failure/blocked selection, exit 2 means invalid CLI arguments, and exit 130 means keyboard/SIGTERM interruption. Clean interruption stops new scheduling/provider admission and drains active workers before releasing engine/batch locks, closing logs and retaining partial summaries. Returned valid responses are retained; waiting for SDK I/O or local preparation can extend cleanup. Abrupt process death/power loss may leave locks or partial staging; inspect active processes and artifacts locally before manually removing a stale lock. Never remove a lock while another run is active.

If a console pipe closes, durable logs continue. If local logging fails (disk full, permissions), the run fails safely rather than reporting unlogged success; completed caches remain. Missing-key guidance points to `OPENAI_API_KEY` without logging its value. Provider failures use fixed access/quota/connectivity guidance; payload/traceback forwarding and unsafe debug output are unavailable. Long elapsed time alone does not establish a hung provider. Inspect stage/chunk events, transport bounds and network availability, then deliberately interrupt/resume if needed.

Optional `run --separate-speakers` adds a separate attributed family; `--speaker-config` supplies per-entry names and confirmed mappings without editing staging. Missing names retain scoped unidentified speakers. Review and chapter gates apply independently to both selected families. A missing/invalid attributed prerequisite blocks attribution while eligible original work continues, and vice versa when shared source parts remain valid. Blocked prerequisites consume no provider requests and use fixed per-family guidance; the command exits nonzero while retaining completed family results; see [batch media and attribution](batch-media-attribution.md).

Implementation is split into `src/batch/plan.py` (schema/metadata/selection), `storage.py` (copies/verification/locking), `runner.py` (engine invocation and phase gates), `cli.py` (commands/counters), `concurrency.py` (bounded interview scheduling), and `src/progress.py` (event allowlist/logs/heartbeats), `src/provider_errors.py` (safe SDK metadata categories), and `src/review_reuse.py` (locked, byte-identical approved review transfer). Tests use only synthetic media and mocked providers.

### Distinguishing failures before a retry

New execution events and failed private review chunk records include `error_category` and, for recognized SDK HTTP failures, an integer `http_status`. SDK timeouts additionally report `timeout_phase` as `connect`, `write`, `read`, `pool` or `unknown`, based solely on recognized active exception-chain types. A transport phase is not a provider/network root-cause determination; older logs cannot be classified retroactively. Unknown, ambiguous, suppressed, cyclic and overlong chains remain unknown. Categories distinguish authentication, permission, model access, invalid requests, definite quota exhaustion, transient rate limits, connection failures, timeouts, service failures, completion/refusal/truncation, schema/source validation, and unknown failures. Arbitrary exception messages, response bodies, request URLs/headers and request IDs remain excluded. A quota category requires a known SDK quota code; HTTP 429 alone is a rate limit, not proof of exhausted billing quota.

A definite authentication/permission/model/request/quota error stops further chunks **in that interview review**. Remaining coverage cores are retained as failed with `attempted: false` and `not_attempted`; they were not sent to the provider. Review remains failed/incomplete and chapters remain gated. Ordinary transient or validation failures preserve the existing later-chunk attempts and findings. The shared run control now stops subsequent provider starts after definite systemic errors or the configured consecutive-failure threshold. The SDK may already have retried a request before returning the error; request/time limits require zero retries.

Before retrying a failed review:

1. Stop after a diagnostic provider failure; do not advance to bulk processing or review because the diagnostic command finished. Inspect only the safe execution categories/status/counters first. Old generic failure records cannot establish the original HTTP status or timeout cause.
2. Verify retained staging (`interview batch verify`) and source/configuration-bound caches; keep failed outputs and human notes intact. Run the offline installation/progress tests below to check the software without provider requests.
3. Correct known account/model/configuration issues locally. A repeated unknown failure needs investigation before a paid rerun; elapsed time alone is insufficient evidence.
4. If a new attempt is deliberately authorized, select one interview and preserve matching ASR settings. `--request-retries 0 --request-timeout 30` lowers per-request retry/read budgets, but **does not establish a total wall-clock deadline or limit review to one chunk**. To cap new SDK starts, also use `--max-requests 1`; see the [one-operation diagnostic and start-deadline limits](recovery-controls.md). These are not a dollar cap or hard cancellation of remote work.

```bash
uv run --locked pytest -q tests/test_provider_errors.py tests/test_progress.py tests/test_installation.py
uv run --locked interview batch verify --batch-folder private/batches/demo-001
uv run --locked interview batch status --batch-folder private/batches/demo-001
```
