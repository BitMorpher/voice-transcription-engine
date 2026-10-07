# Command and option reference

For a first run, follow [getting started](getting-started.md). For an explanation of the steps, outputs, and terminology, read [how the pipeline works](pipeline-guide.md). This reference answers “which option do I need?” and retains the older spellings used by existing scripts.

Start with [choosing an operation](#choose-an-operation), then use the [batch options](#batch-specific-parameters), [shared processing options](#parameters-shared-by-processing-commands), or [direct transcription options](#direct-transcription-parameters). [Older option spellings](#older-option-spellings) map to the same settings.

Run `interview --help` for the command overview. Existing `voice-transcribe` and `voice-batch` entrypoints retain the same options; see [migration](migration.md).

Use `interview transcribe` for one recording, a folder of independent recordings, or one interview whose parts have a declared order. Use `interview batch` for a private plan containing several independent interviews, including interviews with multiple parts. A **session** here means one batch entry and its complete ordered interview. Parallel workers never turn the parts of one interview into separate sessions.

The [setup guide](setup.md) explains Python, uv, FFmpeg/ffprobe, dependencies and credentials. Keep recordings, recordings lists, batch plans, hints and outputs in private storage. Commands that transcribe or edit send audio/text to the hosted provider and can incur charges. Audio preparation, inventory, check, batch preparation, verify and status do not call OpenAI. Batch prepare reads and copies media; verify reads staged media to check hashes. Inventory/check/status do not read media content.

ASR in existing manifests means automatic speech recognition: the original speech-to-text pass. Diarization means separating voices within a request. It does not establish who those people are. Choosing another supported model or reasoning effort can change output, latency and provider usage; this repository has no real-transcript benchmark proving the best quality/cost balance. Check your account's current model access/pricing/rate limits before deliberate paid work. Use [the isolated text comparison](text-comparison.md) to assess the candidate without processing audio again.

All examples use invented filenames and IDs. Replace them with your own private inputs. Run from the checkout with `uv run --locked`; installed commands also work elsewhere. Both commands support `-h` and `--help`, which print usage without processing files. Flags cannot be abbreviated. Quote paths containing spaces. An input is a local path, never a media URL.

## Choose an operation

| Command or mode | Purpose and prerequisites |
| --- | --- |
| `interview transcribe --input-folder ...` | Compatibility audio-folder mode: independent WAV/MP3/M4A files, scanned nonrecursively, with flat output filenames. No stage-based resume. |
| `interview transcribe --pipeline --input ...` | Normalize audio/video and retain original transcription in a private job directory. Also accepts a folder through `--input` or `--input-folder`. |
| `interview transcribe --prepare-audio --input ...` | Normalize audio/video locally without a key or provider request. Pipeline-style outputs and resume are available. |
| `interview transcribe --author-workflow --input ...` | Raw transcription, optional polish, author review and optional chapter drafts for each independent file. Default stages: raw, polish, review. |
| `interview transcribe --author-workflow --recordings-list ...` | Process one interview's ordered parts, then combine exact raw text and run interview-wide stages. |
| `interview batch inventory --batch-plan ...` | Read plan/manifest JSON and source metadata; show selected readiness. |
| `interview batch check --batch-plan ...` | Inventory checks plus FFmpeg/ffprobe availability. This does not decode media or prove codec validity. |
| `interview batch prepare --batch-plan ... --batch-folder ... --copy-local-files` | Create fresh private staging and an immutable plan snapshot; copy/hash sources. Never reuse an existing batch directory for prepare. |
| `interview batch verify --batch-folder ...` | Verify the saved snapshot, staging ledgers and copies. Does not need the original source files. |
| `interview batch run --batch-folder ... --send-to-openai` | Process existing staged interviews. Default phase: raw. Resume is automatic. |
| `interview batch status --batch-folder ...` | Read retained summaries, show chronological executions and latest recorded stages by item/family/phase. Does not freshly verify output contents. |
| `interview compare-text ...` | Compare text settings in separate private output/checkpoint directories. Default offline mode uses synthetic fixtures and no network. See [the comparison guide](text-comparison.md) before explicitly allowing paid text calls. |

The workflow guides provide the [ordered manifest](ordered-interviews.md), [batch plan and staging](batch-orchestration.md), [review rubric](author-workflow.md) and [speaker configuration](batch-media-attribution.md) schemas.

## Batch-specific parameters

| Parameter | Default, purpose and valid use |
| --- | --- |
| `action` | Required positional word: `inventory`, `check`, `prepare`, `verify`, `run` or `status`, as above. |
| `--batch-plan PATH` | Required for inventory/check/prepare. Private version-1 JSON; manifest paths are relative to this file. Forbidden on verify/run/status, which use the saved batch snapshot. |
| `--batch-folder PATH` | Fresh destination required for prepare; existing staged directory required for verify/run/status. Those existing operations forbid `--batch-plan`. Inventory/check use the plan instead. |
| `--select ID` | Repeat for exact entry IDs. Without selection, plan operations use all entries and staged operations use the saved prepared selection. IDs are private; logs use original one-based plan positions. Repeating selections never reorders the plan. Chapter runs require explicit selections. |
| `--exclude ID` | Repeat to remove exact IDs after selection. Unknown IDs or an empty final selection fail. You cannot run an entry that was never staged. |
| `--copy-local-files` | Explicit prepare approval to read and copy sources. Required with a fresh batch destination. No source is modified. |
| `--download-cloud-files` | Prepare only: permit opening macOS cloud placeholders so their contents can be downloaded locally. Without it, detected offline sources are blocked. Other remote filesystems may download on open; check your storage behavior. |
| `--send-to-openai` | Required for run: approve provider processing for the chosen phase and families. Does not set a monetary cap. |
| `--step raw\|review\|chapters` | Run only; default `raw`. Raw processes original transcription and opt-in attribution. Review checks each requested family separately, then performs polish and review for ready families. Chapters require each family’s intact complete review without high findings plus explicit IDs and human approval. An unavailable family is blocked while another ready family can continue. Text phases never buy missing audio or review to pass a gate. |
| `--parallel-interviews N` | Run only; positive integer, default `1`. At most N independent interviews overlap. `1` is also tolerated on other actions for compatibility, with no effect. |
| `--human-reviewed` | Chapters phase: confirm you checked every requested family's recordings/raw/report. Required with explicit selections. Editing an Excel file is not imported approval; retain machine-generated bundle bytes and keep notes in copies. There is no batch high-finding bypass. |
| `--separate-speakers` | Run only; add a separate attributed family as well as the original family. Adds a speaker audio pass and selected downstream stages. Missing display names keep scoped unidentified speakers. |
| `--speaker-config PATH` | Optional private version-1 per-entry configuration; requires run with interview enabled. Sets names, confirmed scoped mappings or an entry's `enabled: false`. Other enabled entries retain unidentified speakers. See the [configuration example](batch-media-attribution.md#per-entry-attribution-with-unknown-identities-by-default). |
| `--chapter-style interview\|narrative\|both` | Chapters phase; default `both`. Interview drafts use deterministic source excerpts; narrative arrangement can add provider calls. Applies to each requested family. |
| `--narrative-person first\|third` | Chapters phase; default `first`. Controls conservative testimony framing, not permission to invent facts or a new story. |

Options for copying/hydration, phases, chapters and human approval are meaningful only on their listed operations. Some older arguments are accepted and ignored elsewhere; avoid relying on an ignored flag to approve or configure another operation. Nondefault parallel-interview counts, provider admission controls and speaker flags explicitly reject incompatible actions.

## Parameters shared by processing commands

For `interview batch`, audio/model/hint/provider options below apply to `run`; `--media-timeout` also applies to prepare. Logging options apply to any batch action. For `interview transcribe`, supply transcription/provider options when actually transcribing; local extraction has no provider requests.

| Parameter | Default, purpose, values and interactions |
| --- | --- |
| `--transcription-model MODEL` | Original speech-to-text model; default `gpt-transcribe`. This CLI also accepts `whisper-1`, `gpt-4o-transcribe`, `gpt-4o-mini-transcribe` for compatibility. It does not silently fall back if a model is inaccessible. `--model` is the existing compatibility alias. Preserve model/hints/chunk settings for raw cache reuse. |
| `--editing-model MODEL` | Separate faithful punctuation/capitalization/layout polish; `gpt-6-astra` (default) or `gpt-6.1-sol`. Direct pipeline/legacy mode needs enhancement opt-in; workflow's polish stage and batch review/chapters select it. A model change selects a retained text version while keeping verified raw/audio; old artifacts remain intact. Legacy flat audio-folder outputs still require a fresh destination. |
| `--review-model MODEL` | Review and narrative chapter model; `gpt-6-astra` (default) or `gpt-6.1-sol`. Direct use requires workflow. Also accepted as `--author-model`. Changing it changes review/chapter bindings; approval must match the exact generated review. Raw-only runs make no author request. With batch review/chapters, each family’s prerequisites and results are independent. Interview chapter excerpts are deterministic. |
| `--editing-reasoning-effort low\|medium\|high` | Reasoning effort for punctuation/layout polish, default `high`. It changes the editing fingerprint. This is separate from the review setting and does not change ASR. |
| `--review-reasoning-effort low\|medium\|high` | Reasoning effort for review and narrative chapter arrangement, default `high`. Direct use requires workflow; `--author-reasoning-effort` is an alias. It changes review/chapter fingerprints and their exact approval requirements. |
| `--text-profile legacy\|balanced` | Default `legacy` preserves Astra/high for both text operations. Explicit `balanced` selects Sol 6.1/low polish and Sol 6.1/medium review as a comparison candidate. Explicit model/effort flags override each preset setting, regardless of flag order. A profile selects settings; it does not select stages or authorize provider calls. Equivalent explicit settings use the same cache fingerprints. |
| `--context-file PATH` | Optional local regular UTF-8 text, stripped at its edges, up to 8192 UTF-8 bytes of context. Sent with original ASR requests; applies to every selected recording. Hint-file reads have a 64 KiB safety limit. Paths and contents are not written to execution logs. |
| `--glossary-file PATH` | Optional UTF-8 expected terms, one per line, stripped with blank/duplicate terms removed. Maximum 100 terms, each at most 256 UTF-8 bytes. Supported only for `gpt-transcribe`; other ASR choices reject nonempty glossaries. Terms are hints, not proof of what was spoken. |
| `--language CODE` | Repeat for expected lowercase two/three-letter codes; at most 16 for `gpt-transcribe`. Compatibility ASR models and interview diarization allow at most one two-letter code. Omit for automatic detection. This is a hint, not translation. |
| `--audio-chunk-seconds S` | Duration cap for original audio requests: 1–600 seconds, fractional values allowed, default `300`. The byte cap can split audio sooner. Smaller values create more requests/boundaries and do not guarantee lower total time. Changing this invalidates the original ASR configuration binding. |
| `--speaker-model MODEL` | Additional speaker pass: currently only `gpt-4o-transcribe-diarize`. Default uses that model. Requires interview mode; original ASR still uses its own model option. `--interview-model` is the existing compatibility alias. |
| `--speaker-chunk-seconds S` | Interview only: independent 1–600-second duration cap, fractional values allowed. Omitted uses the original audio chunk duration. An explicit value creates a separate attributed configuration while preserving original ASR reuse. Speaker labels reset per request; shorter chunks need more scoped mappings. |
| `--confirm-speaker-mappings` | Requires explicit diarization duration. Confirm that every supplied speaker mapping was checked by listening against the new request scopes. Without confirmation, supplied mappings with an explicit duration are rejected. This flag neither supplies mappings nor identifies voices. |
| `--media-timeout S` | Positive finite seconds per FFmpeg operation; default `3600`. Not a total interview/batch deadline. FFprobe has its own fixed 30-second bound. |
| `--request-timeout S` | Positive finite seconds per SDK I/O wait; default `120`. Not a full operation/run timeout. With an admission deadline, each new operation uses the smaller of this value and the remaining window. |
| `--request-retries N` | Integer 0–5, default `2`: SDK retries after an operation's initial attempt; greater than zero also permits one extra application operation for eligible text schema/coverage and strict source-correction failures (including chapter wording with valid layout). Both can extend time and charges. Must be `0` with either provider request allowance or admission deadline. |
| `--max-requests N` | Optional positive integer; at most N new SDK operation admissions across all selected sessions and ASR/diarization/polish/review/chapters. Verified cache hits consume zero. Requires retries `0`. Counts operations, not tokens, HTTP redirects or dollars. |
| `--max-run-seconds S` | Optional positive finite elapsed admission window from control initialization, including intervening local work. Requires retries `0`. Stops new provider starts; does not kill current I/O or local conversion at that time. |
| `--failure-limit N` | Positive integer, default `2`. Stop admission after N consecutive failed operations for the same endpoint/model; successes reset that scope. Definite account/model/configuration/quota errors stop immediately. Shared across workers; responses are observed in completion order. Local media/schema/output failures do not themselves spend this streak. |
| `--validation-failure-limit N` | Positive integer, default `3`. Stop all new provider admissions when one text stage/model accumulates N terminal local validation failures across workers in this invocation. Count once after permitted recovery is exhausted, or immediately for non-retryable quote/source/completion failures. Successes never reset this count. Different stage/model counts are separate; cache hits, SDK exceptions and admission-denied recoveries do not count. Active requests drain and preserve valid checkpoints. |
| `--progress auto\|plain\|json` | Default `auto`: an updating status panel on a capable interactive terminal; sanitized JSON lines when redirected or terminal control is unavailable. `plain` prints readable scrolling messages; `json` always prints structured events. Bars count successfully completed or verified reused sections/parts, never a request merely started. Unknown totals show activity without a percentage. Display choice does not change private JSONL execution logs. |
| `--plain` | Alias for `--progress plain`; readable scrolling messages without colors or terminal controls. Mutually exclusive with an explicit `--progress` option. |
| `--quiet` | Suppress console progress, including reported processing failures; retain private execution logs and exit status. Argument usage errors still appear. Batch interviews inherit this setting. |
| `--no-color` | Disable progress colors; also honors the presence of `NO_COLOR`, including an empty value. Live updates still work. Plain/JSON output has no colors or terminal controls. |
| `--status-interval S` | Positive finite idle interval, default `30`. Reports that the coordinator is alive while work is pending. Does not reduce timeouts or prove provider progress. Active batch sessions get their own item/stage heartbeat, including the current active session count. |
| `--logs-folder PATH` | Private execution log destination. Default: output/execution-logs for direct commands, batch/execution-logs for prepare/verify/run. Other batch actions are console-only unless this option is supplied. Uses a new exclusive JSONL file for each execution; logs contain safe counters/configuration enums, not media, hints, names, mappings or keys. |

## Direct transcription parameters

| Parameter | Default, purpose and valid combinations |
| --- | --- |
| `--input PATH` | Exactly one input choice: local regular media file or nonrecursive folder. Requires pipeline, extraction or workflow mode. Supported extensions and audio-stream checks are in the [README](../README.md#one-command-videoaudio--wav--transcription). |
| `--input-folder PATH` | Exactly one input choice: local folder, not a single file. Works for compatibility audio mode and pipeline/extraction/workflow. `--input_folder` remains a deprecated spelling. Empty inputs fail rather than choosing the current folder. |
| `--recordings-list PATH` | Exactly one input choice; requires workflow. Version-1 JSON with explicit ordered parts. Conflicts with input/input-folder. Set media types per part; leave global media type at `auto`. |
| `--output-folder PATH` | Default `private/output`. Private destination: pipeline jobs, ordered parts/generations or compatibility flat files. The older `--output_folder` spelling remains supported. Existing conflicting outputs are not silently overwritten. Repository-local content must be under ignored private/data directories. |
| `--pipeline` | Enable normalized media and manifest/checksum-based job outputs. Workflow already includes preparation; combining pipeline with workflow is accepted but redundant. |
| `--prepare-audio` | Local normalized WAV and manifest only; no provider/key needed. Conflicts with workflow, interview, enhancement and transcription hints. Can use resume and media timeout; pipeline is redundant. |
| `--resume` | Pipeline/extraction/workflow only. Verify source/settings/artifact hashes and reuse completed matching stages/checkpoints. Compatibility flat audio mode rejects it. Not an option on interview batch, which always resumes. |
| `--polish-text` | Explicit paid faithful layout derivative in compatibility/pipeline mode; workflow also selects polish even if omitted from stages. Keeps original raw unchanged. `--enhance_for_reading` is a deprecated spelling. Not permitted with extraction. |
| `--format-as-interview` | Compatibility audio-folder mode only: legacy faithful layout output with the saved interview-layout filename. It assigns no speaker identities and is unavailable in pipeline/workflow/extraction. `--format_as_interview` is deprecated. Prefer enhancement for new commands. Combining both layout flags in compatibility mode writes both legacy derivatives and can repeat editing requests. |
| `--author-workflow` | Select raw, optional polish, review and optional chapters. Default stages are raw,polish,review; no chapters by default. Cannot be combined with extraction. |
| `--media-type auto\|audio\|video` | Workflow only; default `auto` by extension. Explicit types must match every selected supported file. With an ordered manifest use per-part types and leave this at `auto`. |
| `--steps LIST` | Workflow only; comma-separated subset of raw,polish,review,chapters, without duplicates or spaces. Default raw,polish,review. Dependency order is fixed regardless of list order. Raw always remains; a chapters stage requires an actual chapter style. |
| `--chapter-style none\|interview\|narrative\|both` | Workflow only; default `none`. Any selected style implies review and chapters even if omitted from stages. Interview excerpts are deterministic; narrative arrangement can add model calls. |
| `--narrative-person first\|third` | Workflow only, default `first`. Applies to selected narrative chapters; does not invent testimony. |
| `--draft-with-unresolved-high` | Workflow with chapter selection only. Explicitly produce visibly warned drafts despite high findings. Never bypasses an incomplete review. There is no corresponding batch option. |
| `--separate-speakers` | Workflow only; add original plus separate attributed outputs and downstream selected stages. Requires both display names. Batch mode has its own optional names policy. |
| `--interviewer-name TEXT` | Interview only; required direct display name, not evidence of voice identity. Kept in private attributed outputs. |
| `--interviewee-name TEXT` | Interview only; required direct display name. Both names must be nonempty and distinct under case-insensitive comparison. |
| `--speaker-map MAPPING` | Interview only; repeat confirmed `PART:REQUEST:LABEL=interviewer` or `=interviewee` mappings. Part/request numbers begin at 1; labels belong only to that request. Unmapped voices remain unidentified. Names do not create mappings. See [speaker scopes](interview-attribution.md). |

For value options that accept compatibility spellings, choose one spelling in a command. As with other non-repeating argparse values, supplying the same setting multiple times keeps the last value. Selection, exclusions, languages and mappings are intentionally repeatable.

## Examples by goal

Prepare once, process two independent sessions together, then inspect retained state:

```bash
uv run --locked interview batch prepare --batch-plan private/config/batch-plan.json \
  --batch-folder private/batches/demo-001 --copy-local-files
uv run --locked interview batch run --batch-folder private/batches/demo-001 \
  --step raw --parallel-interviews 2 --send-to-openai --request-retries 0
uv run --locked interview batch status --batch-folder private/batches/demo-001
```

Resume both original and attributed raw with one shared allowance. Values below are examples to choose deliberately, not recommended account limits:

```bash
interview batch run --batch-folder private/batches/demo-001 --step raw \
  --separate-speakers --parallel-interviews 2 --send-to-openai \
  --transcription-model gpt-transcribe --speaker-model gpt-4o-transcribe-diarize \
  --audio-chunk-seconds 300 --speaker-chunk-seconds 60 \
  --request-retries 0 --request-timeout 45 \
  --max-requests 12 --max-run-seconds 600 --failure-limit 2
```

Preserve matching ASR/hints/durations on subsequent phases. Run review in parallel only after complete raw. Read the generated reports and listen to the recordings before the separately approved chapter command:

```bash
interview batch run --batch-folder private/batches/demo-001 --step review \
  --parallel-interviews 2 --send-to-openai
interview batch run --batch-folder private/batches/demo-001 --step chapters \
  --select entry-a --select entry-b --human-reviewed --chapter-style both \
  --parallel-interviews 2 --send-to-openai
```

If raw enabled attribution, add interview mode and the same speaker configuration/duration to these phases when requesting that family too. The example immediately above uses original-only families and default ASR settings. Approval applies only to explicitly selected families/entries and cannot be borrowed from another configuration.

Select the balanced candidate explicitly for a ready batch text phase:

```bash
interview batch run --batch-folder private/batches/demo-001 --step review \
  --text-profile balanced --parallel-interviews 4 --send-to-openai \
  --request-timeout 600 --request-retries 2 --failure-limit 2
```

This command omits total-request and admission-time caps; timeout, retries and the failure breaker remain bounded. Four workers are a deliberate example, not an account-limit recommendation. To keep Astra review while testing Sol polish, append `--review-model gpt-6-astra --review-reasoning-effort high`. Preserve the chosen review model/effort in a later approved chapter command, along with matching ASR and speaker settings. Model selection does not count as human review approval.

Use one selected session and one worker for at most one new provider operation:

```bash
interview batch run --batch-folder private/batches/demo-001 --select entry-a --step raw \
  --parallel-interviews 1 --send-to-openai --request-retries 0 \
  --max-requests 1 --max-run-seconds 180
```

The first uncached stage uses the allowance. A long interview usually remains incomplete. Save the checkpoint and inspect safe status before deliberately increasing the allowance; this is not a complete-interview or monetary budget.

Extract locally; later transcribe without rewriting raw; or request review from an ordered interview:

```bash
interview transcribe --prepare-audio --input private/input/synthetic.mp4 \
  --output-folder private/output --media-timeout 600
interview transcribe --pipeline --input private/input/synthetic.mp4 \
  --output-folder private/output --resume --transcription-model gpt-transcribe
interview transcribe --author-workflow --recordings-list private/config/session-a.json \
  --output-folder private/ordered-output --steps raw,review --resume
```

Use supported multilingual hints without interview diarization, which accepts at most one two-letter hint:

```bash
interview transcribe --author-workflow --input private/input/synthetic.wav --steps raw \
  --transcription-model gpt-transcribe --context-file private/hints/context.txt \
  --glossary-file private/hints/glossary.txt --language en --language hi \
  --output-folder private/multilingual
```

## Outputs, progress, costs and compatibility

Batch outputs stay under `item-NNNN/output/`: part raw/audio caches, combined interview generations and separate attributed namespaces. Raw text, provenance, review JSON/XLSX and chapter drafts remain independently bound to their exact source/settings. Private `summary-<run>.json` files retain item/family results in original plan order even when completion events arrive out of order. See [output trees](ordered-interviews.md#outputs-and-provenance) and [attributed outputs](interview-attribution.md).

Console output follows `--progress`; durable execution events always remain JSON lines. The [progress guide](pipeline-guide.md#read-the-progress-display) explains panel counters and bars. Match JSON `item` to the original plan position, use `family` for attributed events (other processing defaults to original), and inspect `stage`, `part`, `chunk` and `stage_status`. A shared sequence preserves event order, not interview completion order. Heartbeats identify each idle active session and its most recently recorded stage; `active_sessions` includes local work as well as provider waits. `cache_reused: true` means a stage was verified and skipped. Provider counters are cumulative admissions for the whole execution. Safe effective configuration includes session workers and contains no personal names/paths/hint text. Logs use owner-only permissions where supported and are not encrypted.

Batch processing keeps the chosen progress format through each nested interview and text phase. `selected` stays the number of selected interviews; only the batch coordinator reports `finished` as interview outcomes become final. Interactive command-line validation clears the live panel and stops status updates before printing usage and the error, so the diagnostic stays visible.

Exit codes: `0` for all selected work successful, `1` for failed/blocked/incomplete/unattempted work or runtime configuration errors, `2` for argument syntax errors, `130` for keyboard/SIGTERM interruption. A batch can contain a complete original phase and blocked attributed phase. Missing, mismatched or altered prerequisites produce `blocked` with a fixed `blocked_reason`, never a provider error. An entry with any blocked requested family is counted as blocked unless a processing failure or admission limit takes precedence; its completed family remains complete. Inspect `families`, `family_blockers` and `phase_result`, not just the overall entry status. Status reports history, not a fresh integrity check.

Parallelism changes when requests happen, not which stages are selected. Attribution adds a second audio pass and selected downstream work. Smaller chunks add boundaries/requests; grouped attributed polish reduces starts by retaining several short turns per validated request. SDK retries and bounded local text recovery can repeat remote work. Validated ASR, speaker-pass, polish, review and narrative request checkpoints are reused on matching pipeline/workflow or batch resume. Legacy audio-folder mode has no durable request resume. Text events expose actual SDK-operation/latency counters and available provider token usage with missing-usage markers; these are not HTTP-attempt counts or a bill. Per-request completion allowances remain bounded, but there is no whole-run token/dollar budget, live latency/accuracy/cost guarantee or configured requests-per-minute throttle. See [recovery controls](recovery-controls.md) for exact metric, checkpoint and admission semantics.

New examples use the clearer names listed below. Older spellings keep the same setting values, defaults, cache identities and saved output naming. Underscore spellings and the legacy interview-layout operation are deprecated for new scripts; no removal release is scheduled. The other aliases remain supported. No runtime upgrade or dependency change is needed for these aliases or the terminal display.

## Older option spellings

These pairs are interchangeable. Prefer the left column for new commands; existing scripts do not need an immediate rename. Choices such as `raw`, `polish`, `review`, and `chapters` retain their meanings. Repeating a non-repeatable setting with both spellings still uses the last supplied value.

| Preferred option | Older spelling | Available on |
| --- | --- | --- |
| `--recordings-list` | `--interview-manifest` | `interview transcribe` |
| `--author-workflow` | `--workflow` | `interview transcribe` |
| `--prepare-audio` | `--extract-only` | `interview transcribe` |
| `--polish-text` | `--enhance-for-reading`, `--enhance_for_reading` | `interview transcribe` |
| `--separate-speakers` | `--interview` | Both commands |
| `--speaker-chunk-seconds` | `--diarization-chunk-seconds` | Both commands |
| `--steps` | `--stages` | `interview transcribe` |
| `--step` | `--phase` | `interview batch` |
| `--chapter-style` | `--chapters` | Both commands; direct default `none`, batch chapter default `both`. |
| `--review-model` | `--author-model` | Both commands |
| `--review-reasoning-effort` | `--author-reasoning-effort` | Both commands |
| `--request-timeout` | `--provider-timeout` | Both commands |
| `--request-retries` | `--provider-retries` | Both commands |
| `--max-requests` | `--max-provider-requests` | Both commands |
| `--failure-limit` | `--provider-failure-limit` | Both commands |
| `--status-interval` | `--heartbeat-seconds` | Both commands |
| `--logs-folder` | `--log-directory` | Both commands |
| `--batch-plan` | `--plan` | `interview batch` |
| `--batch-folder` | `--batch` | `interview batch` |
| `--download-cloud-files` | `--allow-hydration` | `interview batch prepare` |
| `--transcription-model` | `--model` | Both commands |
| `--speaker-model` | `--interview-model` | Both commands |
| `--input-folder` | `--input_folder` | `interview transcribe` |
| `--output-folder` | `--output_folder` | `interview transcribe` |
| `--format-as-interview` | `--format_as_interview` | Direct legacy audio-folder mode; layout only, no voice identities. |

## Multiple sessions at once

`interview batch run --parallel-interviews N` sets the maximum number of interviews running together. `N` must be a positive integer; the default is `1`, preserving serial processing. Start with `2` when you deliberately want overlap. There is no automatic increase or hardware-based choice. The active pool is capped by the selected entry count, and only that many entries are submitted at a time. Large values can increase local memory, temporary WAV storage, FFmpeg load and provider traffic.

Each worker completes the existing interview workflow. Parts, chunks and stage dependencies within an interview retain their order. Original and attributed families keep separate files, bindings and gates within that entry's output directory. Workers do not share transcript text or speaker mappings. A local validation/media failure affects its interview; other independent entries continue. A provider stop is shared across every worker and every stage.

`--parallel-interviews` controls overlap, **not requests per minute, tokens per minute or a dollar budget**. After a final SDK transient 429, new starts share a bounded cooldown and then 0.25-second spacing. This does not coordinate SDK-internal retries or enforce account request/token limits. Up to N workers can have provider operations admitted together; an operation may include SDK retries. A higher value does not prove better latency, fix a network problem or improve transcription quality. Your provider account can impose additional limits.

`--max-requests` is one shared allowance for the whole execution, not an allowance per interview. `--max-run-seconds` is one elapsed admission deadline, not N separate timers. Failure streaks are also shared per endpoint/model and counted in response-completion order. A successful response resets that scope's streak but never reopens stopped admission. Calls admitted before a stop can still finish and save valid checkpoints. Later unscheduled sessions are `not_attempted`; already active sessions can finish successfully or report failed/incomplete work. Which session receives a scarce request allowance depends on scheduling, so use one selected session and one worker for a diagnostic with a predictable target.

Ctrl+C/SIGTERM stops scheduling and denies new provider operations. The coordinator waits for already running workers, retaining their returned responses and holding the batch lock and execution log open until they finish. Cleanup may take time: SDK timeouts apply to I/O waits, retries can extend them, and local preparation can finish before a worker reaches its next provider check. A signal does not prove that remote work stopped or avoided a charge. When draining completes, locks are released and exit 130 is returned. Completed sessions remain complete; unfinished active sessions are interrupted; unscheduled sessions are unattempted. Repeating the same batch command reuses verified artifacts and request checkpoints automatically. Direct pipeline/workflow commands require `--resume`.

## Timeout phases and blocked families

For a recognized SDK timeout, `timeout_phase` is one of `connect`, `write`, `read`, `pool` or `unknown`. These fixed values come only from recognized HTTPX/HTTPX2 exception types in the active exception chain. `write` concerns sending request data; `read` concerns waiting for response data. A read timeout does not establish whether the provider or the network caused the delay. `pool` is waiting for a local connection slot. Generic, suppressed, contradictory, cyclic or overlong chains remain `unknown`. No error messages, URLs, headers, credentials, request IDs or response bodies are emitted. Older logs cannot gain this detail retroactively.

Batch text phases report each requested family’s prerequisite separately. `raw_prerequisite` means matching complete raw or intact supporting artifacts could not be verified; `review_prerequisite` means the chapter review bundle could not be accepted; `high_findings`, `human_review_required` and `staging_unverified` explain the other blocked gates. These checks consume no provider allowance. Ready families may still incur paid text requests, and the whole command returns nonzero if a requested family remains blocked. A `phase_result` stage records each executed family’s final phase outcome.

To request only original review, omit both `--separate-speakers` and `--speaker-config` and select sessions with complete original raw. With `--separate-speakers`, original and attributed review/chapter processing can succeed independently. Completed original review never approves attributed chapters. Each eligible chapter family still requires its own exact intact review, no high findings and actual human approval.

For complete batches, use the [full-run commands](recovery-controls.md#full-runs-omit-diagnostic-caps): omit diagnostic caps, choose timeout 600/retries 2 and keep two-interview concurrency initially. For speaker-only duration comparisons, use the [experiment guide](speaker-chunk-experiment.md).
