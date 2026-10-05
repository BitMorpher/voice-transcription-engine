"""Bounded local container validation on an authorized private staging copy."""

import json
import subprocess
import tempfile

from .plan import BatchError, require

SAFE_PROBE = 'Staged video could not be validated; retain partial staging and use a fresh batch after correcting the input.'
SUFFIXES = {'iso_bmff': '.mp4', 'matroska': '.mkv', 'avi': '.avi'}


def suffix_from_probe(record):
    require(isinstance(record, dict) and set(record) == {'contract', 'container_family', 'staged_suffix', 'has_audio', 'has_video'}
            and type(record['contract']) is int and record['contract'] == 1
            and isinstance(record['container_family'], str) and record['container_family'] in SUFFIXES
            and record['staged_suffix'] == SUFFIXES[record['container_family']]
            and record['has_audio'] is True and record['has_video'] is True, SAFE_PROBE)
    return record['staged_suffix']


def probe_video(path, timeout):
    # No tags, arbitrary diagnostics, URLs, external playlists or original path.
    # A temporary stdout file bounds the amount of JSON read into memory.
    try:
        with tempfile.TemporaryFile() as output:
            result = subprocess.run([
                'ffprobe', '-v', 'error', '-protocol_whitelist', 'file',
                '-format_whitelist', 'mov,matroska,webm,avi',
                '-show_entries', 'format=format_name:stream=codec_type:stream_disposition=attached_pic',
                '-of', 'json', str(path)], stdin=subprocess.DEVNULL, stdout=output,
                stderr=subprocess.DEVNULL, timeout=min(timeout, 30), check=False)
            require(result.returncode == 0, SAFE_PROBE)
            output.seek(0)
            raw = output.read(65537)
        require(len(raw) <= 65536, SAFE_PROBE)
        body = json.loads(raw)
        formats = set(body['format']['format_name'].split(','))
        if formats == {'mov', 'mp4', 'm4a', '3gp', '3g2', 'mj2'}:
            family = 'iso_bmff'
        elif formats == {'matroska', 'webm'}:
            family = 'matroska'
        elif formats == {'avi'}:
            family = 'avi'
        else:
            raise ValueError()
        streams = body['streams']
        require(isinstance(streams, list) and any(s['codec_type'] == 'audio' for s in streams)
                and any(s['codec_type'] == 'video' and s['disposition']['attached_pic'] == 0
                        for s in streams), SAFE_PROBE)
        return {'contract': 1, 'container_family': family, 'staged_suffix': SUFFIXES[family],
                'has_audio': True, 'has_video': True}
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError, AttributeError):
        raise BatchError(SAFE_PROBE) from None
