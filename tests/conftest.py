"""Only synthetic local media and mocked providers are allowed in tests."""

import json
import shutil
import socket
import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError('Tests must never connect to an external API.')
    monkeypatch.setattr(socket.socket, 'connect', blocked)
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)


@pytest.fixture
def provider():
    client = MagicMock()
    client.audio.transcriptions.create.return_value = SimpleNamespace(text='Synthetic transcript.')
    client.chat.completions.create.return_value = SimpleNamespace(choices=[
        SimpleNamespace(finish_reason='stop', message=SimpleNamespace(content=None, refusal=None))
    ])
    def edit(**kwargs):
        supplied = json.loads(kwargs['messages'][-1]['content'])
        base = client.chat.completions.create.return_value.choices[0]
        content = base.message.content
        if content is None:
            content = json.dumps({'chunk_index': supplied['chunk_index'], 'text': supplied['text'],
                                  'speaker_uncertain': True}, ensure_ascii=False)
        return SimpleNamespace(choices=[SimpleNamespace(
            finish_reason=base.finish_reason,
            message=SimpleNamespace(content=content, refusal=base.message.refusal))])
    client.chat.completions.create.side_effect = edit
    return client


@pytest.fixture
def synthetic_media(tmp_path):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('FFmpeg and ffprobe are required for synthetic roundtrip tests.')

    def create(name='synthetic.mp4', *, audio=True):
        path = tmp_path / name
        args = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-n']
        if path.suffix.lower() in {'.mp4', '.mov', '.mkv', '.webm', '.avi', '.m4v'}:
            args += ['-f', 'lavfi', '-i', 'color=c=blue:s=64x64:r=10:d=1.125', '-c:v', 'mpeg4']
        if audio:
            args += ['-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=16000:duration=1.125']
        # Output options must follow all input options.
        if '-c:v' in args:
            position = args.index('-c:v')
            del args[position:position + 2]
            args += ['-c:v', 'mpeg4']
        args += ['-metadata', 'title=SYNTHETIC_PRIVATE_TAG', '-metadata', 'artist=SYNTHETIC_CREATOR']
        args += [str(path)]
        subprocess.run(args, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        return path
    return create
