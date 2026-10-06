# Batch media staging and optional speaker attribution

The installed `voice-batch` CLI retains original transcripts and processing defaults.
`inventory` and `check` inspect JSON and filesystem metadata only. `prepare` requires
`--copy-local-files`; `run` requires `--send-to-openai`. Provider and human-review
gates remain separate. Plans, speaker settings, staging and outputs are private user
files. Keep them outside source control.

## Extensionless video files

An ordered manifest may explicitly declare an extensionless local recording:

```json
{"version": 1, "interview_id": "session-a", "parts": [
  {"id": "recording-a", "path": "../input/local-recording", "media_type": "video"}
]}
```

No extensionless `auto` or `audio` inputs are accepted. Metadata preflight cannot
establish that the file contains valid video/audio. During authorized `prepare`,
the coordinator verifies the original metadata, copies exact bytes into a private
pending file, and compares hashes. It then runs local ffprobe on that copy with
file-only protocols, an allowlist of container demuxers, discarded diagnostics,
restricted JSON fields and a timeout capped at 30 seconds (or the smaller
`--media-timeout`). Probe JSON reads are capped at 64 KiB. Both an audio stream and
a video stream excluding attached cover artwork must be present.

Recognized container families receive generic processing suffixes: ISO BMFF/MOV
family `.mp4`, Matroska/WebM family `.mkv`, AVI `.avi`. These are staged filenames,
not claims of conversion or original MIME type. The copied bytes do not change;
original filenames remain untouched. Unsupported containers, missing streams,
failed probes, malformed results or changed bytes fail staging without publishing
an accepted ledger. Partial copies remain private for inspection; use a fresh batch
after failed prepare. Source hydration still requires `--allow-hydration`.

Entries containing extensionless videos use staging ledger version 2, binding the
normalized suffix and minimal probe evidence to each copied content hash and the
immutable original metadata snapshot. `verify` validates those records and staged
bytes without reading or probing originals. Existing unversioned ledgers remain
version 1 and keep their original verification rules. Ordinary named inputs retain
version 1 staging. Neither staging nor content probing confirms recording order;
unconfirmed groups must remain blocked or be explicitly split into independent
sessions by the user.

## Per-entry attribution, with unknown identities by default

```bash
voice-batch run --batch private/batches/session-001 --phase raw \
  --send-to-openai --interview
```

This explicitly adds `gpt-4o-transcribe-diarize` requests using `diarized_json` and
`chunking_strategy=auto` alongside the existing ASR model. The model contract and
limitations are described in [interview attribution](interview-attribution.md).
Without names or mappings, all provider speaker IDs remain unidentified. No
interviewer identity, dialogue role or continuity across provider requests is
inferred. Additional speakers and overlap remain represented with uncertainty.

`--speaker-config` optionally reads this private configuration:

```json
{
  "version": 1,
  "interviews": [
    {"id": "entry-a", "interviewer_name": null,
     "interviewee_name": "Example Guest", "speaker_map": []},
    {"id": "entry-b", "enabled": false}
  ]
}
```

IDs must exist in the batch snapshot. Duplicate/unknown IDs, JSON keys, malformed
names or mappings, competing fields on disabled entries and unsupported models
fail before provider calls. Omitted entries use unidentified-speaker attribution;
`enabled: false` retains only the original family for that entry. Empty names are
invalid; omitted or null names are allowed. Names alone do not identify a voice.
An optional `--interview-model` accepts only `gpt-4o-transcribe-diarize`.
Speaker options require `run --interview`; they do not silently enable extra ASR.

After listening to the recording, an entry with a supplied guest name can use
`"speaker_map": ["1:1:B=interviewee"]`. Each key is recording-part index,
local API-request index and provider label, not dialogue turn number. A mapping
requires a supplied name for its target role. Repeated IDs/labels in different
parts or requests do not imply the same person. No reference audio upload or
arbitrary-person recognition is implemented.

Speaker settings are separate from immutable staging. Editing names/mappings
creates a different attributed family while reusing verified diarization responses
and original caches. Run `raw` with the revised configuration before `review`;
review/chapter gates will not buy a new missing diarization pass automatically.
The configuration is loaded and validated once per invocation. It does not modify
previous outputs or approve a previous review for changed provenance.

Batch output uses attributed contract v3, including a provenance checksum in its
manifest and a separate names/mappings namespace. Direct-CLI contract v2 remains
unchanged and still requires both names; existing v1/v2 outputs remain intact.
Raw provider payloads, exact attributed speech, per-part files and source-bound
review/chapter references retain the documented attribution evidence. The shared
recording provenance and diarization cache contracts remain version 1.

## Independent stages and human approval

```bash
voice-batch run --batch private/batches/session-001 --phase review \
  --send-to-openai --interview --speaker-config private/config/speakers.json \
  --provider-retries 0 --provider-timeout 30
voice-batch run --batch private/batches/session-001 --phase chapters \
  --select entry-a --human-reviewed --send-to-openai --interview \
  --speaker-config private/config/speakers.json --chapters interview
```

Use the same selected families, ASR model, hints, chunk size and speaker settings
across phases. Review requires verified complete raw for both requested families.
It runs polish and review independently. Chapters require explicit entry selection,
human review of both requested families, complete source/settings-bound reports
and no high findings in either. Original review cannot approve attributed chapters.
Approvals capture exact manifest, raw, provenance and JSON/XLSX identities and are
revalidated before any provider request. Chapter generations reuse the exact
accepted review bytes; they do not regenerate an unapproved review.

An attributed family rejects conflicting completed polish/review/chapter settings
before further requests; use fresh output for a deliberate editorial configuration
change. Changing speaker mappings instead creates a new attributed source that
requires its own complete review and human approval. There is no batch high-finding
bypass. A failed attributed stage does not relabel completed original or attributed
raw/review stages as failed. Complete earlier artifacts remain inspectable.

The extra ASR pass and selected downstream stages can incur additional charges.
SDK retries zero and a request timeout do not impose a whole-run budget. Batch
processing may continue after ordinary failures; stop and inspect the first failure
before bulk retries. Individual chunks are not durable checkpoints, so repeating
failed stages may repeat successful requests. Safe logs exclude names, paths,
transcripts, configuration values, probe output and arbitrary provider diagnostics.

## Isolated installation

Create a fresh dedicated environment rather than replacing an existing user tool:

```bash
uv venv --no-project --python 3.14 private/runtimes/session-engine-001
uv pip install --python private/runtimes/session-engine-001/bin/python \
  --constraints private/config/runtime-constraints.txt \
  "git+https://github.com/BitMorpher/voice-transcription-engine.git@<reviewed-commit>"
private/runtimes/session-engine-001/bin/voice-batch --help
```

Use a constraints file exported from that reviewed commit's lockfile. On Windows,
use the environment's `Scripts/python.exe` and installed console executable.
Do not reuse an occupied runtime directory. `uv tool install --force` can recreate
an existing tool environment and replace same-named entry points. The isolated
commands above preserve existing tools and environments. Tests use synthetic media,
mocked providers and installed-wheel entry points; they do not establish live
speaker accuracy or publication clearance.

## Recovering long speaker passes

Use an explicit independent `--diarization-chunk-seconds` to experiment with shorter speaker requests while keeping original ASR settings/caches. Request checkpoints, admission bounds, mapping reconfirmation and recorded per-family status are described in [recovery controls](recovery-controls.md). Defaults remain unchanged and live quality is unverified.
