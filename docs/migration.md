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

No cache or output migration is required. Job identities, stage fingerprints, manifests,
batch snapshots, prompt versions, provenance fields, schema identifiers, artifact paths,
and saved pipelines retain their existing contracts. Supply the same input paths, output
folders, and settings when resuming. Human annotations and historic artifacts are not
rewritten. New review workbooks identify their creator as Interview Studio.

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
