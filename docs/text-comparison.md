# Compare text settings before a full run

`interview compare-text` exercises text polish and author review in isolated private directories. It reads no audio, creates no chapters, and never reads a batch folder. The default command uses a local synthetic provider with no API key or network. Sol 6.1 low polish and Sol 6.1 medium review are candidates for a lower-cost profile; these checks do not establish their real quality, speed, or cost.

Run this first:

```bash
interview compare-text --output-folder private/comparison-offline
```

The default cases are Sol 6.1 low original polish, Sol 6.1 medium review, Astra high review, and Sol 6.1 low grouped attributed polish. The fixture has 128 short speaker turns, including 25 empty or whitespace-only turns, Unicode combining marks, joining controls, symbols, uncertain attribution, criticism, allegations, and sensitive disclosure. The local provider echoes faithful editing JSON and supplies predefined exact review excerpts. Production validators check these responses.

At the default 6000-byte bound, grouped polish uses **4 measured SDK operations instead of 103 nonempty per-turn operations**, a **96.12% reduction** on this fixture. All 128 turns, their speaker headers, empty speech, and source text remain exact. This measures request consolidation on synthetic text, not an actual billed-call count or a forecast of savings on real interviews. The ordinary polish and each review case use two operations on the 8267-byte source.

Repeat a completed or interrupted comparison with the same command and `--resume`:

```bash
interview compare-text --output-folder private/comparison-offline --resume
```

Every existing case, artifact, and saved request response is checked before a live client can start. A completed artifact must match validated reconstruction from its checkpoints. Editing a transcript, checkpoint, review report, or completion manifest stops reuse. Exact CRLF and UTF-8 bytes are retained. Missing checkpoints in an unfinished case can be requested on resume; changed checkpoints fail closed. A concurrent process using the same output folder is rejected by an exclusive lock. Interruption retains successful checkpoints and releases the lock. A stale lock must be investigated locally before removal.

Each case has its own directory beneath `synthetic/` or `openai/`, then stage, model, reasoning effort, source SHA-256, and settings fingerprint. Synthetic responses cannot become live-model cache hits. Exact source snapshots, checkpoint responses, completed derivatives/reports, and immutable attempt metrics stay in that private tree. Each invocation also saves a new `comparison-*.json` summary. Existing cases need explicit resume; source/model/effort changes select separate directories and preserve earlier artifacts. In a repository, outputs must be under ignored `private/` or `data/`; external private storage is also accepted. Input and output symlinks or parent traversal are rejected. No source filename, transcript excerpt, speaker name, key, or provider error body is printed in summary metrics.

## A separately authorized small live comparison

Select a short, representative UTF-8 transcript sample, at most 64 KiB. Use identical unedited source bytes for both review settings. Include context needed to judge ambiguity and attribution; avoid comparing reviews of differently polished text. Confirm the exact sample, cases, and API spending scope before executing the paid command. Merely installing this PR or choosing the balanced profile does not send this comparison.

This explicit command makes only the selected review comparison:

```bash
interview compare-text --text-file private/samples/review-sample.txt \
  --output-folder private/comparison-live \
  --case review:gpt-6.1-sol:medium \
  --case review:gpt-6-astra:high \
  --request-timeout 120 --request-retries 0 \
  --send-to-openai
```

To include faithful original polish, add `--case polish:gpt-6.1-sol:low`; add `--case polish:gpt-6-astra:high` for an editing baseline. Repeated `--case` flags replace the default case list. Without them, a supplied text file selects the three original polish/review cases and omits attributed polish. `attributed-polish` is available only for the built-in synthetic fixture: paragraphs in a text file are not inferred speaker turns.

`--send-to-openai` requires `--text-file` and the configured `OPENAI_API_KEY`. Without that opt-in, even a supplied file uses the local synthetic provider, which is useful for integration and fidelity checks but cannot assess real review judgment. API request settings use `store=false`; this is not a zero-retention promise. Preserve private sample and artifact access controls.

The command accepts `low`, `medium`, and `high` effort for the supported models, `gpt-6.1-sol` and `gpt-6-astra`. `--chunk-bytes` changes the 64–6000-byte source/core bound. Completion ceilings leave room for faithful schema output and reasoning; they are ceilings, not token consumption estimates. Truncation, refusal, invalid schema, wrong turn order, changed words or symbols, incomplete core acknowledgement, and non-exact Unicode quotes fail validation. Lower-priced settings do not fix invalid source offsets and never bypass those checks.

## Read the measurements correctly

For every case the JSON summary includes wall latency, time spent inside SDK operations, operation starts/completions/failures, cache hits, validation recovery operations, and actual SDK-reported token usage. `usage_missing` and per-field reported-operation counters distinguish absent usage from real zero consumption. Synthetic responses deliberately have no token usage. Reasoning tokens are a subset of completion tokens; cached input tokens are a subset of prompt tokens. Do not add either subset again when estimating spend.

`--request-retries 0` is the comparison default, so one admission means one application SDK operation. SDK retries are configured separately and their internal HTTP attempts are not directly observed. An allowed schema/coverage recovery with retries enabled can add one extra operation; word/quote fidelity failures are not retried. Operation counts are not invoices, server-work guarantees, or exact network-attempt counts. Failed requests may lack usage even when provider work occurred. Use current account usage and the provider's [model pricing](https://developers.openai.com/api/docs/pricing) when estimating cost from observed tokens.

`--request-timeout` bounds individual SDK waits. `--failure-limit` stops future admissions after repeated failures for a model, and definite account/model/quota failures stop immediately. These are not monetary caps or a full-run wall-clock guarantee. For a deliberately limited diagnostic, add `--max-requests N` with `--request-retries 0`; the allowance is shared across all selected cases, and validated cache hits consume zero. The command performs cases sequentially. A cap can leave review incomplete, which must remain visibly incomplete. No total request cap is set by default.

`--output-dir`, `--provider-retries`, `--provider-failure-limit`, and `--max-provider-requests` are accepted aliases for the corresponding comparison options. Use `interview compare-text --help` for the exact combinations.

## Human quality rubric

Reviewers should read the same raw sample and the private `author_review.json` for each case, ideally hiding the model label until scoring. Record examples and missed passages beside these dimensions:

| Dimension | What to assess |
| --- | --- |
| Faithful polish | Every original word, repetition, number, name, Sanskrit spelling, mark, and symbol remains in order; punctuation and layout improve readability without changing meaning. |
| Issue detection | Meaningful criticism, serious allegations, sensitive disclosures, uncertain responsibility, and disputed facts are flagged when context supports them. Count both misses and unnecessary flags. |
| Nuance and context | Personal perspective remains personal perspective; reported speech, hedging, irony, uncertainty, and neighboring context are interpreted fairly. A flag is not proof. |
| Attribution | The model retains uncertainty and does not turn scoped speaker labels or a mentioned person into a verified identity. |
| Evidence | Every quoted span and Unicode offset is exact, and each core is acknowledged. Automated validity alone does not prove that all issues were found. |
| Actionability | The local reason/action/question fits the passage and helps a human make a decision without unsupported certainty. |
| Efficiency | Compare uncached latency and actual reported prompt/completion tokens, retries, failures, and cache hits alongside the quality scores. Explain missing usage. |

Do not choose a winner solely by finding count, a clean schema, lower output tokens, or this synthetic fixture. Compare multiple representative samples before another full batch. Adopt Sol medium review only if its quality is acceptable for that material; retain Astra where its additional judgment demonstrably helps. Any unresolved high finding still needs the existing human review and chapter gates. The comparison command grants no publication approval and performs no drafting.

## Apply the selected settings

The direct and batch entrypoints keep their legacy Astra/high defaults. Opt in with `--text-profile balanced`, or choose each field explicitly: `--editing-model gpt-6.1-sol --editing-reasoning-effort low --review-model gpt-6.1-sol --review-reasoning-effort medium`. Explicit fields override preset fields regardless of flag order. `--review-model` and `--review-reasoning-effort` also configure narrative chapter arrangement; deterministic interview excerpts need no model. These flags configure text stages and do not replace provider approval or chapter gates.

Source-bound text versions retain verified raw/audio outputs and earlier text generations. Changing review settings reuses matching polish; changing polish settings selects a new polish derivative without retranscribing audio. Read [migration](migration.md), [author workflow](author-workflow.md), and [recovery controls](recovery-controls.md) before continuing an existing output tree.

The comparison command also accepts `--validation-failure-limit N` (default 3), using the same terminal stage/model counts and stop/drain semantics as direct/batch processing. Review v2 uses exact piece references and locally calculated offsets; the synthetic fixture is versioned separately so old v1 synthetic responses cannot be reused as v2 results. See [review recovery controls](recovery-controls.md#review-evidence-contract-and-validation-stop).
