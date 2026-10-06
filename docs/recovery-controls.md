# Saving progress and bounding provider starts

A failed long recording can resume from its validated request checkpoints. This applies to original ASR in pipeline/workflow mode and to the separate interview diarization pass. It does not recover successful responses that an older release held only in memory. Legacy audio-only output and editing/review/chapter stages do not gain request checkpoints.

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
| `--provider-retries N` | SDK retries per operation; existing default 2. Request/time limits require explicit zero. |
| `--provider-timeout S` | Per-I/O SDK timeout, default 120 seconds. With a start deadline it is reduced to the remaining admission window for each new operation. |

The request counter measures application SDK starts, not money or tokens. Zero retries prevents the SDK's automatic retry attempts; redirects, custom transports and provider billing behavior are not dollar guarantees. With retries enabled, one counted operation may include several HTTP attempts before the application observes failure. The circuit breaker observes the final operation result, not each SDK retry.

The time option is a **start deadline**, not a hard whole-run timeout. It does not cancel an in-flight call, truncate a valid returned response, or stop local media preparation at the deadline. The SDK timeout bounds individual I/O waits, not total processing time; a progressing upload/response can exceed the admission window. A returned valid response is saved, then the next start is denied. Ctrl+C or SIGTERM interrupts local processing, releases locks and retains completed checkpoints; cancellation cannot establish that remote work stopped or was not billed. No background provider worker is left behind by these controls.

Provider failures stop at the configured threshold; local validation/media failures remain isolated between independent interviews. Later entries are `not_attempted`, the active limited entry is `incomplete`, and signal interruption is `interrupted`. Counts separate selected, processed, incomplete and unattempted entries. These states never satisfy review/chapter completion gates. A run returns nonzero if any selected work remains incomplete or unattempted.

## One-operation diagnostic and deliberate recovery

This example is a command for a future explicitly authorized paid test. It is not executed by tests or installation. An existing staged session with verified original ASR reaches the speaker pass without repeating ASR:

```bash
voice-batch run --batch private/batches/demo-001 --select entry-a \
  --phase raw --interview --speaker-config private/config/speakers.json \
  --model gpt-transcribe --audio-chunk-seconds 300 \
  --diarization-chunk-seconds 60 --provider-timeout 120 --provider-retries 0 \
  --max-provider-requests 1 --max-run-seconds 180
```

The first uncached provider operation consumes the allowance, regardless of stage. If original ASR is incomplete, that operation may be ASR instead of diarization. It may also complete a sufficiently short session or use no allowance when all selected work is cached. For longer sessions a nonzero incomplete result is expected, with the completed chunk checkpoint retained. This is not a one-chunk quality assessment for a full interview and does not produce a full transcript automatically.

After inspecting the safe result and authorizing further paid processing, repeat with an explicitly chosen larger request allowance. Preserve the ASR model/hints/chunk setting and independent diarization duration to reuse checkpoints. Do not blindly launch every interview after repeated failures. `voice-batch` resumes automatically; **it has no `--resume` option**. Direct `voice-transcribe` pipeline/workflow recovery uses `--resume`.

## Safe status and configuration

Every new run records allowlisted effective model enums, chunk durations, provider timeout/retries, admission limits, failure threshold, interview-enabled flag, hint-presence booleans and language-hint count. It records no supplied names, mappings, hint content, source/output paths, plan IDs, keys, endpoints, request IDs or provider payloads. Per-call events show admitted operation count and effective I/O timeout. Full responses belong only in private checkpoint files.

`voice-batch status --batch ...` presents chronological history with the original `historical_run` and `started_at`, then the latest **recorded** stages per item, family and phase, with `latest_run`. Original completion remains visible when attribution fails, and original-only review does not replace attributed history. Latest stage sets come from one run rather than combining different settings/generations. The envelope `run` is the status command's identity; the explicit historical/latest fields identify the earlier execution. Legacy summaries use their filesystem modification time when no timestamp exists and do not acquire inferred family success. Status does not read transcripts/media or freshly verify artifact checksums; use the existing verification/gates before processing.

Offline regressions cover later-chunk failures, restart reuse, corrupt/rebound checkpoints, old completed cache reuse, the synthetic 43/47 recovery case, independent chunk scopes, SDK mock-transport timeout/retry behavior, cross-stage limits, failure streaks, signals and mixed-family chronology. They validate software contracts, not live provider accuracy, latency or cost.
