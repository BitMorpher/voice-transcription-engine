"""Local, metadata-stripping media preparation with bounded FFmpeg execution."""

import json
import math
import os
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

AUDIO_EXTENSIONS = frozenset({'.wav', '.mp3', '.m4a'})
VIDEO_EXTENSIONS = frozenset({'.mp4', '.mov', '.mkv', '.webm', '.avi', '.m4v'})
MEDIA_EXTENSIONS = AUDIO_EXTENSIONS | VIDEO_EXTENSIONS


class MediaError(RuntimeError):
    """A safe, actionable error; never contains media paths or subprocess output."""


def require_ffmpeg():
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        raise MediaError('FFmpeg and ffprobe are required; make both available on PATH.')


def _run(command, timeout, *, capture=False):
    # Discard raw diagnostics: they can contain source paths, tags, and media content.
    try:
        result = subprocess.run(
            command, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        raise MediaError('Media processing timed out; increase --media-timeout if needed.') from None
    except OSError:
        raise MediaError('Cannot start FFmpeg/ffprobe; check executable access and PATH.') from None
    if result.returncode:
        raise MediaError('Media processing failed; check that the local file is valid and decodable.')
    return result.stdout


def validate_wave(path):
    try:
        if Path(path).stat().st_size >= 2**32:
            raise MediaError('Prepared WAV exceeds the format limit; split the source into smaller recordings.')
        with wave.open(str(path), 'rb') as audio:
            if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, 16000):
                raise MediaError('Prepared audio has an unexpected format.')
            if audio.getnframes() == 0:
                raise MediaError('Media contains no audio samples.')
            audio.setpos(audio.getnframes() - 1)
            if len(audio.readframes(1)) != 2:
                raise MediaError('Prepared audio data is incomplete.')
    except (OSError, EOFError, wave.Error):
        raise MediaError('Prepared audio is missing or invalid.') from None


def prepare_audio(source, destination, *, timeout=3600):
    """Extract the first audio stream to 16 kHz mono PCM WAV without overwriting.

    Accept local audio or video files only. Restrict nested FFmpeg protocols to local
    files, strip global/stream metadata and chapters, then publish a complete file
    atomically. Filenames are separate argv entries; no shell is invoked.
    """
    source, destination = Path(source).absolute(), Path(destination).absolute()
    if source.suffix.lower() not in MEDIA_EXTENSIONS:
        raise MediaError('Unsupported media extension.')
    if not source.is_file() or source.is_symlink():
        raise MediaError('Input must be an accessible local regular file, not a symlink.')
    if not math.isfinite(timeout) or timeout <= 0:
        raise MediaError('Media timeout must be a positive finite number.')
    if destination.suffix.lower() != '.wav':
        raise MediaError('Prepared audio output must use the .wav extension.')
    if os.path.lexists(destination):
        raise MediaError('Audio output already exists; use a new destination or verified --resume.')
    require_ffmpeg()
    raw = _run([
        'ffprobe', '-v', 'error', '-protocol_whitelist', 'file',
        '-show_entries', 'stream=codec_type', '-of', 'json', str(source),
    ], min(timeout, 30), capture=True)
    try:
        streams = json.loads(raw)['streams']
        if not any(stream.get('codec_type') == 'audio' for stream in streams):
            raise MediaError('Media has no audio stream.')
    except (ValueError, KeyError, TypeError, AttributeError):
        raise MediaError('Could not validate media streams.') from None

    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # FFmpeg writes a new file in a private directory; a hard link provides
    # atomic, exclusive publication even if another writer creates destination.
    with tempfile.TemporaryDirectory(prefix='.prepare-', dir=destination.parent) as temporary:
        prepared = Path(temporary) / 'audio.wav'
        _run([
            'ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-n',
            '-xerror', '-protocol_whitelist', 'file', '-i', str(source),
            '-map', '0:a:0', '-vn', '-sn', '-dn', '-map_metadata', '-1',
            '-map_metadata:s:a', '-1', '-map_chapters', '-1',
            '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le',
            '-fflags', '+bitexact', '-flags:a', '+bitexact', str(prepared),
        ], timeout)
        validate_wave(prepared)
        os.chmod(prepared, 0o600)
        try:
            os.link(prepared, destination)
        except FileExistsError:
            raise MediaError('Audio output already exists; no file was overwritten.') from None
        except OSError:
            raise MediaError('Cannot save prepared audio; check output access and free space.') from None
    return destination
