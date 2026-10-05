# Local implementation validation

The feature was implemented on isolated branch `feature/author-review-chapters`, based on `main` at `27fc9a9284ce19937b6dd4d9bfd19f4939e89a03`. GitHub PR #8 was confirmed merged on 2026-10-04 at 16:01:18 UTC. The earlier checkout was preserved. No merge, real recording processing, paid model request, or private Drive upload was performed for this feature.

## Delivered behavior

`voice-transcribe --workflow` handles one local audio/video path or a nonrecursive folder, with explicit or automatic media type. Select raw, polish, review, and chapter stages. Review is required before chapters, and unresolved high-priority findings block drafts unless `--draft-with-unresolved-high` is supplied. Reports are JSON and XLSX, with exact raw source spans, stable IDs, neutral questions, and reviewer fields. Interview excerpts and conservative narrative arrangements carry provenance, flags, omissions, and human-review labels.

See [the workflow guide](author-workflow.md) for all options, examples, rubric, provenance, privacy, and retry details.

## Checks performed

- CPython 3.14.8, uv 0.12.23; all dependencies installed from the committed lock. Only OpenPyXL 3.1.5 and its dependency et-xmlfile 2.0.0 were added to runtime dependencies.
- Complete offline suite with the locked notebook extra: **379 passed**.
- Separate clean locked default environment: **378 passed, 1 expected optional-notebook skip**.
- Ruff, whitespace diff check, lock check, installed dependency compatibility, wheel and source-distribution builds passed.
- The installed wheel was tested outside the source checkout with a synthetic WAV and mocked providers. Raw/polish/report generation, high-priority blocking, explicit draft override, both chapter styles, resume, literal Excel source cells, exact quote offsets, and packaged prompt/module content were verified.
- Review/chapter tests exercise structured schemas with the locked SDK through local mock transport. Tests block outbound socket connections and clear API keys.
- Regressions cover complete empty versus failed/incomplete reports, long-source coverage, boundary duplicates, invalid/invented references, refusals/truncation, untrusted transcript instructions, immutable raw binding, settings/model/prompt changes, concurrent source/report changes, invalid cached metadata, mixed bundles, private provider/parser/storage errors, and XLSX Unicode/control-character roundtrips and formula injection.
- Public source/test/docs and distribution contents were scanned for credential, email, machine-path and private-artifact patterns. The scan found no patterns or private files in distribution members; all 744 locked archive URLs use public PyPI hosts. Notebook output/execution state is clear. These checks are scoped scans, not a proof that code is free of every vulnerability.
- Native visual QA in Numbers imported and inspected all four XLSX sheets: overview status/counts, wrapped finding excerpts and colored priorities, source offsets and literal leading-equals text, coverage, unreviewed disposition controls and blank reviewer fields. The original XLSX checksums remained unchanged. Native Microsoft Excel rendering was not available; Numbers import and XML/roundtrip tests do not prove every Excel-version behavior.
- A pip-audit check of the complete pinned runtime dependency export reported **no known vulnerabilities**. Development/notebook dependencies were not included in this advisory check.

## Limits of this validation

Model responses are synthetic and mocked. This validates implemented contracts, not ASR accuracy, issue-detection quality, factual judgment, or suitability of actual testimony for publication. Coverage confirms validated model acknowledgement of every source core, not exhaustive issue detection. Raw ASR still requires a recording check. Narrative output preserves source wording and arranges testimony; third-person mode frames exact testimony rather than freely rewriting a story. Workbook decisions are a human record and are not imported as approval by the CLI.

Runtime environments, synthetic examples, detailed test/build/audit logs and distribution archives remain ignored under `private/` or `dist/`. Source archives retain ordinary local tar ownership metadata and should be inspected before any later sharing.


## Subsequent portable orchestration

This document records the historical feature validation above. Current installed commands, serial selection/staging, safe durable progress logs and transport controls are documented in [batch orchestration](batch-orchestration.md) and the [current validation guide](batch-validation.md). Reproduce the current checks from the checkout:

```bash
uv lock --check
uv run --locked pytest -q
uv run --locked --extra notebook pytest -q
uv run --locked ruff check .
uv build
uv run --locked voice-transcribe --help
uv run --locked voice-batch --help
```
