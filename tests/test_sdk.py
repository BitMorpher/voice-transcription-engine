"""Exercise the locked SDK serialization through an entirely local transport."""

import json
import logging
import wave

import httpx2
from openai import OpenAI

from src.model_config import TranscriptionOptions
from src.transcriber import Transcriber


def test_locked_sdk_audio_and_editing_contract(tmp_path):
    requests = []

    def respond(request):
        requests.append(request)
        if request.url.path == '/v1/audio/transcriptions':
            return httpx2.Response(200, json={'text': 'Synthetic transcript.'})
        assert request.url.path == '/v1/chat/completions'
        supplied = json.loads(json.loads(request.content)['messages'][-1]['content'])
        return httpx2.Response(200, json={
            'id': 'synthetic', 'object': 'chat.completion', 'created': 0,
            'model': 'gpt-6-astra',
            'choices': [{'index': 0, 'finish_reason': 'stop', 'message': {
                'role': 'assistant', 'content': json.dumps({
                    **supplied, 'speaker_uncertain': False,
                }),
            }}],
        })

    source = tmp_path / 'synthetic-local-source.wav'
    with wave.open(str(source), 'wb') as audio:
        audio.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        audio.writeframes(b'\x01\x00' * 160)
    with httpx2.Client(transport=httpx2.MockTransport(respond)) as transport:
        with OpenAI(api_key='synthetic-placeholder', http_client=transport, max_retries=0) as client:
            transcriber = Transcriber(client=client, options=TranscriptionOptions(
                context='Synthetic context', keywords=('synthetic',), languages=('en', 'fr'),
            ))
            raw = transcriber.transcribe(str(source), prepared=True)
            assert raw == 'Synthetic transcript.'
            assert transcriber.enhance_transcription(raw) == raw

    audio_body = requests[0].content
    assert b'filename="audio.wav"' in audio_body
    assert source.name.encode() not in audio_body
    for field in ('model', 'prompt', 'keywords', 'languages', 'response_format'):
        assert f'name="{field}'.encode() in audio_body
    edited_body = json.loads(requests[1].content)
    assert edited_body['model'] == 'gpt-6-astra'
    assert edited_body['reasoning_effort'] == 'high'
    assert edited_body['store'] is False
    assert edited_body['max_completion_tokens'] >= 16384
    assert edited_body['response_format']['json_schema']['strict'] is True


def test_current_sdk_transport_logs_are_suppressed(provider, caplog):
    # The OpenAI 3 SDK uses httpx2/httpcore2; nested loggers can include payloads.
    loggers = [logging.getLogger(name + '.synthetic_child') for name in ('httpx2', 'httpcore2')]
    for logger in loggers:
        logger.disabled = False
        logger.setLevel(logging.DEBUG)
    Transcriber(client=provider)
    for logger in loggers:
        logger.error('SYNTHETIC_PAYLOAD_MUST_NOT_LOG')
        assert logger.disabled
    assert 'SYNTHETIC_PAYLOAD_MUST_NOT_LOG' not in caplog.text
