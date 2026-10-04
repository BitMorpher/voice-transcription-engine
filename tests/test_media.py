import json
import subprocess
import wave
from pathlib import Path

import pytest

from src.media import MediaError, prepare_audio


def test_video_roundtrip_and_metadata(synthetic_media, tmp_path):
    source = synthetic_media('synthetic ; $(noop).MP4')
    target = tmp_path / 'private' / 'audio.wav'
    prepare_audio(source, target)
    with wave.open(str(target), 'rb') as result:
        assert (result.getnchannels(), result.getsampwidth(), result.getframerate()) == (1, 2, 16000)
        assert abs(result.getnframes() / 16000 - 1.125) < 0.1
    raw = subprocess.check_output(['ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(target)])
    assert b'SYNTHETIC_PRIVATE_TAG' not in raw
    assert b'SYNTHETIC_CREATOR' not in raw
    assert 'video' not in [stream['codec_type'] for stream in json.loads(raw)['streams']]
    assert target.stat().st_mode & 0o777 == 0o600


def test_no_audio_stream(synthetic_media, tmp_path):
    source = synthetic_media('silent.mp4', audio=False)
    with pytest.raises(MediaError, match='no audio stream'):
        prepare_audio(source, tmp_path / 'audio.wav')
    assert not (tmp_path / 'audio.wav').exists()


def test_corrupt_input(tmp_path):
    source = tmp_path / 'corrupt.mp4'
    source.write_bytes(b'synthetic invalid media')
    with pytest.raises(MediaError, match='valid and decodable'):
        prepare_audio(source, tmp_path / 'audio.wav')


def test_never_overwrites(synthetic_media, tmp_path):
    source = synthetic_media()
    target = tmp_path / 'audio.wav'
    target.write_bytes(b'preserve me')
    with pytest.raises(MediaError, match='already exists'):
        prepare_audio(source, target)
    assert target.read_bytes() == b'preserve me'


def test_missing_ffmpeg(monkeypatch, tmp_path):
    source = tmp_path / 'synthetic.mp4'
    source.touch()
    monkeypatch.setattr('src.media.shutil.which', lambda _: None)
    with pytest.raises(MediaError, match='FFmpeg and ffprobe'):
        prepare_audio(source, tmp_path / 'audio.wav')


def test_timeout_is_sanitized(monkeypatch, tmp_path):
    source = tmp_path / 'personal-source.mp4'
    source.touch()
    monkeypatch.setattr('src.media.require_ffmpeg', lambda: None)
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired('sensitive command', 1, stderr='sensitive provider details')
    monkeypatch.setattr('src.media.subprocess.run', timeout)
    with pytest.raises(MediaError) as error:
        prepare_audio(source, tmp_path / 'audio.wav', timeout=1)
    assert 'timed out' in str(error.value)
    assert 'personal' not in str(error.value)
    assert 'sensitive' not in str(error.value)


def test_conversion_failure_cleans_partial_output(monkeypatch, tmp_path):
    source = tmp_path / 'synthetic.mp4'
    source.touch()
    monkeypatch.setattr('src.media.require_ffmpeg', lambda: None)
    def run(command, timeout, **kwargs):
        if command[0] == 'ffprobe':
            return b'{"streams":[{"codec_type":"audio"}]}'
        Path(command[-1]).write_bytes(b'partial synthetic bytes')
        raise MediaError('Synthetic failure.')
    monkeypatch.setattr('src.media._run', run)
    with pytest.raises(MediaError):
        prepare_audio(source, tmp_path / 'audio.wav')
    assert not (tmp_path / 'audio.wav').exists()
    assert not list(tmp_path.glob('.prepare-*'))


@pytest.mark.parametrize('name', ['unsupported.txt', 'missing.mp4'])
def test_invalid_input(name, tmp_path):
    with pytest.raises(MediaError):
        prepare_audio(tmp_path / name, tmp_path / 'audio.wav')


def test_symlink_input_rejected(tmp_path):
    source = tmp_path / 'synthetic.mp4'
    source.touch()
    link = tmp_path / 'link.mp4'
    link.symlink_to(source)
    with pytest.raises(MediaError, match='symlink'):
        prepare_audio(link, tmp_path / 'audio.wav')


def test_oversized_wav_rejected_before_reading(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from src.media import validate_wave
    target = tmp_path / 'oversized.wav'
    target.touch()
    original = Path.stat
    def size(path, *args, **kwargs):
        if path == target:
            return SimpleNamespace(st_size=2**32)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'stat', size)
    with pytest.raises(MediaError, match='format limit'):
        validate_wave(target)
