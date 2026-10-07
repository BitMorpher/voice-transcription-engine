# Compare shorter speaker requests without changing ASR

This is a guide for a future deliberately authorized paid experiment. Implementation tests use synthetic/offline providers only. A smaller speaker chunk might reduce request latency, but quality and total cost must be measured on the actual recording. No quality gain is claimed.

Use one staged interview with verified complete original ASR. Retain the existing batch, media, caches and original settings. Keep `--audio-chunk-seconds 300`, the original model and any original hints unchanged. Choose an interview containing representative overlapping voices, long monologues, brief turns and difficult boundaries. Keep private notes outside the repository.

1. Record the existing 300-second speaker generation's outcome, request durations, failure categories, request count and recording-check notes. Do not repeat a failed bulk run to collect a baseline.
2. Try 120 seconds on that one interview with one worker. Remove old speaker mappings from the private configuration for this experiment; names alone cannot identify voices. An explicit duration creates a new attributed identity while preserving original ASR and earlier attributed generations.
3. Only after that succeeds and further paid work is authorized, compare 60 seconds. Each duration starts a different attributed cache and requires its own mapping confirmation. Increasing boundaries adds requests and may fragment speech or reset labels more often.
4. Listen around every new request boundary, compare source words and turn coverage, inspect unknown/overlapping labels, and record provider versus local validation errors. Record timeout/read-phase evidence without inferring its root cause. Stop if requests fail or fidelity degrades; preserve checkpoints for investigation.
5. Confirm roles only after listening. Map exact `PART:REQUEST:LABEL` scopes and include `--confirm-speaker-mappings` when mappings accompany the explicit duration. The same label in another request is not evidence of the same voice.

A full one-interview 120-second speaker experiment uses no diagnostic caps:

```bash
voice-batch run --batch-folder private/batches/demo-001 --select entry-a --step raw \
  --separate-speakers --speaker-config private/config/speakers-unmapped.json \
  --transcription-model gpt-transcribe --audio-chunk-seconds 300 \
  --speaker-chunk-seconds 120 --parallel-interviews 1 \
  --request-timeout 600 --request-retries 2 --failure-limit 2 \
  --send-to-openai
```

Use `--speaker-chunk-seconds 60` for the separately authorized comparison. These commands process a complete speaker pass, not one request. If original ASR is incomplete or its settings/path changed, original transcription can also run. For a bounded one-operation diagnostic instead, follow [recovery controls](recovery-controls.md#one-operation-diagnostic-and-deliberate-recovery), with retries zero and explicit caps. A diagnostic's incomplete exit after saving a valid chunk is different from provider failure.

Compare latency distribution, failed requests, total requests and any available private billing evidence. Do not equate request count with cost: model pricing, audio duration, retry attempts and duplicate remote work can all affect charges. Shorter audio chunks **do not change text-review chunks**; review still uses its independent UTF-8 core/context boundaries. Once a duration works consistently, retain it across raw/review/chapter phases and return to two-interview concurrency before considering any increase. Complete review and the existing chapter human gates remain necessary for each attributed generation.
