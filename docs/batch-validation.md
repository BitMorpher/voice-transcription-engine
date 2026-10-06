# Portable batch and progress validation

For current option names, every public parameter, valid combinations and parallel-interview examples, see the [command and parameter guide](cli-reference.md).

The implementation builds on the ordered-interview contract and adds `voice-batch`, installed package imports, safe execution logs and per-chunk/elapsed progress. The original ordered source fidelity and raw/review/chapter contracts remain enforced. See [runnable batch examples](batch-orchestration.md), [ordered parts](ordered-interviews.md), and [single-file author stages](author-workflow.md).

## Reproduce without personal media or paid requests

```bash
uv sync --locked
uv lock --check
uv run --locked pytest -q
uv run --locked --extra notebook pytest -q
uv run --locked ruff check .
uv build
uv run --locked voice-transcribe --help
uv run --locked voice-batch --help
```

`tests/conftest.py` blocks Python network connections and removes the API key from test processes. Media is generated synthetically in temporary directories. Provider responses are mocks. The installed-wheel smoke test additionally blocks network inside its synthetic workflow process. Package and environment setup can download locked dependencies; no test is authorized to use recordings or hosted providers.

## Regression evidence

The new tests cover metadata-only inventory, original plan order and exclusions, strict schemas/duplicate keys/source identities, explicit copy/provider/chapter gates, offline hydration opt-in, original source changes, independent failures with serial/default or explicit parallel processing, exclusive staging and locks, snapshot/manifest/media tampering, symlinks, automatic source/settings-bound resume, completed-review chapter success, changed-ASR gates and tampered review/raw/provenance protection. Tests also check chunk starts/completions, counters, verified reuse, heartbeats and elapsed waits, concurrent event ordering, arbitrary-value redaction, private file modes, closed-console durability, local log failure, timeout/retry bounds, safe SDK HTTP/category classification, systemic early review termination with explicit unattempted coverage, transient failure continuation, and interruption cleanup with retained summaries.

`tests/test_installation.py` builds wheel/source archives offline, installs the wheel into a fresh environment without editable project imports, invokes both console scripts from a separate directory, and runs synthetic raw→polish/review→chapters→resume through the installed coordinator and packaged prompts. Dependencies are taken from the locked test environment. The new module namespace is asserted to come from the fresh wheel environment. `tests/test_documentation.py` checks local Markdown links and documented CLI flags/examples against declared arguments.

The GitHub Actions workflow runs the complete notebook-extra suite, lock/lint/build/help checks on Linux and macOS. Exact commit CI results must be checked on the draft PR before review. Python 3.14 is the project floor; Windows console/script paths are accommodated in smoke tests and guides, but a Windows CI runner is not included in this change. Owner-only POSIX modes do not establish Windows ACL guarantees.

## Limits

No actual ASR accuracy, provider response latency, recording verification or publication suitability is established by synthetic tests. SDK request timeout/retry settings preserve existing defaults; they are not a whole-run time limit. Heartbeats establish coordinator liveness only. Validated original ASR and interview speaker-pass chunks are checkpointed; text-stage requests are not, so failed text stages can repeat charges. SIGTERM/keyboard interruption cleans up owned locks; abrupt termination may require manual stale-lock inspection. Logs are sanitized local files, not encrypted storage. Source archives and generated test/build outputs are retained privately and are not published with the PR.

## Historical portable-batch validation results

- Full notebook-extra suite: **555 passed**.
- Separate default dependency environment: **554 passed, 1 expected optional-notebook skip**.
- Ruff, whitespace diff check, offline lock consistency, installed dependency compatibility, both installed help commands, wheel/source builds, installed-wheel synthetic workflow/resume, and Markdown link/CLI example checks passed.
- Outgoing source and generated archive-content scans found no private setup paths, private source references or credential patterns; notebook outputs remain clear. New commit metadata uses a generic contributor identity. Generated distributions and detailed local logs remain ignored and are not published.

GitHub Actions results belong to the exact pushed commit and are reported separately in the draft PR handoff. No real media, paid provider requests, merge, or deployment occurred.


## Copilot review follow-up

All three findings were applicable and addressed:

- Chapter generations reuse the exact review JSON/XLSX bundle accepted by the human gate, revalidated under the interview lock. Tests forbid fresh review calls, compare both bundle files byte for byte, verify chapter review hashes, and reject approval changes/conflicting target reviews without provider requests. The installed-wheel smoke also asserts review reuse.
- Chapter response property/count/refusal/truncation failures now emit terminal `completion` events, and schema/coverage failures emit `validation` events. Synthetic events retain the chunk index and omit payload/error text.
- `Transcriber` accepts exact numeric timeout types and rejects `True`/`False`; both values have regressions.

CLI syntax and stacked base history remain unchanged. No review replies or thread resolutions are posted by these implementation fixes.

## Parallel interview validation

The current batch CLI adds explicit `run --parallel-interviews N`, default 1. Parts within each interview retain their order; worker changes do not change cache identities. `--transcription-model` and `--speaker-model` are the preferred model flags, with published `--model` and `--interview-model` aliases preserved. The [parameter guide](cli-reference.md) documents every public flag, combinations, defaults, costs and examples. Earlier validation counts above describe the earlier feature, not the current suite.

Current local evidence on CPython 3.14.8:

- Full locked notebook-extra environment: **738 passed**.
- Separate locked default-dependency environment: **737 passed, 1 expected notebook skip**.
- Ruff, whitespace, offline lock consistency, installed dependency compatibility, both installed help commands, wheel/source builds, Markdown links/examples and complete public-option coverage passed.
- Source and archive-content privacy scans found no personal paths, real credential patterns, media or private output artifacts. Notebook outputs remain empty. Distribution files remain ignored and private.

Twenty-one session regressions use barriers/events and invented PCM, with no paid providers. They prove actual overlap and a ceiling of two simultaneous fake operations, serial part/family request order, isolated outputs, byte-identical completed resume, one shared request allowance, endpoint/model failure stopping, completion after a deadline without reopening admission, local tampering isolation, approved review-byte reuse, safe per-item logging/heartbeats, log-failure stopping, runtime/parser validation and recorded mixed-family status. Main-thread SIGTERM and repeated signals retain already returned checkpoints, deny later starts, preserve unattempted selection rows, and keep the batch lock until workers finish. Serial preparation interruption retains its exit/status behavior.

An additional installed-wheel regression runs parallel interviews with a shared allowance, recovery and status from outside the checkout, with sockets blocked. The public-parameter coverage test prevents new flags from silently losing reference documentation or batch help text. Existing fidelity, speaker mapping, symlink/cache checks and author gates remain covered by the full suite.

These checks establish software behavior on synthetic inputs. They do not identify the cause of a live network/provider timeout, prove a model is faster/cheaper, measure live accuracy, establish a requests-per-minute limit or promise a dollar cap. Already admitted I/O and local preparation may prolong cancellation cleanup. No real interview was read, hydrated, uploaded or processed, and no existing runtime, batch or cache was changed.
