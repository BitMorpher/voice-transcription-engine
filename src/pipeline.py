"""Resumable local media pipeline; manifests contain only opaque IDs and checksums."""

import hashlib
import json
import os
from pathlib import Path

if __package__:
    from .author_workflow import AuthorWorkflowError, run_author_stages
    from .media import MEDIA_EXTENSIONS, MediaError, prepare_audio
    from .model_config import DEFAULT_ASR_MODEL, EditingOptions, TranscriptionOptions
    from .private_output import digest, output_directory, write_private
    from .transcriber import TranscriptionError
else:
    from author_workflow import AuthorWorkflowError, run_author_stages
    from media import MEDIA_EXTENSIONS, MediaError, prepare_audio
    from model_config import DEFAULT_ASR_MODEL, EditingOptions, TranscriptionOptions
    from private_output import digest, output_directory, write_private
    from transcriber import TranscriptionError


class PipelineError(RuntimeError):
    """A safe pipeline/storage error."""

    def __init__(self, message, *, stages=None):
        super().__init__(message)
        self.stages = stages or {}


class Pipeline:
    def __init__(self, output, *, resume=False, media_timeout=3600, model=DEFAULT_ASR_MODEL,
                 options=None, editing_options=None, author_options=None, progress=None):
        self.output = output_directory(output)
        self.resume = resume
        self.media_timeout = media_timeout
        self.options = options or TranscriptionOptions(model=model)
        self.editing_options = editing_options or EditingOptions()
        self.model = self.options.model
        self.author_options = author_options
        self.progress = progress or (lambda stage, status: None)

    @staticmethod
    def _verified(job, state, stage, filename, configuration=None, transcription_sha256=None):
        record = state['stages'].get(stage)
        target = job / filename
        return (
            isinstance(record, dict) and record.get('status') == 'complete'
            and (configuration is None or record.get('configuration_sha256') == configuration)
            and (transcription_sha256 is None
                 or record.get('transcription_sha256') == transcription_sha256)
            and target.is_file() and not target.is_symlink()
            and record.get('sha256') == digest(target)
        )

    def _enhancement_fingerprint(self, transcription_sha256):
        """Bind the editing contract and settings to the exact raw transcript."""
        binding = {'contract': 1, 'editing_configuration_sha256': self.editing_options.fingerprint,
                   'transcription_sha256': transcription_sha256}
        return hashlib.sha256(json.dumps(binding, sort_keys=True).encode('utf-8')).hexdigest()

    @staticmethod
    def _write_derivative(job, transcriber, expected_sha256):
        raw = job / 'transcription.txt'
        snapshot = raw.read_bytes()
        if raw.is_symlink() or hashlib.sha256(snapshot).hexdigest() != expected_sha256:
            raise PipelineError('Raw transcript changed before editing; use a stable transcript and a new output folder.')
        edited = transcriber.enhance_transcription(snapshot.decode('utf-8'))
        if raw.is_symlink() or digest(raw) != expected_sha256:
            raise PipelineError('Raw transcript changed during editing; no derivative was saved. Use a new output folder.')
        write_private(job / 'derivative_readability.txt',
                      'AI readability derivative; verify against transcription.txt.\n'
                      'Speaker identities and turn boundaries are unverified; no roles are inferred.\n\n'
                      + edited)

    def process(self, source, *, transcriber=None, extract_only=False, enhance=False):
        """Return per-stage statuses. Resume requires a matching, verified manifest.

        Existing unverified artifacts are conflicts, never overwritten. Reattempt
        failed transcription from prepared audio; API chunks are not checkpointed.
        """
        source = Path(source)
        if not source.is_file() or source.is_symlink() or source.suffix.lower() not in MEDIA_EXTENSIONS:
            raise PipelineError('Input must be a supported local regular media file.')
        if transcriber is not None and transcriber.options.fingerprint != self.options.fingerprint:
            raise PipelineError('Transcriber model and hints must match the pipeline configuration recorded in the manifest.')
        if transcriber is not None and enhance and transcriber.editing_options.fingerprint != self.editing_options.fingerprint:
            raise PipelineError('Editing model must match the pipeline editing configuration.')
        source_hash = digest(source)
        # Include basename bytes to distinguish identical files with different names,
        # but never persist the name itself. Full paths are not part of the ID.
        identity = hashlib.sha256(os.fsencode(source.name) + b'\0' + bytes.fromhex(source_hash)).hexdigest()
        job = self.output / identity
        if job.is_symlink():
            raise PipelineError('Refusing a symlink job directory.')
        job.mkdir(mode=0o700, exist_ok=True)
        os.chmod(job, 0o700)
        manifest = job / 'manifest.json'
        lock = job / '.lock'
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        except FileExistsError:
            raise PipelineError('Job is locked; wait for its running process or remove a stale lock after checking.') from None
        try:
            state = {'version': 2, 'source_sha256': source_hash, 'model': self.model,
                     'transcription_configuration_sha256': self.options.fingerprint, 'stages': {}}
            if os.path.lexists(manifest):
                if not self.resume:
                    raise PipelineError('Job already exists; use --resume to verify and skip completed stages.')
                try:
                    if manifest.is_symlink():
                        raise ValueError()
                    state = json.loads(manifest.read_text(encoding='utf-8'))
                    if (state.get('version') != 2 or state.get('source_sha256') != source_hash
                            or not isinstance(state.get('stages'), dict)):
                        raise ValueError()
                    configuration_changed = (state.get('model') != self.model
                        or state.get('transcription_configuration_sha256') != self.options.fingerprint)
                    if configuration_changed:
                        record = state['stages'].get('transcription', {})
                        if (not isinstance(record, dict) or record.get('status') == 'complete'
                                or os.path.lexists(job / 'transcription.txt')):
                            raise ValueError()
                        # Extraction has no ASR configuration. A fresh/failed ASR
                        # stage may use newly supplied model/hints without replacing raw text.
                        state['model'] = self.model
                        state['transcription_configuration_sha256'] = self.options.fingerprint
                except (ValueError, AttributeError, OSError):
                    raise PipelineError('Resume manifest is invalid or configuration changed; use a new output folder.') from None

            audio = job / 'audio.wav'
            stages = [('conversion', 'audio.wav', lambda: prepare_audio(source, audio, timeout=self.media_timeout))]
            if not extract_only:
                if transcriber is None:
                    raise PipelineError('A configured transcriber is required for the full pipeline.')
                stages.append(('transcription', 'transcription.txt', lambda: write_private(
                    job / 'transcription.txt', transcriber.transcribe(str(audio), prepared=True))))
                if enhance:
                    stages.append(('enhancement', 'derivative_readability.txt', lambda: self._write_derivative(
                        job, transcriber, state['stages']['transcription']['sha256'])))

            summary = {stage: 'pending' for stage, _, _ in stages}
            for stage, filename, operation in stages:
                configuration = self.options.fingerprint if stage == 'transcription' else None
                transcription_sha256 = None
                if stage == 'enhancement':
                    transcription_sha256 = digest(job / 'transcription.txt')
                    if transcription_sha256 != state['stages']['transcription']['sha256']:
                        summary[stage] = 'failed'
                        raise PipelineError('Raw transcript changed before editing; use a new output folder.', stages=summary)
                    configuration = self._enhancement_fingerprint(transcription_sha256)
                if self.resume and self._verified(job, state, stage, filename, configuration, transcription_sha256):
                    summary[stage] = 'skipped'
                    self.progress(stage, 'skipped')
                    continue
                if os.path.lexists(job / filename):
                    summary[stage] = 'failed'
                    raise PipelineError('Unverified output exists; use a new output folder. No artifact was overwritten.', stages=summary)
                try:
                    self.progress(stage, 'running')
                    operation()
                    # Detect changes during preparation before accepting any outputs.
                    if stage == 'conversion' and digest(source) != source_hash:
                        raise PipelineError('Source changed during conversion; use a stable source and a new output folder.')
                    state['stages'][stage] = {'status': 'complete', 'sha256': digest(job / filename)}
                    if configuration:
                        state['stages'][stage]['configuration_sha256'] = configuration
                    if transcription_sha256:
                        state['stages'][stage]['transcription_sha256'] = transcription_sha256
                except Exception as error:
                    state['stages'][stage] = {'status': 'failed'}
                    self._save(manifest, state)
                    summary[stage] = 'failed'
                    message = str(error) if type(error) in (MediaError, PipelineError, TranscriptionError) else 'Stage failed; check media validity, output access, and free space.'
                    raise PipelineError(message, stages=summary) from None
                self._save(manifest, state)
                summary[stage] = 'complete'
                self.progress(stage, 'complete')
            if not extract_only and self.author_options is not None:
                try:
                    run_author_stages(job, state, transcriber, self.author_options,
                                      resume=self.resume,
                                      save=lambda: self._save(manifest, state), summary=summary,
                                      progress=self.progress)
                except AuthorWorkflowError as error:
                    raise PipelineError(str(error), stages=summary) from None
            return identity, summary
        finally:
            lock.unlink()

    @staticmethod
    def _save(manifest, state):
        write_private(manifest, json.dumps(state, indent=2, sort_keys=True) + '\n', replace=True)
