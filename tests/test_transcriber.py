import io
import sys
import wave
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.transcriber import ConfigurationError, Transcriber, TranscriptionError


def write_wave(path, frames=20001):
    with wave.open(str(path), 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b'\x01\x00' * frames)
    return path


def test_key_required():
    with pytest.raises(ConfigurationError, match='OPENAI_API_KEY'):
        Transcriber()


def test_client_configuration_is_bounded_and_sanitized(monkeypatch):
    factory = MagicMock()
    monkeypatch.setitem(sys.modules, 'openai', SimpleNamespace(OpenAI=factory))
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-placeholder')
    Transcriber()
    factory.assert_called_once_with(timeout=120.0, max_retries=2)
    factory.side_effect = RuntimeError('DO_NOT_LOG_CONFIGURATION')
    with pytest.raises(ConfigurationError) as error:
        Transcriber()
    assert 'DO_NOT_LOG' not in str(error.value)


@pytest.mark.parametrize('extension', ['.wav', '.mp3', '.m4a'])
def test_audio_compatibility(extension, synthetic_media, provider):
    source = synthetic_media('synthetic' + extension)
    transcriber = Transcriber(client=provider)
    assert transcriber.transcribe(str(source)) == 'Synthetic transcript.'
    call = provider.audio.transcriptions.create.call_args.kwargs
    assert call['file'][0] == 'audio.wav'
    assert call['model'] == 'gpt-transcribe'


def test_exact_chunk_sizes_order_and_final_tail(tmp_path, provider):
    source = write_wave(tmp_path / 'synthetic.wav')
    transcriber = Transcriber(client=provider)
    transcriber.max_bytes = 4096
    counts, texts = [], []
    def create(**kwargs):
        name, stream, mime = kwargs['file']
        assert name == 'audio.wav' and mime == 'audio/wav'
        data = stream.read()
        assert len(data) <= 4096
        with wave.open(io.BytesIO(data), 'rb') as chunk:
            counts.append(chunk.getnframes())
            assert len(chunk.readframes(chunk.getnframes())) == counts[-1] * 2
        text = f'Synthetic part {len(counts)}'
        texts.append(text)
        return SimpleNamespace(text=text)
    provider.audio.transcriptions.create.side_effect = create
    assert transcriber.transcribe(str(source), prepared=True) == '\n\n'.join(texts)
    assert sum(counts) == 20001
    assert len(counts) > 1 and counts[-1] < counts[0]


def test_failed_chunk_never_returns_partial_transcript(tmp_path, provider):
    source = write_wave(tmp_path / 'synthetic.wav')
    transcriber = Transcriber(client=provider)
    transcriber.max_bytes = 4096
    provider.audio.transcriptions.create.side_effect = [SimpleNamespace(text='Synthetic first part'), RuntimeError('PRIVATE_PROVIDER_ERROR')]
    with pytest.raises(TranscriptionError) as error:
        transcriber.transcribe(str(source), prepared=True)
    assert 'chunk 2' in str(error.value)
    assert 'PRIVATE_PROVIDER_ERROR' not in str(error.value)
    assert provider.audio.transcriptions.create.call_count == 2


def test_bad_response_fails(tmp_path, provider):
    provider.audio.transcriptions.create.return_value = SimpleNamespace()
    with pytest.raises(TranscriptionError):
        Transcriber(client=provider).transcribe(str(write_wave(tmp_path / 'synthetic.wav')), prepared=True)


def test_truncated_wave_never_succeeds(tmp_path, provider):
    source = write_wave(tmp_path / 'synthetic.wav')
    source.write_bytes(source.read_bytes()[:-2])
    with pytest.raises(TranscriptionError, match='ended unexpectedly'):
        Transcriber(client=provider).transcribe(str(source), prepared=True)
    provider.audio.transcriptions.create.assert_not_called()


def test_missing_audio_is_safe(provider):
    with pytest.raises(FileNotFoundError) as error:
        Transcriber(client=provider).transcribe('synthetic-missing.wav')
    assert 'synthetic-missing' not in str(error.value)


def test_enhancement_refuses_truncation(provider):
    provider.chat.completions.create.return_value.choices[0].finish_reason = 'length'
    with pytest.raises(TranscriptionError, match='incomplete'):
        Transcriber(client=provider).enhance_transcription('Synthetic transcript.')


def test_interview_opt_in_does_not_request_invention(provider):
    Transcriber(client=provider).enhance_as_interview('Synthetic monologue.')
    instructions = provider.chat.completions.create.call_args.kwargs['messages'][0]['content']
    assert 'invent questions' in instructions
    assert 'monologue' in instructions


def test_provider_child_debug_logs_are_suppressed(tmp_path, provider, capsys):
    import logging
    logger = logging.getLogger('openai.synthetic_test_child')
    logger.disabled = False
    logger.setLevel(logging.DEBUG)
    def create(**kwargs):
        logger.warning('PRIVATE_PROVIDER_PAYLOAD')
        return SimpleNamespace(text='Synthetic transcript.')
    provider.audio.transcriptions.create.side_effect = create
    Transcriber(client=provider).transcribe(str(write_wave(tmp_path / 'synthetic.wav')), prepared=True)
    captured = capsys.readouterr()
    assert 'PRIVATE_PROVIDER_PAYLOAD' not in captured.out + captured.err
    assert logger.disabled


def test_new_asr_parameters_and_generic_filename(tmp_path, provider):
    from src.model_config import TranscriptionOptions
    source = write_wave(tmp_path / 'synthetic.wav')
    options = TranscriptionOptions(context='Synthetic context.', keywords=('SyntheticTerm',), languages=('en', 'fr'))
    Transcriber(client=provider, options=options).transcribe(str(source), prepared=True)
    request = provider.audio.transcriptions.create.call_args.kwargs
    assert request['model'] == 'gpt-transcribe'
    assert request['response_format'] == 'json'
    assert request['prompt'] == 'Synthetic context.'
    assert request['extra_body'] == {'keywords': ['SyntheticTerm'], 'languages': ['en', 'fr']}
    assert request['file'][0] == 'audio.wav'
    assert 'timestamp_granularities' not in request and 'chunking_strategy' not in request


def test_duration_cap_covers_every_frame(tmp_path, provider):
    from src.model_config import TranscriptionOptions
    source = write_wave(tmp_path / 'synthetic.wav', frames=32001)
    transcriber = Transcriber(client=provider, options=TranscriptionOptions(chunk_seconds=1))
    counts = []
    def create(**kwargs):
        with wave.open(kwargs['file'][1], 'rb') as chunk:
            counts.append(chunk.getnframes())
        return SimpleNamespace(text=f'Synthetic part {len(counts)}')
    provider.audio.transcriptions.create.side_effect = create
    text = transcriber.transcribe(str(source), prepared=True)
    assert counts == [16000, 16000, 1]
    assert text.endswith('Synthetic part 3')


def test_model_error_never_falls_back_to_deprecated_asr(tmp_path, provider):
    source = write_wave(tmp_path / 'synthetic.wav')
    provider.audio.transcriptions.create.side_effect = RuntimeError('PRIVATE_API_ERROR')
    with pytest.raises(TranscriptionError):
        Transcriber(client=provider).transcribe(str(source), prepared=True)
    assert provider.audio.transcriptions.create.call_count == 1
    assert provider.audio.transcriptions.create.call_args.kwargs['model'] == 'gpt-transcribe'
