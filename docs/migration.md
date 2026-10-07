# Moving to Interview Studio

Voice Transcription Engine is now **Interview Studio**. The canonical repository is
[`BitMorpher/interview-studio`](https://github.com/BitMorpher/interview-studio).
The Python distribution is `interview-studio`; the primary command is `interview`.

| Existing command | Preferred command | Behavior |
| --- | --- | --- |
| `voice-transcribe OPTIONS` | `interview transcribe OPTIONS` | Same single-file, folder, and ordered-interview workflows. |
| `voice-batch ACTION OPTIONS` | `interview batch ACTION OPTIONS` | Same batch actions, approvals, selection, and automatic resume. |

Both existing commands and all previous option spellings remain supported. No removal
date is scheduled. `interview --help`, `interview transcribe --help`, and
`interview batch --help` show the command structure. Options belong after `transcribe`
or `batch`; batch actions still follow `batch`, for example `interview batch status`.

The implemented workflows retain faithful raw transcripts, optional speaker-attributed
derivatives, text polish, editorial review, and chapter drafts with provenance and human
review gates. Interview-technique coaching is planned; there is no coaching command yet.

## Install in a dedicated environment

From a new checkout:

```bash
git clone https://github.com/BitMorpher/interview-studio.git
cd interview-studio
uv sync --locked
uv run --locked interview --help
uv run --locked interview transcribe --help
uv run --locked interview batch --help
uv run --locked voice-transcribe --help
uv run --locked voice-batch --help
```

For an installed runtime, build the reviewed checkout and install its wheel in a fresh
environment. Keep existing active runtimes intact until their runs finish:

```bash
uv build
uv venv --python 3.14.8 private/runtime
uv pip install --python private/runtime/bin/python dist/interview_studio-0.1.0-py3-none-any.whl
private/runtime/bin/interview --help
private/runtime/bin/voice-transcribe --help
private/runtime/bin/voice-batch --help
```

The paths above use POSIX shell conventions; on Windows use the environment's `Scripts`
directory. FFmpeg and ffprobe remain external prerequisites. See [setup](setup.md).

The distribution name changed, so an installer treats `voice-transcription-engine` and
`interview-studio` as different distributions. **Do not install both into one environment:**
they share the preserved `voice_transcription_engine` import namespace and legacy scripts.
Create a fresh environment instead of overlaying or uninstalling one from a shared install.
Existing imports such as `voice_transcription_engine.cli` continue to work in the new wheel.

`interview` is also the name of an unrelated [PyPI package](https://pypi.org/project/interview/).
Do not use `pip install interview` for this project. Use the verified repository or built
wheel above. A public PyPI metadata check on 2026-10-07 found no project named
`interview-studio`; this does not reserve the name. Check `command -v interview` (or
`Get-Command interview` on Windows) before using a global command, and prefer the explicit
environment path when another tool owns that command. This check is not trademark clearance.

## Existing work and local checkouts

No destructive cache or output migration is required. Existing raw/audio identities,
batch snapshots and ASR fingerprints remain compatible, including omitted speaker
duration using the original audio duration. Supply the same input paths, output folders
and ASR/speaker settings when resuming. Text versions now bind editing/review settings
independently: review-only changes reuse matching validated polish, and changing a text
model/effort preserves earlier artifacts. Manifests retain per-configuration derivative
records and record the selected artifact paths. Compatible legacy outputs require exact
source/settings hashes and fidelity validation before reuse; altered, missing or unbound
artifacts fail closed. No old files are moved or deleted. Human notes and historical
bundles are not rewritten.

Defaults remain Astra/high for polish and review. The explicit `--text-profile balanced`
opts into Sol 6.1/low polish and Sol 6.1/medium review candidate; individual model and
effort flags override it. This opt-in prevents an upgrade from silently switching existing
model settings or selecting different caches. Review model/effort also apply to narrative
chapters and must match their reviewed prerequisite. See the [option reference](cli-reference.md)
and [text comparison guide](text-comparison.md) before another paid run. Use a new dedicated
runtime for the reviewed release, keeping running processes and existing runtime environments
intact.

GitHub redirects old repository links. For an existing clone, verify the canonical
repository before changing only its remote URL:

```bash
git remote -v
git remote set-url origin https://github.com/BitMorpher/interview-studio.git
```

A local folder rename is optional for running the CLI. Before moving a checkout, close or
relocate shells, editors, and processes that use it, confirm the destination does not
exist, and keep dirty/untracked files with the checkout. Moving a checkout can break
absolute paths in editable environments, script shebangs, or private input manifests;
recreate its development environment and inspect your path references afterward. Do not
rename installed runtimes, media, batches, outputs, or old task folders as part of branding.
