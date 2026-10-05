# Portable batch and progress validation

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

The new tests cover metadata-only inventory, original plan order and exclusions, strict schemas/duplicate keys/source identities, explicit copy/provider/chapter gates, offline hydration opt-in, original source changes, independent serial failures, exclusive staging and locks, snapshot/manifest/media tampering, symlinks, automatic source/settings-bound resume, completed-review chapter success, changed-ASR gates and tampered review/raw/provenance protection. Tests also check chunk starts/completions, counters, verified reuse, heartbeats and elapsed waits, concurrent event ordering, arbitrary-value redaction, private file modes, closed-console durability, local log failure, timeout/retry bounds, safe SDK HTTP/category classification, systemic early review termination with explicit unattempted coverage, transient failure continuation, and interruption cleanup with retained summaries.

`tests/test_installation.py` builds wheel/source archives offline, installs the wheel into a fresh environment without editable project imports, invokes both console scripts from a separate directory, and runs synthetic raw→polish/review→chapters→resume through the installed coordinator and packaged prompts. Dependencies are taken from the locked test environment. The new module namespace is asserted to come from the fresh wheel environment. `tests/test_documentation.py` checks local Markdown links and documented CLI flags/examples against declared arguments.

The GitHub Actions workflow runs the complete notebook-extra suite, lock/lint/build/help checks on Linux and macOS. Exact commit CI results must be checked on the draft PR before review. Python 3.14 is the project floor; Windows console/script paths are accommodated in smoke tests and guides, but a Windows CI runner is not included in this change. Owner-only POSIX modes do not establish Windows ACL guarantees.

## Limits

No actual ASR accuracy, provider response latency, recording verification or publication suitability is established by synthetic tests. SDK request timeout/retry settings preserve existing defaults; they are not a whole-run time limit. Heartbeats establish coordinator liveness only. Successful API chunks in a failed stage are not durably checkpointed, and resume can repeat charges. SIGTERM/keyboard interruption cleans up owned locks; abrupt termination may require manual stale-lock inspection. Logs are sanitized local files, not encrypted storage. Source archives and generated test/build outputs are retained privately and are not published with the PR.

## Validation results for this change

- Full notebook-extra suite: **541 passed**.
- Separate default dependency environment: **540 passed, 1 expected optional-notebook skip**.
- Ruff, whitespace diff check, offline lock consistency, installed dependency compatibility, both installed help commands, wheel/source builds, installed-wheel synthetic workflow/resume, and Markdown link/CLI example checks passed.
- Outgoing source and generated archive-content scans found no private setup paths, private source references or credential patterns; notebook outputs remain clear. New commit metadata uses a generic contributor identity. Generated distributions and detailed local logs remain ignored and are not published.

GitHub Actions results belong to the exact pushed commit and are reported separately in the draft PR handoff. No real media, paid provider requests, merge, or deployment occurred.
