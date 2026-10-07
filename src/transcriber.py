"""Faithful audio transcription with exact, bounded PCM chunks and safe failures."""

import io
import json
import logging
import os
import tempfile
import wave
from pathlib import Path

if __package__:
    from .attributed_editing import (GROUP_CONTRACT, GROUP_INSTRUCTION, editing_fingerprint,
                                    grouped_turns, group_schema, validate_group, reassemble)
    from .text_requests import ResponseValidationError, TextRequestCache, text_binding, validated_chat
    from .chunk_cache import ChunkCache, descriptor, layout_for
    from .private_output import digest
    from .provider_control import CURRENT_CONTROL, ControlledClient, ProviderStopped
    from .provider_errors import classify
    from .progress import emit_progress
    from .media import AUDIO_EXTENSIONS, prepare_audio
    from .model_config import DEFAULT_ASR_MODEL, EditingOptions, TranscriptionOptions
    from .text_editing import (
        FAITHFUL_INSTRUCTION,
        EditingError,
        schema,
        split_text,
        validate_edit,
        words,
    )
else:
    from attributed_editing import (GROUP_CONTRACT, GROUP_INSTRUCTION, editing_fingerprint,
                                   grouped_turns, group_schema, validate_group, reassemble)
    from text_requests import ResponseValidationError, TextRequestCache, text_binding, validated_chat
    from chunk_cache import ChunkCache, descriptor, layout_for
    from private_output import digest
    from provider_control import CURRENT_CONTROL, ControlledClient, ProviderStopped
    from provider_errors import classify
    from progress import emit_progress
    from media import AUDIO_EXTENSIONS, prepare_audio
    from model_config import DEFAULT_ASR_MODEL, EditingOptions, TranscriptionOptions
    from text_editing import (
        FAITHFUL_INSTRUCTION,
        EditingError,
        schema,
        split_text,
        validate_edit,
        words,
    )


class TranscriptionError(RuntimeError):
    """Safe transcription failure without provider payloads or source information."""

    def __init__(self, message, *, category=None):
        super().__init__(message)
        self.validation_category = category if category in {
            'validation_schema', 'validation_coverage', 'validation_source',
            'validation_diarization', 'completion'} else None


class ConfigurationError(EnvironmentError):
    """Safe setup guidance without values from the environment."""


def _suppress_provider_logging():
    """Suppress SDK/network loggers, including already configured child loggers."""
    roots = ('openai', 'httpx', 'httpcore', 'httpx2', 'httpcore2')
    names = set(roots) | set(logging.Logger.manager.loggerDict)
    for name in names:
        if any(name == root or name.startswith(root + '.') for root in roots):
            logger = logging.getLogger(name)
            logger.disabled = True
            logger.setLevel(logging.CRITICAL + 1)


DEFAULT_UPLOAD_BYTES = 20 * 1024 * 1024

class Transcriber:
    def __init__(self, model_name=DEFAULT_ASR_MODEL, *, client=None, media_timeout=3600,
                 options=None, editing_options=None, provider_timeout=120, provider_retries=2):
        if (type(provider_timeout) not in (int, float) or not 0 < provider_timeout < float('inf')
                or type(provider_retries) is not int or not 0 <= provider_retries <= 5):
            raise ConfigurationError('Use a positive finite provider timeout and retries from 0 to 5.')
        self.options = options or TranscriptionOptions(model=model_name)
        self.editing_options = editing_options or EditingOptions()
        self.model_name = self.options.model
        self.max_bytes = DEFAULT_UPLOAD_BYTES  # Safely below the API's 25 MB limit.
        self.media_timeout = media_timeout
        if client is None:
            if not os.getenv('OPENAI_API_KEY', '').strip():
                raise ConfigurationError('Set OPENAI_API_KEY in your environment before transcribing.')
            try:
                import openai
            except ImportError:
                raise ConfigurationError('The openai SDK is required; install the project dependencies.') from None
            # SDK/network debug logging can contain authorization headers or payloads.
            _suppress_provider_logging()
            try:
                client = openai.OpenAI(timeout=provider_timeout, max_retries=provider_retries)
            except Exception:
                raise ConfigurationError('Cannot configure OpenAI; check your environment settings.') from None
        self._client = client
        self.provider_timeout, self.provider_retries = provider_timeout, provider_retries
        _suppress_provider_logging()

    @property
    def client(self):
        if CURRENT_CONTROL.get() is not None:
            return ControlledClient(self._client, self.provider_timeout, self.provider_retries)
        return self._client

    def _wave_chunks(self, audio_path, *, chunk_seconds=None):
        """Yield WAV buffers covering every frame once, each below max_bytes.

        Read one chunk at a time; no compressed-size estimation or unbounded list
        of decoded audio. Frame boundaries preserve the final sub-second tail.
        """
        with wave.open(str(audio_path), 'rb') as audio:
            frame_bytes = audio.getnchannels() * audio.getsampwidth()
            max_frames = min((self.max_bytes - 64) // frame_bytes,
                             int((self.options.chunk_seconds if chunk_seconds is None else chunk_seconds) * audio.getframerate()))
            if max_frames < 1 or audio.getnframes() < 1:
                raise TranscriptionError('Audio is empty or chunk size is invalid.')
            remaining = audio.getnframes()
            offset_frames = 0
            total_chunks = (remaining + max_frames - 1) // max_frames
            while remaining:
                count = min(max_frames, remaining)
                frames = audio.readframes(count)
                if len(frames) != count * frame_bytes:
                    raise TranscriptionError('Audio ended unexpectedly; no complete transcript was produced.')
                buffer = io.BytesIO()
                with wave.open(buffer, 'wb') as chunk:
                    chunk.setparams(audio.getparams())
                    chunk.writeframes(frames)
                if buffer.tell() > self.max_bytes:
                    raise TranscriptionError('Encoded chunk exceeds the upload limit.')
                buffer.seek(0)
                buffer.total_chunks = total_chunks
                buffer.offset_seconds = offset_frames / audio.getframerate()
                buffer.duration_seconds = count / audio.getframerate()
                buffer.name = 'audio.wav'  # Never send the personal source filename.
                yield buffer
                remaining -= count
                offset_frames += count

    def transcribe(self, audio_path: str, *, prepared=False) -> str:
        """Transcribe WAV/MP3/M4A in order; any failed chunk fails the entire stage.

        ``prepared`` is reserved for pipeline-produced mono PCM WAV. Legacy
        callers keep using ``transcribe(path)``; media is normalized locally.
        """
        source = Path(audio_path)
        if not source.is_file():
            raise FileNotFoundError('Audio input is missing or inaccessible.')
        if source.suffix.lower() not in AUDIO_EXTENSIONS:
            raise TranscriptionError('Unsupported audio extension; convert video to audio first.')
        if prepared:
            return self._transcribe_wave(source)
        with tempfile.TemporaryDirectory(prefix='voice-transcription-') as temporary:
            audio = prepare_audio(source, Path(temporary) / 'audio.wav', timeout=self.media_timeout)
            return self._transcribe_wave(audio)

    def asr_checkpoint(self, audio_path, *, checkpoint_root, source_sha256, audio_sha256):
        if digest(audio_path) != audio_sha256:
            raise TranscriptionError('Prepared audio changed before transcription.')
        def validate(body, duration):
            if not isinstance(body, dict) or set(body) != {'text'} or not isinstance(body['text'], str):
                raise ValueError()
        cache = ChunkCache(checkpoint_root, {
            'family': 'original', 'source_sha256': source_sha256, 'audio_sha256': audio_sha256,
            'configuration_sha256': self.options.fingerprint, 'max_upload_bytes': self.max_bytes,
            'chunk_seconds': float(self.options.chunk_seconds), 'response_format': 'json',
            'model': self.options.model,
        }, layout_for(self._wave_chunks(audio_path)), validate)
        return cache

    def checkpointed_transcribe(self, audio_path, *, checkpoint_root, source_sha256, audio_sha256):
        cache = self.asr_checkpoint(audio_path, checkpoint_root=checkpoint_root,
            source_sha256=source_sha256, audio_sha256=audio_sha256)
        text = self._transcribe_wave(audio_path, checkpoint=cache)
        cache.verify()
        if digest(audio_path) != audio_sha256:
            raise TranscriptionError('Prepared audio changed during transcription; no complete transcript was saved.')
        return text

    def _transcribe_wave(self, audio_path, *, checkpoint=None):
        parts = []
        try:
            for index, chunk in enumerate(self._wave_chunks(audio_path), start=1):
                emit_progress('transcription', 'running', chunk=index, chunks=chunk.total_chunks)
                try:
                    _suppress_provider_logging()
                    record = descriptor(index, chunk)
                    saved = checkpoint.get(record) if checkpoint else None
                    if saved is not None:
                        parts.append(saved['text'])
                        emit_progress('transcription', 'skipped', chunk=index, chunks=chunk.total_chunks)
                        continue
                    response = self.client.audio.transcriptions.create(
                        file=('audio.wav', chunk, 'audio/wav'), **self.options.request_parameters(),
                    )
                    text = getattr(response, 'text', None)
                    if not isinstance(text, str):
                        raise ResponseValidationError('Provider returned an invalid transcription response.')
                    if checkpoint and text.strip():
                        checkpoint.put(record, {'text': text})
                    parts.append(text)
                    emit_progress('transcription', 'complete', chunk=index, chunks=chunk.total_chunks)
                except ProviderStopped:
                    emit_progress('transcription', 'not_attempted', chunk=index, chunks=chunk.total_chunks)
                    raise
                except Exception as error:
                    emit_progress('transcription', 'failed', chunk=index, chunks=chunk.total_chunks, **classify(error))
                    raise TranscriptionError(
                        f'Transcription failed on chunk {index}; no complete transcript was saved. '
                        'Check API access, quota, and network connectivity before retrying.'
                    ) from None
                finally:
                    chunk.close()
        except (OSError, EOFError, wave.Error):
            raise TranscriptionError('Prepared audio could not be read.') from None
        return '\n\n'.join(parts)

    def _enhance(self, transcription, *, checkpoint_root=None):
        if not isinstance(transcription, str):
            raise TranscriptionError('Editing input must be transcript text.')
        if not transcription.strip():
            return transcription
        edited_parts, uncertain = [], []
        try:
            sources = list(split_text(transcription, self.editing_options.chunk_bytes))
        except UnicodeError:
            raise TranscriptionError('Editing input must be valid UTF-8 transcript text.') from None
        parameters, validators = [], []
        for index, source in enumerate(sources, 1):
            floor = {'low': 8192, 'medium': 12288, 'high': 16384}[self.editing_options.reasoning_effort]
            budget = min(32768, max(floor, len(source.encode('utf-8')) * 3 + 8192))
            parameters.append(dict(
                model=self.editing_options.model,
                messages=[{'role': 'system', 'content': FAITHFUL_INSTRUCTION},
                          {'role': 'user', 'content': json.dumps(
                              {'chunk_index': index, 'text': source}, ensure_ascii=False)}],
                response_format=schema(index),
                extra_body={'reasoning_effort': self.editing_options.reasoning_effort,
                            'max_completion_tokens': budget, 'store': False}))
            validators.append(lambda content, source=source, index=index:
                              validate_edit(content, source, index))
        try:
            cache = TextRequestCache(checkpoint_root,
                text_binding('polish', transcription, self.editing_options.fingerprint,
                             validator_contract=1), parameters, validators) if checkpoint_root else None
            for index, source in enumerate(sources, 1):
                if not source.strip():
                    edited_parts.append(source)
                    continue
                emit_progress('enhancement', 'running', chunk=index, chunks=len(sources))
                _suppress_provider_logging()
                edited, speaker_uncertain = validated_chat(self.client, parameters[index - 1],
                    validators[index - 1], stage='enhancement', cache=cache, index=index)
                edited_parts.append(edited)
                emit_progress('enhancement', 'complete', chunk=index, chunks=len(sources))
                if speaker_uncertain:
                    uncertain.append(index)
            if cache:
                cache.verify()
            combined = ''.join(edited_parts)
            if words(combined) != words(transcription):
                raise EditingError('Reassembled editing changed words, symbols, or boundaries; no derivative was saved.')
            if uncertain:
                combined = '[Speaker attribution uncertain in chunks: ' + ', '.join(map(str, uncertain)) + ']\n\n' + combined
            return combined
        except ResponseValidationError as error:
            emit_progress('enhancement', 'failed', **classify(error))
            raise TranscriptionError(str(error), category=error.category) from None
        except Exception as error:
            emit_progress('enhancement', 'failed', **classify(error))
            raise TranscriptionError('Optional editing failed; check API/model access, quota, and network connectivity. The original transcript is retained.') from None

    def enhance_transcription(self, transcription: str, *, checkpoint_root=None) -> str:
        """Opt-in punctuation/layout derivative with verified word preservation."""
        return self._enhance(transcription, checkpoint_root=checkpoint_root)

    @property
    def attributed_editing_fingerprint(self):
        return editing_fingerprint(self.editing_options)

    def enhance_attributed(self, raw, turns, *, checkpoint_root=None):
        """Edit bounded groups; preserve all source metadata and empty turns locally."""
        try:
            groups = grouped_turns(raw, turns, self.editing_options.chunk_bytes)
            parameters, validators = [], []
            for index, group in enumerate(groups, 1):
                payload = json.dumps({'group_index': index, 'turns': group}, ensure_ascii=False)
                # Full escaped schema output plus generous reasoning headroom. This
                # is a ceiling, not a spending estimate. Truncation fails closed.
                floor = {'low': 8192, 'medium': 12288, 'high': 16384}[self.editing_options.reasoning_effort]
                budget = min(65536, max(floor, len(payload.encode()) * 3 + 8192))
                parameters.append(dict(model=self.editing_options.model,
                    messages=[{'role': 'system', 'content': GROUP_INSTRUCTION},
                              {'role': 'user', 'content': payload}],
                    response_format=group_schema(index), extra_body={
                        'reasoning_effort': self.editing_options.reasoning_effort,
                        'max_completion_tokens': budget, 'store': False}))
                validators.append(lambda content, group=group, index=index:
                                  validate_group(content, group, index))
            cache = TextRequestCache(checkpoint_root,
                text_binding('attributed-polish', raw, self.attributed_editing_fingerprint,
                             validator_contract=GROUP_CONTRACT), parameters, validators) if checkpoint_root else None
            edits = []
            for index, group in enumerate(groups, 1):
                if not any(piece['text'].strip() for piece in group):
                    edits.extend({**piece, 'speaker_uncertain': False} for piece in group)
                    continue
                emit_progress('enhancement', 'running', chunk=index, chunks=len(groups))
                _suppress_provider_logging()
                edits.extend(validated_chat(self.client, parameters[index - 1], validators[index - 1],
                             stage='enhancement', cache=cache, index=index))
                emit_progress('enhancement', 'complete', chunk=index, chunks=len(groups))
            if cache:
                cache.verify()
            return reassemble(raw, turns, edits)
        except ProviderStopped:
            raise
        except ResponseValidationError as error:
            emit_progress('enhancement', 'failed', **classify(error))
            raise TranscriptionError(str(error), category=error.category) from None
        except Exception as error:
            emit_progress('enhancement', 'failed', **classify(error))
            raise TranscriptionError('Attributed editing failed; validated groups are retained for resume.') from None

    def enhance_as_interview(self, transcription: str) -> str:
        """Legacy alias for faithful layout only; never generate interviewer turns."""
        return self._enhance(transcription)
