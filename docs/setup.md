# Setup and dependency management

- Python **3.14 or newer**; the development/runtime pin is **3.14.8** in `.python-version`. Python 3.9 is no longer supported. The 3.14 floor follows this project's current environment standard; it is not a claim that the SDK itself requires 3.14.
- [uv](https://docs.astral.sh/uv/getting-started/installation/) **0.12.23 or newer**. Older versions may not know the pinned Python release.
- FFmpeg **and ffprobe** available on `PATH`. Check with `ffmpeg -version` and `ffprobe -version`. They are external system prerequisites, not Python packages; install them yourself through your trusted package manager if missing.
- Your own OpenAI account, API key, quota, and network access for transcription. Hosted API use can incur charges; this project does not run a local Whisper model.

For a new clone, use `git clone https://github.com/BitMorpher/interview-studio.git` and `cd interview-studio`. For existing installations, read the [migration guide](migration.md) before changing environments.

From the checkout root:

```bash
uv python install 3.14.8
uv sync --locked
uv run --locked interview --help
uv run --locked interview transcribe --help
uv run --locked interview batch --help
```

`uv sync --locked` creates the ignored `.venv`, installs the editable CLI, runtime dependencies and the default `dev` group using committed `uv.lock`. The Python pin selects the standard CPython runtime, not a free-threaded build. Setup may download Python and packages; it never installs FFmpeg or configures credentials. Once synced, use `uv run --locked` for commands without activating the environment. For a runtime-only installation, use `uv sync --locked --no-dev` and `uv run --locked --no-dev interview transcribe --help` (plain `uv run` would reinstall the default development group).

`pyproject.toml` is the only dependency declaration: `openai` and `openpyxl` are runtime dependencies; `dev` contains pytest and Ruff. OpenPyXL writes the author review workbook. Optional `notebook` contains PyDub, `audioop-lts`, ipykernel and JupyterLab. `uv.lock` records exact versions, public PyPI locations and hashes for all groups/extras. Build-backend versions are pinned separately in `[build-system]`, since uv's project lock does not lock isolated build requirements. FFmpeg is not managed by the lock. The CLI does not need PyDub, local Whisper, Torch, NumPy or SciPy.

The existing experimental notebook is available separately:

```bash
uv sync --locked --extra notebook
uv run --locked --extra notebook jupyter lab notebook/development.ipynb
```

`audioop-lts` provides the module PyDub needs after Python [removed `audioop` in 3.13](https://docs.python.org/3/library/audioop.html). These notebook dependencies are excluded from the default CLI install. The blank notebook input must be supplied locally; keep any outputs/media and saved notebook results private. Clear notebook outputs before committing changes.

The old `setup.py` and hand-maintained requirements files have been replaced. Standard Python installers can still install this PEP 517/621 project with `python -m pip install .` in a compatible virtual environment, but that command does not consume `uv.lock`. If a pip requirements export is needed, generate it from the lock into ignored storage:

```bash
mkdir -p private
uv export --locked --no-dev --no-emit-project --format requirements.txt --output-file private/requirements.txt
```

To deliberately update dependencies, edit/add them with uv, run `uv lock`, review the lock diff and run the [offline verification checks](development.md). `uv lock --check` and `--locked` refuse a stale lock instead of silently updating it. See the official [uv project sync documentation](https://docs.astral.sh/uv/concepts/projects/sync/) for group/extra selection.

Set `OPENAI_API_KEY` yourself in the current process environment, preferably through your secret manager. An interactive terminal prompt avoids putting the key into shell history:

```bash
read -rs OPENAI_API_KEY
export OPENAI_API_KEY
```

This prompt syntax works in bash/zsh. Never paste a real key into a command, notebook, tracked file, or chat. `.env.example` contains a placeholder; `.env` files are ignored and **are not automatically loaded**. The CLI never creates persistent credentials. SDK environment settings, including a custom base URL, remain user-managed.
