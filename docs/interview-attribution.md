# Additive interview speaker attribution

`voice-transcribe --workflow --interview` keeps the current output family and
adds a separate attributed family. It uses actual provider speaker segments at
transcription time; it does not ask a text editor to invent dialogue turns.
Names alone cannot identify a voice. Initially, voices appear as scoped speaker
labels. After listening to the recording, supply explicit mappings to show the
interviewer/interviewee names. Mapping identifies a user-confirmed claim; it does
not establish biometric identity or verify the provider's diarization.

```bash
voice-transcribe --workflow --interview --input private/input/session.wav \
  --output-folder private/output/session --stages raw \
  --interviewer-name "Example Host" --interviewee-name "Example Guest"

# After listening and identifying the provider's A and B voices in request 1:
voice-transcribe --workflow --interview --input private/input/session.wav \
  --output-folder private/output/session --stages raw --resume \
  --interviewer-name "Example Host" --interviewee-name "Example Guest" \
  --speaker-map 1:1:A=interviewer --speaker-map 1:1:B=interviewee

# Select the same additional stages for both output families:
voice-transcribe --workflow --interview --input private/input/session.wav \
  --output-folder private/output/session --stages raw,polish,review --chapters both --resume \
  --interviewer-name "Example Host" --interviewee-name "Example Guest" \
  --speaker-map 1:1:A=interviewer --speaker-map 1:1:B=interviewee

# Ordered recordings from one interview are also supported:
voice-transcribe --workflow --interview --interview-manifest private/input/interview.json \
  --output-folder private/output/ordered-session --stages raw,review \
  --interviewer-name "Example Host" --interviewee-name "Example Guest"
```

See [ordered recording inputs](ordered-interviews.md) for the manifest contract.
`--interview` requires `--workflow` and both distinct, nonempty names. Names are
local display metadata and are not sent as voice hints to the provider. Name and
mapping flags without `--interview`, duplicate or malformed mappings, unsupported
models, extraction mode, and multiple language hints fail before provider setup.
`--interview` is a `voice-transcribe` option; `voice-batch` retains its existing
CLI and explicit provider/human-review gates.

## Diarization versus mapping

The original `--model` remains unchanged (default `gpt-transcribe`). The
additional `--interview-model` currently accepts only
`gpt-4o-transcribe-diarize`. That model returns `diarized_json` with segment text,
speaker, start and end. `chunking_strategy=auto` is always sent, satisfying the
provider requirement for requests longer than 30 seconds. This CLI exposes no
format override: ordinary JSON/text would lose speaker annotations; SRT/VTT,
verbose JSON, word timestamp granularities, prompts and logprobs are not used
with the diarization model.

Each local request remains bounded by `--audio-chunk-seconds` (1–600, default
300) and 20 MiB. Exact PCM frames, including a fractional final request, are
retained. Original context/glossary hints apply only to the original pass; the
diarizer does not support prompts. A single two-letter `--language` hint can
apply to both passes; omit language hints for automatic detection. Other
transcription models and Realtime sessions do not provide this CLI's speaker
segment contract.

`--speaker-map PART:REQUEST:LABEL=interviewer` or `=interviewee` is an explicit
confirmation after listening. Part and request indices start at 1. For a single
file, part is 1. For an ordered manifest, part follows its array order. For a
folder input, each file is an independent interview, so the same mapping applies
to each file; use individual commands when recordings need different mappings.
The labels come from `provider_responses.json` or `provenance.json`, not from
turn order. For example, `1:1:A` and `1:2:A` are different scoped voices. Even
within a continuous interview, later recordings and later API requests may
reset IDs. No names or roles propagate automatically across those boundaries.
Multiple local IDs may be explicitly mapped to the same participant. Unknown
or missing labels remain unidentified; an unmatched mapping fails without
publishing a misleading attributed transcript.

More than two speakers remain in the output; only confirmed identities receive
names. Segments retain provider order, even when timestamps overlap. Overlap
flags are derived from intersecting timestamps; they do not establish that the
provider captured every simultaneous voice. Missing/unclear speech, speaker
splits or merges, accents, noise and overlap require listening. No questions,
answers, dialogue roles, or missing words are inferred from text.

OpenAI also supports up to four known-speaker references, paired with short
identifiers and 2–10 second audio clips. This implementation deliberately uses
explicit scoped mappings instead; it has no reference-upload or arbitrary-person
voice-recognition feature. A future reference feature would require explicit
local clips, consent/access decisions, clip validation, and content-hash cache
bindings. Passing names by themselves is not a substitute for audio references.

## Separate artifacts and resume

Original jobs, raw text, polish, review JSON/XLSX and chapter drafts keep their
existing paths and contracts. Attributed outputs live under:

```text
<output>/attributed/
  diarization-cache/<audio-and-provider-config-sha256>/
    provider_responses.json
    manifest.json
  <source-binding-sha256>/<names-and-mappings-sha256>/
    transcription.txt
    diarization_transcription.txt
    attribution_notice.txt
    provenance.json
    manifest.json
    derivative_readability.txt                    # when polish is selected
    author_review_<opaque-id>/review_report.json   # when review is selected
    author_review_<opaque-id>/review_report.xlsx
    chapters_<opaque-id>/chapter_drafts.json       # when chapters are selected
    chapters_<opaque-id>/chapter_interview.txt     # selected styles only
    chapters_<opaque-id>/chapter_narrative.txt
```

Here `transcription.txt` is an **attributed derivative**, including metadata
headers and exact provider segment speech, not a replacement for original raw
ASR text. `diarization_transcription.txt` retains the full provider text, with
explicit blank-line separators between requests; `provider_responses.json`
retains every response field, full text and segment text independently. The
original pass can produce different words from the diarization model. This
implementation never substitutes either transcript for the other. If full
provider text cannot be reconciled with segment text (with or without provider
speaker prefixes), the cache remains available but the derivative fails rather
than silently losing words.

`provenance.json` records segment indices, scoped speaker keys, identity evidence
(`user_confirmed_mapping` or `unidentified`), automatic/unverified diarization,
overlap flags, exact speech hashes and character spans, original raw hashes, and
part-local audio timestamps. No global time is inferred across recording pauses.
Metadata headers are not spoken words. `attribution_notice.txt` explains this
alongside the attributed transcript. Polish edits each speech segment separately
and restores exact metadata labels. Review JSON includes source-bound speaker
references; XLSX has a **Speaker turns** sheet with the same evidence. Both chapter
styles include speaker provenance and visible uncertainty. Inspect JSON for
precise source spans; chapter layout remains a human-review draft.

Raw always remains available. The same selected stages run independently for both
families; without `--interview` there are no extra diarization or author calls.
A complete attributed review never approves an original review, or vice versa.
Both retain the complete-review and unresolved-high gates described in the
[author workflow](author-workflow.md). `--draft-with-unresolved-high` remains an
explicit, visibly labeled draft override for both families. Existing batch
approval cannot be transferred to an attributed source.

Names/mapping changes create a new attributed family and can reuse verified
diarization responses with `--resume`; those display changes do not require
another audio request. Stages and editorial models are bound independently to
the exact source and provenance. Tampered, missing, symlinked, or conflicting
completed artifacts fail without overwriting; changed provider/chunk settings
require fresh output where an existing family conflicts. Failed diarization is
retried for the whole recording: individual requests are not checkpointed, and
successful requests in a failed recording may be charged again. Completed earlier
recording caches remain reusable. Locks and atomic directory publication keep
interrupted writes from appearing complete; inspect a stale lock before removing
it. Storage uses the same private permissions and repository output restrictions
as the original pipeline.

## Cost, privacy, and validation

Enabling this mode sends a **second audio transcription pass**. Selected polish
and author review run again for the attributed source; narrative chapter
arrangement can add model calls. Interview chapter excerpts remain deterministic.
Retries may add charges. Review/chapter gates apply independently, and completed
original outputs remain available if attribution fails. Start with `--stages raw`
and inspect speaker turns before requesting further stages.

Only media content and supported transcription settings go to the additional
ASR pass. Generic `audio.wav` upload names conceal local filenames. Names may be
sent in attributed review/chapter text because they are part of the derivative;
speech-only polish does not send name headers. Logs report allowlisted stages,
opaque counters and `family=attributed`, never names, paths, mappings, transcript
text, reference clips or provider exception bodies. Raw/provider output stays in
private artifacts. No credentials are persisted.

The automated tests use synthetic audio, mocked providers and a local SDK mock
transport. They verify request serialization, frame coverage, turn provenance,
additional speakers, overlap, missing labels, malformed responses, CLI privacy,
independent resume and collisions, retained output families, and review gates.
They do **not** establish live diarization accuracy. The next validation step is
an explicitly approved, consented short two-speaker recording: listen to every
turn, confirm scoped mappings, check resets/overlap, and compare both raw outputs.
No paid/live provider validation was performed during implementation.

Provider contract checked against official documentation and the locked
`openai==3.24.0` SDK on 2026-10-05:
[OpenAI speech-to-text guide](https://developers.openai.com/api/docs/guides/speech-to-text),
[transcription API reference](https://developers.openai.com/api/reference/resources/audio/subresources/transcriptions/methods/create).
