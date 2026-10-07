# Portable batch orchestration and safe execution events

`voice-batch` processes independent interviews serially. Each interview has an explicit ordered manifest and may contain one audio file, one video file, or multiple mixed recordings. `voice-transcribe` remains the single interview/file coordinator. Both commands are installed in the `voice_transcription_engine` package and work outside the source checkout. Python 3.14+, the declared Python dependencies, FFmpeg and ffprobe on `PATH`, and your own environment settings are the prerequisites. No checkout pin, account name, device path, shell launcher, or platform package-manager path is configured in a batch plan.

From a checkout:

```bash
uv sync --locked
uv run --locked voice-batch --help
uv run --locked voice-transcribe --help
```

For an independent environment, install a locally built wheel with your standard installer:

```bash
uv build
uv venv --python 3.14 private/runtime
uv pip install --python private/runtime/bin/python dist/voice_transcription_engine-0.1.0-py3-none-any.whl
# Activate that environment and use voice-batch / voice-transcribe from any working directory.
# On Windows, use private/runtime/Scripts/python.exe and the corresponding activation command.
```

Keep private plans, manifests, media, hints, staging and outputs outside Git or under this repository's ignored `private/` tree. A plan is user input, not repository configuration. JSON paths are relative to the containing JSON file; absolute local paths also work. Arbitrary symlinks, URL inputs, duplicate source identities, duplicate JSON keys and invalid schema fields are rejected. Supported audio is WAV/MP3/M4A; video is MP4/MOV/MKV/WebM/AVI/M4V. Extensionless files require explicit `media_type: "video"`; inventory/check accept their metadata, while prepare must validate a copied local container before assigning a staged suffix. See [batch media and attribution](batch-media-attribution.md). Do not put personal IDs in public examples.

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
uv run --locked voice-batch inventory --plan private/config/batch-plan.json
# Also check FFmpeg/ffprobe availability; still no media-content or provider reads.
uv run --locked voice-batch check --plan private/config/batch-plan.json --select entry-a
# Repeated selection runs in plan order, not argument order. Exclusion is exact.
uv run --locked voice-batch inventory --plan private/config/batch-plan.json \
  --select entry-b --select entry-a --exclude entry-b
# Explicit source reads and verified local copies, into a NEW directory:
uv run --locked voice-batch prepare --plan private/config/batch-plan.json \
  --select entry-a --batch private/batches/demo-001 --copy-local-files
uv run --locked voice-batch verify --batch private/batches/demo-001
```

Metadata preflight is not codec validation. The ordered engine decodes **all parts of each selected interview before its first ASR call**. An invalid interview does not prevent later independent selected interviews from being attempted. `prepare` never calls providers. Extensionless video staging uses a bounded local ffprobe on an exact private copy, not a filename guess or an original rename. Supported named media retains the existing staging path and verification contract. It copies into generic numbered filenames, hashes originals and copies, and checks original metadata/content before accepting the staging ledger. Originals are never modified. macOS dataless sources need explicit `--allow-hydration` on `prepare`; inventory/check only inspect their metadata. On other systems a file open may trigger remote filesystem reads; keep staging on a filesystem whose behavior you control.

Preparation writes an exclusive immutable plan snapshot, selected IDs, per-entry manifest and staging ledger. Partial staging is retained for inspection; use a fresh batch directory after a failed prepare. Never edit staged manifests, ledgers or media. `verify` checks their recorded bytes and hashes without accessing original recordings; originals may subsequently be unavailable. The stage snapshot/checksums protect accidental modification, not an attacker who can rewrite both records and hashes.

## Raw, review and human-approved chapters

```bash
# Explicit opt-in to provider calls. Defaults: gpt-transcribe, 300-second ASR chunks.
uv run --locked voice-batch run --batch private/batches/demo-001 \
  --phase raw --send-to-openai
# Raw must already be complete for these sources and ASR settings.
uv run --locked voice-batch run --batch private/batches/demo-001 \
  --phase review --send-to-openai
# Inspect raw recordings and the private review reports separately before this command.
uv run --locked voice-batch run --batch private/batches/demo-001 \
  --phase chapters --select entry-a --human-reviewed --send-to-openai \
  --chapters both --narrative-person first
# Resume is automatic for batch processing; intact complete stages are reused.
uv run --locked voice-batch run --batch private/batches/demo-001 \
  --select entry-a --phase review --send-to-openai
uv run --locked voice-batch status --batch private/batches/demo-001
```

The batch chapter phase requires explicit IDs, `--human-reviewed`, and a complete source/settings-bound review with **no high findings**. The flag records the caller's decision; it does not infer approval from an Excel edit. Preserve machine bundles and keep human notes in separate copies. There is no batch unresolved-high override. Review failure, incomplete coverage, altered raw/review files, or changed binding prevents chapters. The direct engine retains its separately documented draft override.

Batch chapters reuse the **exact review JSON and XLSX bytes accepted by the human gate**. A new chapter configuration may create a new ordered generation; its review bundle is copied from the approved generation, not regenerated by the provider. The gate captures manifest/raw/provenance/review identities, and the copy is revalidated under the interview lock before author operations. Changed approval data or a conflicting review already present in the target generation fails closed without requesting a new review or silently replacing that bundle. The chapter manifest's review SHA binds drafts to the approved JSON. Reusing the same bundle does not remove the explicit `--human-reviewed`/selection requirement.

Raw transcripts retain exact part bytes and separators. All part caches and combined generations use the existing source/prompt/model/settings bindings; batch orchestration does not weaken them. Changing chapter settings can generate another combined generation while reusing ASR and the exact gate-approved review bundle. ASR, polish, review and narrative chunks are **not durably checkpointed within a stage**; a failed stage can repeat successful requests and incur charges on resume. There is no automatic rollback or deletion. Model quality/accuracy and publication suitability still require human review.

Models, hints and transport bounds are caller options:

```bash
uv run --locked voice-batch run --batch private/batches/demo-001 \
  --phase raw --select entry-a --send-to-openai \
  --model gpt-transcribe --audio-chunk-seconds 300 \
  --context-file private/hints/context.txt --glossary-file private/hints/glossary.txt \
  --language en --provider-timeout 120 --provider-retries 2 --heartbeat-seconds 30
```

Use the same ASR model, hints and chunk size in subsequent phases. Editing/review model defaults remain `gpt-6-astra`; change with `--editing-model`/`--author-model` when intentionally choosing a new configuration. `--provider-timeout` is the SDK's positive finite request timeout; `--provider-retries` is 0–5, default 2. These are transport settings and do not invalidate verified content caches. SDK timeouts/retries are not a whole-job wall-clock limit; retries and sequential chunks extend elapsed time. `--media-timeout` bounds individual FFmpeg operations, default 3600 seconds. Credentials and custom provider endpoint settings remain in the caller's environment.

## Logs, progress and troubleshooting

Installed commands show a transient live dashboard on interactive terminals, with finished-item `X/Y`, outcome/error counts, active/queued counts and each active item's stage and known part/chunk position. The bar measures item outcomes, not processing time; no stage percentage or ETA is invented. Processing stays serial. The dashboard clears on exit and leaves readable outcomes, safe guidance and a final summary. Select `--plain` for append-only readable output without color or cursor controls, `--progress json` for JSON even on terminals, `--quiet` to suppress progress while retaining logs/exit status, and `--no-color` (or `NO_COLOR`) to disable color. Redirected output and dumb terminals default to JSON. See the [display options and counting definitions](../README.md#serial-batches-live-progress-and-local-execution-logs).

JSON events flush immediately and include an opaque **execution** UUID, monotonic sequence and elapsed seconds. `scope` distinguishes batch counters from an inner interview summary; batch `phase` identifies the requested operation. Events include safe stage status, numbered item/part/chunk counters, known chunk totals where available, processed/failure counters, and `cache_reused: true` for verified skipped stages. Review reports per-core starts and completions; elapsed idle heartbeats continue while a provider or local operation is pending. A heartbeat proves the coordinator is alive, not that the request has succeeded or that a provider is making progress. Human output displays the wait without advancing its completed count.

`voice-transcribe` defaults to `<output>/execution-logs/execution-<run>.jsonl`. Batch prepare/verify/run default to `<batch>/execution-logs/`; all use exclusive run files. Inventory/check/status write console events only unless `--log-directory` is given. Local logs retain the allowlisted JSON events in every display mode, never plan IDs, private paths, transcript text, hints, arbitrary exception strings, raw provider responses or credentials. Source provenance, plan snapshots and author artifacts are separately private content files. Files use mode 0600 and directories 0700 where the filesystem enforces those modes; storage is not encrypted.

```bash
# Single video preparation, local only, with a chosen heartbeat/log directory:
uv run --locked voice-transcribe --extract-only --input private/input/synthetic-b.mp4 \
  --output-folder private/extracted --log-directory private/logs --heartbeat-seconds 10
# Replay the same safe JSONL events after a failed attempt:
cat private/logs/execution-*.jsonl
# Batch failure counters from retained summaries:
uv run --locked voice-batch status --batch private/batches/demo-001
```

Safe failures include fixed actionable setup/gate guidance; stage/item counters locate the failed entry via the external plan. Processing continues with the next independent entry after ordinary failure. Exit 0 means all selected entries succeeded, exit 1 means failure/blocked selection, exit 2 means invalid CLI arguments, and exit 130 means keyboard/SIGTERM interruption. Clean interruption releases engine/batch locks, closes logs and retains batch partial summaries. Abrupt process death/power loss may leave locks or partial staging; inspect active processes and artifacts locally before manually removing a stale lock. Never remove a lock while another run is active.

If a console pipe closes, durable logs continue. If local logging fails (disk full, permissions), the run fails safely rather than reporting unlogged success; completed caches remain. Missing-key guidance points to `OPENAI_API_KEY` without logging its value. Provider failures use fixed access/quota/connectivity guidance; payload/traceback forwarding and unsafe debug output are unavailable. Long elapsed time alone does not establish a hung provider. Inspect stage/chunk events, transport bounds and network availability, then deliberately interrupt/resume if needed.

Optional `run --interview` adds a separate attributed family; `--speaker-config` supplies per-entry names and confirmed mappings without editing staging. Missing names retain scoped unidentified speakers. Review and chapter gates apply independently to both selected families; see [batch media and attribution](batch-media-attribution.md).

Implementation is split into `src/batch/plan.py` (schema/metadata/selection), `storage.py` (copies/verification/locking), `runner.py` (engine invocation and phase gates), `cli.py` (commands/counters), and `src/progress.py` (event allowlist/logs/heartbeats), `src/console_progress.py` (human display and outcome accounting), `src/provider_errors.py` (safe SDK metadata categories), and `src/review_reuse.py` (locked, byte-identical approved review transfer). Tests use only synthetic media and mocked providers.

### Distinguishing failures before a retry

New execution events and failed private review chunk records include `error_category` and, for recognized SDK HTTP failures, an integer `http_status`. Categories distinguish authentication, permission, model access, invalid requests, definite quota exhaustion, transient rate limits, connection failures, timeouts, service failures, completion/refusal/truncation, schema/source validation, and unknown failures. Arbitrary exception messages, response bodies, request URLs/headers and request IDs remain excluded. A quota category requires a known SDK quota code; HTTP 429 alone is a rate limit, not proof of exhausted billing quota.

A definite authentication/permission/model/request/quota error stops further chunks **in that interview review**. Remaining coverage cores are retained as failed with `attempted: false` and `not_attempted`; they were not sent to the provider. Review remains failed/incomplete and chapters remain gated. Ordinary transient or validation failures preserve the existing later-chunk attempts and findings. Independent batch interviews retain failure isolation. The SDK may already have retried a request before returning the error.

Before retrying a failed review:

1. Inspect only the safe execution categories/status/counters first. Old generic failure records cannot establish the original HTTP status or timeout cause.
2. Verify retained staging (`voice-batch verify`) and source/configuration-bound caches; keep failed outputs and human notes intact. Run the offline installation/progress tests below to check the software without provider requests.
3. Correct known account/model/configuration issues locally. A repeated unknown failure needs investigation before a paid rerun; elapsed time alone is insufficient evidence.
4. If a new attempt is deliberately authorized, select one interview and preserve matching ASR settings. `--provider-retries 0 --provider-timeout 30` lowers per-request retry/read budgets, but **does not establish a total wall-clock deadline or limit review to one chunk**. Do not represent this command as a fully bounded paid diagnostic. A whole-run supervisor or a separately designed single-request diagnostic is outside this change.

```bash
uv run --locked pytest -q tests/test_provider_errors.py tests/test_progress.py tests/test_installation.py
uv run --locked voice-batch verify --batch private/batches/demo-001
uv run --locked voice-batch status --batch private/batches/demo-001
```
