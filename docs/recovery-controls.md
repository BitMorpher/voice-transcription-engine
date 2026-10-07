# Saving progress and bounding provider starts

For current option names, every public parameter, valid combinations and parallel-interview examples, see the [command and parameter guide](cli-reference.md).

A failed long recording can resume from its validated request checkpoints. This applies to original ASR in pipeline/workflow mode and to the separate interview diarization pass. It does not recover successful responses that an older release held only in memory. Pipeline/workflow and batch text stages also checkpoint validated polish, author review and model-generated narrative arrangement. Attributed polish checkpoints each scoped speech turn. Deterministic interview excerpts make no model requests. Legacy audio-folder mode has no durable request resume; direct library calls must supply `checkpoint_root` to retain text work.

## Checkpoints and compatibility

Each nonempty ASR response and structurally valid diarization response is written privately before the next request. A checkpoint directory is published atomically only after its response and manifest are complete. ASR retains exact text; diarization retains full provider JSON. ASR silence/empty responses are not persisted as reusable checkpoints, so a failed empty recording can be retried. No partial recording is marked complete. Every chunk must return before the original raw transcript or attributed family can be published; mismatched full text/turn words still block attribution while preserving the full provider response.

Bindings cover source SHA-256, prepared-audio SHA-256, model/settings fingerprints, response format, byte/duration limits, exact encoded WAV checksum, chunk order, offsets, durations, total coverage, and the checkpoint contract. Existing checkpoints are validated before new calls for that recording; ordered interviews preflight partial ASR checkpoints across every part. Changed/incomplete/symlink checkpoints fail closed, with no overwrite. Orphan temporary directories are not successful checkpoints. Checksums detect changes relative to the saved manifest; they are not a cryptographic signature against someone replacing both manifest and content.

New checkpoints store the full recording binding and chunk layout once in a private `binding.json`. Each chunk manifest references that binding's fingerprint and its own descriptor, so metadata storage and validation grow linearly with chunk count. Existing version-1 chunk checkpoints remain reusable without rewriting them; a resumed recording can contain both old and new chunk manifests. A missing or changed shared binding blocks reuse of new-format checkpoints.

Original manifest version 2 and whole-recording diarization cache version 1 remain compatible. Verified completed recordings bypass the new chunk caches and remain byte-identical on reuse. Original ASR defaults/model/hints/fingerprint have not changed. Keep the same staging paths and matching ASR settings: ordered cache identities include resolved paths. Copying/moving a batch and processing it at a new location can prevent reuse. A backup can be retained separately while processing at the original path.

## Independent speaker-pass duration

`--diarization-chunk-seconds 60` changes only interview diarization. Omit it to retain the existing behavior of using `--audio-chunk-seconds` (300 by default). Values are 1–600 seconds. A shorter duration is an explicit latency experiment, not a proven quality improvement: it adds request boundaries, may split speech and makes speaker labels reset more often. No live quality/latency validation was performed for this change.

An explicit duration creates a separate attributed generation, binding its cache, provenance and downstream stages. Prior attributed output/reviews remain intact. Original ASR does not repeat solely because this option changes. Every supplied mapping must be checked against the new request scopes; remove mappings initially, listen to the new chunks, then supply the confirmed mappings with `--confirm-speaker-mappings`. Repeating old `PART:REQUEST:LABEL` strings without that confirmation is rejected. Names alone still do not identify voices. Chapters still require complete, source-bound, human-approved review for each requested family without high findings.

## Admission limits and retries

Both installed commands accept:

| Option | Meaning |
| --- | --- |
| `--max-provider-requests N` | At most N new SDK operation starts across original ASR, diarization, polish, review and chapters, over all selected entries. Cache hits consume zero. |
| `--max-run-seconds S` | Stop admitting new provider operations after S elapsed seconds from control initialization, including intervening local work. |
| `--provider-failure-limit N` | Stop after N consecutive provider-operation failures for the same endpoint/model, default 2. A successful operation resets that scope’s streak; successful original ASR does not erase diarization failures. Definite authentication/permission/model/request/quota errors stop admission immediately. |
| `--provider-retries N` | SDK retries per operation; existing default 2. When greater than zero, eligible local text schema/coverage failures may start one additional application operation. Request/time limits require explicit zero and disable that recovery. |
| `--provider-timeout S` | Per-I/O SDK timeout, default 120 seconds. With a start deadline it is reduced to the remaining admission window for each new operation. |

The request counter measures application SDK starts, not money or tokens. Zero retries prevents the SDK's automatic retry attempts; redirects, custom transports and provider billing behavior are not dollar guarantees. With retries enabled, one counted operation may include several HTTP attempts before the application observes failure. The circuit breaker observes the final operation result, not each SDK retry.

`voice-batch run --parallel-interviews N` adds explicit overlap across whole interview groups, default 1. Start with 2. All workers share the same request allowance, elapsed admission deadline and per-endpoint/model failure streak; these controls are never multiplied by N. Streaks use response-completion order and a later success cannot reopen stopped admission. Operations admitted before a stop may still finish and save valid responses. A final transient 429 starts a shared cooldown before new operations, followed by 0.25-second spacing. This is reactive pacing, not a configured requests-per-minute or monetary budget. Parts within each interview remain ordered. Prefer one worker and one selection for a diagnostic aimed at a specific stage; scarce allowances across parallel interviews are assigned by scheduling.

The time option is a **start deadline**, not a hard whole-run timeout. It does not cancel an in-flight call, truncate a valid returned response, or stop local media preparation at the deadline. The SDK timeout bounds individual I/O waits, not total processing time; a progressing upload/response can exceed the admission window. A returned valid response is saved, then the next start is denied. Ctrl+C/SIGTERM stops new scheduling and provider admission. Parallel batch cleanup waits for active workers while keeping locks/logs open; local work and already admitted I/O may take time to finish. Returned valid responses are retained before locks are released. Cancellation cannot establish that remote work stopped or was not billed. No background provider worker remains after normal cleanup returns.

Provider failures stop at the configured threshold; local validation/media failures remain isolated between independent interviews. Later unscheduled entries are `not_attempted`, active entries denied by request/time limits are `incomplete`, and unfinished active entries on signal cancellation are `interrupted`. Already active entries that finish successfully remain complete. Counts separate selected, processed, incomplete and unattempted entries. Incomplete/interrupted/unattempted states never satisfy review/chapter completion gates. A run returns nonzero if any selected work remains incomplete or unattempted.

## One-operation diagnostic and deliberate recovery

This example is a command for a future explicitly authorized paid test. It is not executed by tests or installation. An existing staged session with verified original ASR reaches the speaker pass without repeating ASR:

```bash
voice-batch run --batch private/batches/demo-001 --select entry-a \
  --phase raw --interview --speaker-config private/config/speakers.json \
  --transcription-model gpt-transcribe --audio-chunk-seconds 300 \
  --diarization-chunk-seconds 60 --provider-timeout 120 --provider-retries 0 \
  --max-provider-requests 1 --max-run-seconds 180 --send-to-openai
```

The first uncached provider operation consumes the allowance, regardless of stage. If original ASR is incomplete, that operation may be ASR instead of diarization. It may also complete a sufficiently short session or use no allowance when all selected work is cached. For longer sessions a nonzero incomplete result is expected, with the completed chunk checkpoint retained. This is not a one-chunk quality assessment for a full interview and does not produce a full transcript automatically.

**Stop after a diagnostic provider failure.** A timeout/connection/account failure is not the expected request-limit stop and is not evidence of a saved chunk. Do not proceed to all sessions or enlarge the allowance until that failure is investigated. Only after confirming a valid returned response/checkpoint and deliberately authorizing further paid processing should you repeat with a larger allowance. Preserve the ASR model/hints/chunk setting and independent diarization duration to reuse checkpoints. Do not blindly launch every interview after repeated failures. `voice-batch` resumes automatically; **it has no `--resume` option**. Direct `voice-transcribe` pipeline/workflow recovery uses `--resume`.

## Safe status and configuration

Every new run records allowlisted effective model enums, chunk durations, provider timeout/retries, admission limits, failure threshold, interview-enabled flag, hint-presence booleans and language-hint count. It records no supplied names, mappings, hint content, source/output paths, plan IDs, keys, endpoints, request IDs or provider payloads. Per-call events show admitted operation count and effective I/O timeout. Full responses belong only in private checkpoint files.

`voice-batch status --batch ...` presents chronological history with the original `historical_run` and `started_at`, then the latest **recorded** stages per item, family and phase, with `latest_run`. Original completion remains visible when attribution fails, and original-only review does not replace attributed history. Latest stage sets come from one run rather than combining different settings/generations. The envelope `run` is the status command's identity; the explicit historical/latest fields identify the earlier execution. Legacy summaries use their filesystem modification time when no timestamp exists and do not acquire inferred family success. Status does not read transcripts/media or freshly verify artifact checksums; use the existing verification/gates before processing.

Offline regressions cover later-chunk failures, restart reuse, corrupt/rebound checkpoints, old completed cache reuse, the synthetic 43/47 recovery case, independent chunk scopes, SDK mock-transport timeout/retry behavior, cross-stage limits, failure streaks, signals and mixed-family chronology. They validate software contracts, not live provider accuracy, latency or cost.

## Diagnose the failure before another paid run

New SDK timeout events include a fixed `timeout_phase`: `connect` (connection establishment), `write` (sending request data), `read` (waiting for response data), `pool` (waiting for a local connection slot), or `unknown`. Classification follows recognized transport exception types, never message text. Unknown or ambiguous evidence stays unknown; a `read` result does not establish the provider/network root cause. The SDK may already have retried before returning its final exception when retries are enabled. Old logs cannot be reclassified from elapsed time alone.

Review is not a remedy for incomplete raw. Batch review/chapters check each family separately, record unavailable prerequisites as `blocked`, and continue only the eligible families. For example, complete original raw with missing attribution can produce complete original review and blocked attributed review in the same run. The command returns nonzero and preserves both facts. No audio requests are made by a text phase to fill missing prerequisites. To request only original review, omit interview mode and the speaker configuration and select known-ready originals.

Chapter gates also remain independent: a missing review or high findings in one family blocks that family’s chapters without approving it from another family’s report. The other family can proceed only with its own exact reviewed bundle, explicit selection and actual human approval. Blocked gates have fixed `blocked_reason` guidance; they do not consume request allowance or count as provider failures. Saved summaries retain `family_blockers`, stage states and each family’s `phase_result`; status reports this recorded evidence without reading media/transcripts.

## Validated text resumption

Every successful text request is saved under the existing private job's `text-chunks` before requesting the next chunk. A failed/incomplete response is never promoted. On restart, all saved requests for that stage are preflighted before new requests, then revalidated on reuse and before stage publication. Completed old bundles continue to reuse their existing integrity checks; successful text held only in memory by an older version cannot be recovered.

Text identities bind the full source hash, stage and validator contract, settings fingerprint and exact request hashes. Those requests include model, prompt bytes, JSON schema, reasoning effort, output budget, exact source/context boundaries and chunk index. Narrative arrangement additionally binds the exact review JSON digest, including human dispositions. Each family has its own private job root; attributed speech is scoped by turn. Response hashes and strict local validation reject corruption, duplicate JSON keys, source substitutions, missing files and symlinks. Publication remains atomic. As with audio checkpoints, checksums are change detection, not authentication against someone replacing both manifest and response. Keep the private directory access controls intact.

With `--provider-retries 2`, a malformed JSON/schema response or missing exact review coverage may receive **one** additional text request. It uses the same shared admission checks and increments `provider_requests`; it is not an unlimited retry loop. Each operation can independently have up to two SDK retries, so one recovered text chunk can start up to six HTTP attempts. Eligible local validation failures do not increase the provider breaker streak. Source word/excerpt violations, chapter provenance/wording violations, refusals and incomplete completions are not repeated automatically. `--provider-retries 0` disables local recovery as well as SDK retries. Authentication, quota, access and invalid-request failures retain their immediate shared stop.

Safe events and failed review coverage now separate `validation_schema`, `validation_coverage`, `validation_source`, `validation_diarization` and `completion` from provider/transport errors. No exception prose, excerpts, names or headers enter logs. A speaker response rejected locally is not reported as an unknown provider failure. These categories describe evidence; they do not prove the provider's root cause.

After a final SDK 429 classified as transient rate limiting, the run pauses new starts across workers and endpoints. Numeric Retry-After seconds, milliseconds and HTTP dates are read locally; waits are bounded to 1–120 seconds, with a two-second fallback. Starts are then spaced by at least 0.25 seconds for the remainder of that execution. Waiting workers check cancellation, deadline and breaker stops without consuming requests. Already admitted calls and SDK-internal retries cannot be coordinated or recalled through this wrapper. The final failed operation still counts toward the endpoint/model breaker; quota is not treated as transient rate limiting.

## Full runs: omit diagnostic caps

The caps are opt-in diagnostic controls, not defaults or recommended full-run settings. In particular, `--max-run-seconds 1800` also shortens each new request's I/O timeout to the remaining window; it can leave a late review with less than 600 seconds. The limit is not merely admission-only: it constrains the timeout passed to admitted SDK operations, while still not being a hard wall-clock cancellation guarantee.

For deliberate full processing of an already prepared private batch, start with two interviews, timeout 600 and retries 2. **Omit both** `--max-provider-requests` and `--max-run-seconds`:

```bash
voice-batch run --batch private/batches/demo-001 --phase raw \
  --interview --speaker-config private/config/speakers.json \
  --transcription-model gpt-transcribe --audio-chunk-seconds 300 \
  --parallel-interviews 2 --provider-timeout 600 --provider-retries 2 \
  --provider-failure-limit 2 --send-to-openai

voice-batch run --batch private/batches/demo-001 --phase review \
  --interview --speaker-config private/config/speakers.json \
  --transcription-model gpt-transcribe --audio-chunk-seconds 300 \
  --parallel-interviews 2 --provider-timeout 600 --provider-retries 2 \
  --provider-failure-limit 2 --send-to-openai
```

Repeat matching commands to resume automatically. Keep model, hints, speaker settings and chunk durations consistent across phases; include an experimental speaker duration in both commands if using it. Original and attributed prerequisites remain independent: complete original text can be reviewed while the attributed family stays blocked. Review never purchases missing audio. Invalid checkpoints fail closed; retain them and investigate rather than deleting caches or starting blind bulk retries.

After checking the exact recordings, transcripts and both complete reports, eligible explicitly selected entries can produce chapters:

```bash
voice-batch run --batch private/batches/demo-001 --phase chapters --select entry-a \
  --human-reviewed --chapters both --interview \
  --speaker-config private/config/speakers.json \
  --transcription-model gpt-transcribe --audio-chunk-seconds 300 \
  --parallel-interviews 2 --provider-timeout 600 --provider-retries 2 \
  --provider-failure-limit 2 --send-to-openai
```

Every high finding still blocks batch chapter drafting. Workbook edits are not imported as approval. Do not modify the machine bundle to bypass this gate. No live interview runs were performed to validate accuracy, quality, latency or price; all implementation tests use invented text/media and offline providers. Increase concurrency only after repeatable successful processing and an account-limit review. See the [shorter speaker-chunk experiment](speaker-chunk-experiment.md) for a focused 120/60-second comparison that keeps ASR at 300 seconds.
