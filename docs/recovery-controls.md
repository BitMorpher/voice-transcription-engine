# Saving progress and bounding provider starts

For current option names, every public parameter, valid combinations and parallel-interview examples, see the [command and parameter guide](cli-reference.md).

A failed long recording can resume from its validated request checkpoints. This applies to original ASR in pipeline/workflow mode and to the separate interview diarization pass. It does not recover successful responses that an older release held only in memory. Pipeline/workflow and batch text stages also checkpoint validated polish, author review and model-generated narrative arrangement. Attributed polish checkpoints bounded groups with stable turn/piece IDs; validated earlier groups are reused when a later group fails. Deterministic interview excerpts make no model requests. Legacy audio-folder mode has no durable request resume; direct library calls must supply `checkpoint_root` to retain text work.

## Checkpoints and compatibility

Each nonempty ASR response and structurally valid diarization response is written privately before the next request. A checkpoint directory is published atomically only after its response and manifest are complete. ASR retains exact text; diarization retains full provider JSON. ASR silence/empty responses are not persisted as reusable checkpoints, so a failed empty recording can be retried. No partial recording is marked complete. Every chunk must return before the original raw transcript or attributed family can be published; mismatched full text/turn words still block attribution while preserving the full provider response.

Bindings cover source SHA-256, prepared-audio SHA-256, model/settings fingerprints, response format, byte/duration limits, exact encoded WAV checksum, chunk order, offsets, durations, total coverage, and the checkpoint contract. Existing checkpoints are validated before new calls for that recording; ordered interviews preflight partial ASR checkpoints across every part. Changed/incomplete/symlink checkpoints fail closed, with no overwrite. Orphan temporary directories are not successful checkpoints. Checksums detect changes relative to the saved manifest; they are not a cryptographic signature against someone replacing both manifest and content.

New checkpoints store the full recording binding and chunk layout once in a private `binding.json`. Each chunk manifest references that binding's fingerprint and its own descriptor, so metadata storage and validation grow linearly with chunk count. Existing version-1 chunk checkpoints remain reusable without rewriting them; a resumed recording can contain both old and new chunk manifests. A missing or changed shared binding blocks reuse of new-format checkpoints.

Original manifest version 2 and whole-recording diarization cache version 1 remain compatible. Verified completed recordings bypass the new chunk caches and remain byte-identical on reuse. Original ASR defaults/model/hints/fingerprint have not changed. Keep the same staging paths and matching ASR settings: ordered cache identities include resolved paths. Copying/moving a batch and processing it at a new location can prevent reuse. A backup can be retained separately while processing at the original path.

## Independent speaker-pass duration

`--speaker-chunk-seconds 60` changes only interview diarization. Omit it to retain the existing behavior of using `--audio-chunk-seconds` (300 by default). Values are 1–600 seconds. A shorter duration is an explicit latency experiment, not a proven quality improvement: it adds request boundaries, may split speech and makes speaker labels reset more often. No live quality/latency validation was performed for this change.

An explicit duration creates a separate attributed generation, binding its cache, provenance and downstream stages. Prior attributed output/reviews remain intact. Original ASR does not repeat solely because this option changes. Every supplied mapping must be checked against the new request scopes; remove mappings initially, listen to the new chunks, then supply the confirmed mappings with `--confirm-speaker-mappings`. Repeating old `PART:REQUEST:LABEL` strings without that confirmation is rejected. Names alone still do not identify voices. Chapters still require complete, source-bound, human-approved review for each requested family without high findings.

## Admission limits and retries

Both installed commands accept:

| Option | Meaning |
| --- | --- |
| `--max-requests N` | At most N new SDK operation starts across original ASR, diarization, polish, review and chapters, over all selected entries. Cache hits consume zero. |
| `--max-run-seconds S` | Stop admitting new provider operations after S elapsed seconds from control initialization, including intervening local work. |
| `--failure-limit N` | Stop after N consecutive provider-operation failures for the same endpoint/model, default 2. A successful operation resets that scope’s streak; successful original ASR does not erase diarization failures. Definite authentication/permission/model/request/quota errors stop admission immediately. |
| `--validation-failure-limit N` | Stop all new provider admissions when one text stage/model reaches N cumulative terminal validation failures in this invocation, default 3. Separate from the provider failure streak; SDK or validated success does not reset it. |
| `--request-retries N` | SDK retries per operation; existing default 2. When greater than zero, eligible local schema/coverage, polish preservation and review quote failures may start one additional application operation. Request/time limits require explicit zero and disable that recovery. |
| `--request-timeout S` | Per-I/O SDK timeout, default 120 seconds. With a start deadline it is reduced to the remaining admission window for each new operation. |

The request counter measures application SDK starts, not money or tokens. Zero retries prevents the SDK's automatic retry attempts; redirects, custom transports and provider billing behavior are not dollar guarantees. With retries enabled, one counted operation may include several HTTP attempts before the application observes failure. The circuit breaker observes the final operation result, not each SDK retry.

`interview batch run --parallel-interviews N` adds explicit overlap across whole interview groups, default 1. Start with 2. All workers share the same request allowance, elapsed admission deadline and per-endpoint/model failure streak; these controls are never multiplied by N. Streaks use response-completion order and a later success cannot reopen stopped admission. Operations admitted before a stop may still finish and save valid responses. A final transient 429 starts a shared cooldown before new operations, followed by 0.25-second spacing. This is reactive pacing, not a configured requests-per-minute or monetary budget. Parts within each interview remain ordered. Prefer one worker and one selection for a diagnostic aimed at a specific stage; scarce allowances across parallel interviews are assigned by scheduling.

The time option is a **start deadline**, not a hard whole-run timeout. It does not cancel an in-flight call, truncate a valid returned response, or stop local media preparation at the deadline. The SDK timeout bounds individual I/O waits, not total processing time; a progressing upload/response can exceed the admission window. A returned valid response is saved, then the next start is denied. Ctrl+C/SIGTERM stops new scheduling and provider admission. Parallel batch cleanup waits for active workers while keeping locks/logs open; local work and already admitted I/O may take time to finish. Returned valid responses are retained before locks are released. Cancellation cannot establish that remote work stopped or was not billed. No background provider worker remains after normal cleanup returns.

Provider failures and repeated terminal text validation failures stop at their separate configured thresholds. Media failures remain isolated between independent interviews. Later unscheduled entries are `not_attempted`, active entries denied by request/time limits are `incomplete`, and unfinished active entries on signal cancellation are `interrupted`. Already active entries that finish successfully remain complete. Counts separate selected, processed, incomplete and unattempted entries. Incomplete/interrupted/unattempted states never satisfy review/chapter completion gates. A run returns nonzero if any selected work remains incomplete or unattempted.

## One-operation diagnostic and deliberate recovery

This example is a command for a future explicitly authorized paid test. It is not executed by tests or installation. An existing staged session with verified original ASR reaches the speaker pass without repeating ASR:

```bash
interview batch run --batch-folder private/batches/demo-001 --select entry-a \
  --step raw --separate-speakers --speaker-config private/config/speakers.json \
  --transcription-model gpt-transcribe --audio-chunk-seconds 300 \
  --speaker-chunk-seconds 60 --request-timeout 120 --request-retries 0 \
  --max-requests 1 --max-run-seconds 180 --send-to-openai
```

The first uncached provider operation consumes the allowance, regardless of stage. If original ASR is incomplete, that operation may be ASR instead of diarization. It may also complete a sufficiently short session or use no allowance when all selected work is cached. For longer sessions a nonzero incomplete result is expected, with the completed chunk checkpoint retained. This is not a one-chunk quality assessment for a full interview and does not produce a full transcript automatically.

**Stop after a diagnostic provider failure.** A timeout/connection/account failure is not the expected request-limit stop and is not evidence of a saved chunk. Do not proceed to all sessions or enlarge the allowance until that failure is investigated. Only after confirming a valid returned response/checkpoint and deliberately authorizing further paid processing should you repeat with a larger allowance. Preserve the ASR model/hints/chunk setting and independent diarization duration to reuse checkpoints. Do not blindly launch every interview after repeated failures. `interview batch` resumes automatically; **it has no `--resume` option**. Direct `interview transcribe` pipeline/workflow recovery uses `--resume`.

## Safe status and configuration

Every new run records allowlisted effective model enums, editing/review reasoning efforts, selected text profile, chunk durations, provider timeout/retries, admission limits, failure threshold, interview-enabled flag, hint-presence booleans and language-hint count. Explicit model/effort flags override a profile independently; recorded effective values show the resulting combination. It records no supplied names, mappings, hint content, source/output paths, plan IDs, keys, endpoints, request IDs or provider payloads. Per-call events show admitted operation count and effective I/O timeout. Full responses belong only in private checkpoint files.

Text events include a cumulative `text_metrics` dictionary. `sdk_operations_started`, `sdk_operations_completed` and `sdk_operations_failed` count text SDK calls after admission/pacing, not HTTP attempts or billed requests. `cache_hits` counts validated request checkpoints reused; `validation_retries` counts additional local recovery calls actually started. `usage_responses` counts returned usage with valid prompt/completion counts, and `usage_missing` counts unavailable usage, including exceptions. `prompt_tokens`, `completion_tokens` and `total_tokens` sum provider-reported values only; each token field has a `_reported_operations` availability count. `reasoning_tokens` is a subset of completion tokens, and `cached_prompt_tokens` a subset of prompt tokens: do not add them twice. Missing usage stays unknown, not zero billing. `usage_complete` is true only when every started operation returned usable main usage.

`latency_seconds` sums time inside SDK calls, including SDK retries, with `latency_observations` showing the measured count. It excludes admission/pacing and cache access, and concurrent sums can exceed run elapsed time. `sdk_internal_retries_observed` remains false because the wrapper cannot count them. No token counter is a dollar cap or bill; combine available usage with current provider pricing and inspect account billing. These counters do not disclose content, keys, raw errors or request IDs.

`interview batch status --batch-folder ...` presents chronological history with the original `historical_run` and `started_at`, then the latest **recorded** stages per item, family and phase, with `latest_run`. Original completion remains visible when attribution fails, and original-only review does not replace attributed history. Latest stage sets come from one run rather than combining different settings/generations. The envelope `run` is the status command's identity; the explicit historical/latest fields identify the earlier execution. Legacy summaries use their filesystem modification time when no timestamp exists and do not acquire inferred family success. Status does not read transcripts/media or freshly verify artifact checksums; use the existing verification/gates before processing.

Offline regressions cover later-chunk failures, restart reuse, corrupt/rebound checkpoints, old completed cache reuse, the synthetic 43/47 recovery case, independent chunk scopes, SDK mock-transport timeout/retry behavior, cross-stage limits, failure streaks, signals and mixed-family chronology. They validate software contracts, not live provider accuracy, latency or cost.

## Diagnose the failure before another paid run

New SDK timeout events include a fixed `timeout_phase`: `connect` (connection establishment), `write` (sending request data), `read` (waiting for response data), `pool` (waiting for a local connection slot), or `unknown`. Classification follows recognized transport exception types, never message text. Unknown or ambiguous evidence stays unknown; a `read` result does not establish the provider/network root cause. The SDK may already have retried before returning its final exception when retries are enabled. Old logs cannot be reclassified from elapsed time alone.

Review is not a remedy for incomplete raw. Batch review/chapters check each family separately, record unavailable prerequisites as `blocked`, and continue only the eligible families. For example, complete original raw with missing attribution can produce complete original review and blocked attributed review in the same run. The command returns nonzero and preserves both facts. No audio requests are made by a text phase to fill missing prerequisites. To request only original review, omit interview mode and the speaker configuration and select known-ready originals.

Chapter gates also remain independent: a missing review or high findings in one family blocks that family’s chapters without approving it from another family’s report. The other family can proceed only with its own exact reviewed bundle, explicit selection and actual human approval. Blocked gates have fixed `blocked_reason` guidance; they do not consume request allowance or count as provider failures. Saved summaries retain `family_blockers`, stage states and each family’s `phase_result`; status reports this recorded evidence without reading media/transcripts.

## Validated text resumption

Every successful text request is saved before requesting the next chunk. Job-local work uses private `text-chunks`; original ordered polish uses a shared private `text-chunks/original/<raw-identity>/<editing-and-source-binding>` so review/chapter changes can reuse it. A failed/incomplete response is never promoted. On restart, all saved requests for that stage are preflighted before new requests, then revalidated on reuse and before stage publication. Completed old bundles continue to require their existing integrity checks; successful text held only in memory by an older version cannot be recovered.

Text identities bind the full source hash, stage and validator contract, settings fingerprint and exact request hashes. Those requests include model, prompt bytes, JSON schema, reasoning effort, output budget, exact source/context boundaries and chunk index. Narrative arrangement additionally binds the exact review JSON digest, including human dispositions. Each family has its own private job root; attributed groups bind ordered turn/piece IDs and preserve source boundaries. Separate derivative records retain each model/effort version while unchanged raw/audio identities remain reusable. Legacy per-turn polish reuse requires the original fingerprints, exact headers/separators and per-turn word fidelity; it never bypasses hashes. Response hashes and strict local validation reject corruption, duplicate JSON keys, source substitutions, missing files and symlinks. Publication remains atomic. As with audio checkpoints, checksums are change detection, not authentication against someone replacing both manifest and response. Keep the private directory access controls intact.

With `--request-retries 2`, a malformed JSON/schema response or missing exact review coverage may receive **one** additional text request. It uses the same shared admission checks and increments `provider_requests`; it is not an unlimited retry loop. Each operation can independently have up to two SDK retries, so one recovered text chunk can start up to six HTTP attempts. Eligible local validation failures do not increase the provider breaker streak. Polish preservation failures and schema-valid review evidence failures can also receive that same single correction attempt. The correction uses the unchanged source, model, effort, schema and budget, plus the rejected response held only in memory and fixed repair instructions. It does not add a second recovery allowance after schema/coverage recovery. Every replacement must pass the original strict validator. Review evidence corrections must retain the number and order of finding reason codes and every already-valid finding unchanged; a response cannot pass by dropping criticism. Chapter source/provenance violations, refusals and incomplete completions are not repeated automatically. No smaller-group isolation, deterministic identity fallback, fuzzy quote matching or validator bypass is introduced. `--request-retries 0` disables local recovery as well as SDK retries. Authentication, quota, access and invalid-request failures retain their immediate shared stop.

Safe events and failed review coverage now separate `validation_schema`, `validation_coverage`, `validation_source`, `validation_diarization` and `completion` from provider/transport errors. No exception prose, excerpts, names or headers enter logs. A speaker response rejected locally is not reported as an unknown provider failure. These categories describe evidence; they do not prove the provider's root cause.

After a final SDK 429 classified as transient rate limiting, the run pauses new starts across workers and endpoints. Numeric Retry-After seconds, milliseconds and HTTP dates are read locally; waits are bounded to 1–120 seconds, with a two-second fallback. Starts are then spaced by at least 0.25 seconds for the remainder of that execution. Waiting workers check cancellation, deadline and breaker stops without consuming requests. Already admitted calls and SDK-internal retries cannot be coordinated or recalled through this wrapper. The final failed operation still counts toward the endpoint/model breaker; quota is not treated as transient rate limiting.

## Full runs: omit diagnostic caps

The caps are opt-in diagnostic controls, not defaults or recommended full-run settings. In particular, `--max-run-seconds 1800` also shortens each new request's I/O timeout to the remaining window; it can leave a late review with less than 600 seconds. The limit is not merely admission-only: it constrains the timeout passed to admitted SDK operations, while still not being a hard wall-clock cancellation guarantee.

For deliberate full processing of an already prepared private batch, start with two interviews, timeout 600 and retries 2. **Omit both** `--max-requests` and `--max-run-seconds`:

```bash
interview batch run --batch-folder private/batches/demo-001 --step raw \
  --separate-speakers --speaker-config private/config/speakers.json \
  --transcription-model gpt-transcribe --audio-chunk-seconds 300 \
  --parallel-interviews 2 --request-timeout 600 --request-retries 2 \
  --failure-limit 2 --send-to-openai

interview batch run --batch-folder private/batches/demo-001 --step review \
  --separate-speakers --speaker-config private/config/speakers.json \
  --transcription-model gpt-transcribe --audio-chunk-seconds 300 \
  --parallel-interviews 2 --request-timeout 600 --request-retries 2 \
  --failure-limit 2 --send-to-openai
```

Repeat matching commands to resume automatically. Keep model, hints, speaker settings and chunk durations consistent across phases; include an experimental speaker duration in both commands if using it. Original and attributed prerequisites remain independent: complete original text can be reviewed while the attributed family stays blocked. Review never purchases missing audio. Invalid checkpoints fail closed; retain them and investigate rather than deleting caches or starting blind bulk retries.

After checking the exact recordings, transcripts and both complete reports, eligible explicitly selected entries can produce chapters:

```bash
interview batch run --batch-folder private/batches/demo-001 --step chapters --select entry-a \
  --human-reviewed --chapter-style both --separate-speakers \
  --speaker-config private/config/speakers.json \
  --transcription-model gpt-transcribe --audio-chunk-seconds 300 \
  --parallel-interviews 2 --request-timeout 600 --request-retries 2 \
  --failure-limit 2 --send-to-openai
```

Every high finding still blocks batch chapter drafting. Workbook edits are not imported as approval. Do not modify the machine bundle to bypass this gate. No live interview runs were performed to validate accuracy, quality, latency or price; all implementation tests use invented text/media and offline providers. Increase concurrency only after repeatable successful processing and an account-limit review. See the [shorter speaker-chunk experiment](speaker-chunk-experiment.md) for a focused 120/60-second comparison that keeps ASR at 300 seconds.


## Review evidence contract and validation stop

Author review v2 supplies exact source fragments as `evidence_pieces`, each with an opaque `piece_id`. IDs are deterministic absolute source-span identifiers and are bound to source bytes by request/checkpoint hashes. Each core and its neighbors are partitioned into pieces of at most 1024 UTF-8 source bytes. Short lines share pieces to bound ID/schema overhead; long sentences may span pieces. Characters, CRLF, Unicode marks and joining controls are never normalized. Existing line segment IDs and final Unicode character offsets remain unchanged.

A finding supplies an exact `excerpt` and ordered contiguous `piece_ids`. Python searches only those referenced pieces and calculates the final zero-based, end-exclusive source offsets. The quote must occur exactly once, intersect every referenced piece and intersect the requested core. Repeated text needs a narrower piece reference or a longer exact quote with distinguishing context. Missing, altered, empty or ambiguous quotes fail closed without fuzzy matching or invented offsets. An eligible correction attempt must satisfy the same exact-source checks and preserve existing findings as described above. One invalid finding rejects that chunk, retaining earlier verified chunks.

Coverage is explicit: the response copies `contract_version` and `chunk_index`; `fully_reviewed=true` requires `reviewed_piece_ids` to equal the supplied `core_piece_ids` exactly, in order. Context IDs, omissions, duplicates or a different version cannot satisfy coverage. `fully_reviewed=false` remains incomplete. Validated complete chunks record these IDs in report coverage. An exact acknowledgement is a structural record, not proof that the model detected every relevant issue.

`--validation-failure-limit` defaults to **3** and accepts positive integers. Each final local text-validation failure counts once, after the single eligible correction is exhausted, or immediately for non-retryable failures such as refusal/truncation or chapter source violations. Intermediate recovery failures that subsequently succeed do not count. SDK exceptions, cache integrity errors, media failures, cache hits and denied recoveries do not count. All parallel workers share cumulative counts per text stage/model: original and attributed requests using the same stage/model share a count; different stages or models do not pool failures. SDK successes and validated successes never reset counts. When any one scope reaches its threshold, `stop_reason=validation_failures` closes provider admissions for the entire invocation and prevents new batch-session scheduling. Already admitted requests drain and can save valid checkpoints; late success cannot reopen admission. Remaining work is incomplete or not attempted. A fresh invocation resets the observations and reuses matching verified checkpoints. The existing provider breaker remains independent.

Safe events distinguish `validation_quote_missing`, `validation_quote_ambiguous`, `validation_source`, `validation_schema`, `validation_coverage` and `completion`. `validation_failures` and `scope_validation_failures` count terminal decisions; SDK-operation and retry counters retain their existing meanings. No quotes, arbitrary IDs, exception prose, keys or invalid response bodies enter logs. Invalid responses are never saved in the trusted checkpoint cache, and this change adds no diagnostic body retention.

The v2 prompt, schema, evidence layout and validator contract select a new checkpoint/settings identity. V1 reports remain verifiable as exact-offset source records using the unchanged packaged v1 prompt; their bytes are not migrated or overwritten. V1 request checkpoints and human approvals do not automatically satisfy v2 resume or chapter gates. New review requires its own complete report and human approval. Original ASR/speaker caches and PR20 grouped polish/versioned derivative reuse keep their existing identities and fidelity checks.

The original failure mechanism was a brittle source contract: models supplied both quotes and manually calculated character positions, and local validation rejected a whole chunk if one slice differed. The prompt also failed to define the exact returned coverage values. Successful SDK responses reset the provider breaker before local validation, allowing repeated unusable responses to continue purchasing work. The failed live bodies were not retained, so wrong offsets versus altered quotes cannot be distinguished retrospectively. No SDK defect, Unicode-specific live cause, or model-specific cause was established.

Offline synthetic tests verify these contracts, including real SDK serialization, source/coverage rejection, repeated/Unicode quotes, boundaries, legacy integrity, parallel stop/drain, cancellation, counters and resume. They do not establish live quality, latency, or billing. Before another full batch, review this draft change and deliberately authorize a single synthetic live review request with Sol 6.1 medium, retries 0, one worker, a one-request allowance and `--validation-failure-limit 1` in a fresh private comparison output. Include exact repeated quotes and a boundary-spanning passage. Stop and inspect acknowledgement, source spans and semantic findings; do not expand to private interview samples or a batch without a separate decision. Keep editing Sol low and the existing ASR/speaker settings unchanged. No canary was executed for this change.


## Strict correction diagnostics and interrupted work

Every event now includes `timestamp_utc`, generated locally, alongside monotonic `elapsed_seconds` and sequence. Text SDK calls emit a run-local numeric `sdk_operation`, `sdk_status` (`started`, `returned`, or `failed`) and, at return/failure, `sdk_seconds`. Item, family, stage and chunk correlate those events with validation and checkpoints. An SDK return is not a validated derivative. The duration measures the SDK call including hidden SDK retries; it excludes admission, validation and checkpoint publication. Individual HTTP attempts, provider compute time and network time remain unobservable.

`validation_diagnostics` accepts only counts, indexes, fixed mismatch types and a boolean. Editing diagnostics give source/response token counts, zero-based first mismatch token and `omission`, `token_count` or `token_sequence`; grouped editing adds one-based group, turn-within-group and piece indexes. These are validator observations, not proof of which linguistic edit occurred. Review diagnostics give one-based finding/piece indexes and piece count when available. For a missing quote, `quote_found_elsewhere` reports exact presence elsewhere in the supplied context; it never relocates a quote automatically or searches private material outside that request. No tokens, quotes, names, arbitrary IDs or rejected bodies are serialized. Validation-breaker events include allowlisted stage/model scope.

A retry-in-progress is not a terminal error. A primary terminal failure remains failed even when it trips the breaker. Other started families stopped only by admission are incomplete; families and chunks never requested are not attempted. Queued sessions remain not attempted. The review workbook accepts these coverage states and continues to block chapter drafting. Review resume drains later verified checkpoints even when an earlier missing chunk cannot be requested, preserving their findings and coverage in the current report. All prior bundles and valid checkpoint bytes remain intact.

These changes preserve the v2 review prompt/schema, grouped-edit contract and all base request fingerprints. Correction policy affects only failed requests; valid old derivatives and checkpoints need no migration or regeneration. A correction result is saved under the unchanged base request only after its full source validator and, for review evidence correction, finding-retention guard pass. No invalid response enters that cache.
