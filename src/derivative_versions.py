"""Keep source-bound text configurations without replacing earlier artifacts."""

import copy
import hashlib
import json
import re
from pathlib import Path

if __package__:
    from .private_output import digest
    from .text_editing import words, split_text
else:
    from private_output import digest
    from text_editing import words, split_text


READABILITY_PREFIX = ('AI readability derivative; verify against transcription.txt.\n'
                      'Speaker identities and turn boundaries are unverified; no roles are inferred.\n\n')
# Instruction + schema digest shipped by main at 9c107410 (faithful contract 3).
# Preserve its existing cache identity; a revised contract must use a new identity.
LEGACY_READABILITY_PROMPT_SHA256 = '57f2cc1928b1c9223cc005d6d1afe8e1a8e70408c8456c74ee9a9c8cea1fa915'


def editing_prompt_hash():
    if __package__:
        from . import transcriber as engine
    else:
        import transcriber as engine
    return hashlib.sha256(json.dumps({'instruction': engine.FAITHFUL_INSTRUCTION,
                                     'schema': engine.schema(1)}, sort_keys=True,
                                    ensure_ascii=False).encode('utf-8')).hexdigest()


def readability_speech(payload, raw, chunk_bytes=None):
    """Validate the exact local uncertainty banner separately from spoken words."""
    if not payload.startswith(READABILITY_PREFIX):
        raise ValueError()
    text = payload[len(READABILITY_PREFIX):]
    if words(text) == words(raw):
        return text
    if text.startswith('[Speaker attribution uncertain in chunks: '):
        match = re.match(r'\[Speaker attribution uncertain in chunks: ([1-9][0-9]*(?:, [1-9][0-9]*)*)\]\n\n', text)
        if match is None:
            raise ValueError()
        indexes = [int(value) for value in match[1].split(', ')]
        maximum = len(list(split_text(raw, chunk_bytes))) if chunk_bytes is not None else len(raw)
        if indexes != sorted(set(indexes)) or indexes[-1] > maximum:
            raise ValueError()
        text = text[match.end():]
    if words(text) != words(raw):
        raise ValueError()
    return text


def enhancement_path(job, record=None):
    """Legacy files remain at their original path; later versions are additive."""
    name = (record or {}).get('artifact', 'derivative_readability.txt')
    path = Path(name)
    if (not isinstance(name, str) or path.is_absolute() or '..' in path.parts
            or len(path.parts) not in (1, 2) or path.name != 'derivative_readability.txt'
            or any((job / Path(*path.parts[:index])).is_symlink()
                   for index in range(1, len(path.parts) + 1))):
        raise ValueError()
    return job / path


def _intact(job, stage, record, raw_hash):
    if not isinstance(record, dict):
        raise ValueError()
    if record.get('status') != 'complete':
        return
    if not re.fullmatch(r'[a-f0-9]{64}', record.get('configuration_sha256', '')):
        raise ValueError()
    if stage == 'enhancement':
        path = enhancement_path(job, record)
        if not path.exists() and not path.is_symlink():
            # Missing bytes are never reused. An ordinary validated edit may
            # rebuild them from source/chunk checkpoints without replacing a file.
            record['status'] = 'missing'
            return
        if (record.get('transcription_sha256') != raw_hash
                or not path.is_file() or digest(path) != record.get('sha256')):
            raise ValueError()
        payload = path.read_bytes().decode('utf-8')
        raw_path = job / 'transcription.txt'
        raw_bytes = raw_path.read_bytes()
        if raw_path.is_symlink() or hashlib.sha256(raw_bytes).hexdigest() != raw_hash:
            raise ValueError()
        raw = raw_bytes.decode('utf-8')
        if payload.startswith(READABILITY_PREFIX):
            readability_speech(payload, raw, record.get('chunk_bytes'))
        else:
            # Import only at verification time, after the workflow modules are
            # initialized. Both supported derivative kinds require source fidelity.
            if __package__:
                from .interview_attribution import AttributedInterview
                from .author_workflow import _unique_object
            else:
                from interview_attribution import AttributedInterview
                from author_workflow import _unique_object
            provenance_path = job / 'provenance.json'
            if provenance_path.is_symlink():
                raise ValueError()
            provenance = json.loads(provenance_path.read_bytes().decode('utf-8'), object_pairs_hook=_unique_object)
            if record.get('legacy_turn_layout') is True:
                AttributedInterview._restore_legacy_polish(payload, raw, provenance)
            else:
                AttributedInterview._validate_polish(payload, raw, provenance)
        return
    if record.get('transcription_sha256') != raw_hash:
        raise ValueError()
    artifacts = record.get('artifacts')
    if not isinstance(artifacts, dict) or not artifacts:
        raise ValueError()
    parents = set()
    names = set()
    for name, expected in artifacts.items():
        if not isinstance(name, str):
            raise ValueError()
        path = Path(name)
        if (path.is_absolute() or '..' in path.parts or len(path.parts) != 2
                or (job / path.parts[0]).is_symlink() or (job / path).is_symlink()
                or not (job / path).is_file() or digest(job / path) != expected):
            raise ValueError()
        parents.add(path.parts[0])
        names.add(path.name)
    if len(parents) != 1 or len(names) != len(artifacts):
        raise ValueError()
    if stage == 'author_review' and names != {'review_report.json', 'review_report.xlsx'}:
        raise ValueError()
    if stage == 'chapters' and 'chapter_drafts.json' not in names:
        raise ValueError()


def select_version(job, state, stage, configuration, raw_hash):
    """Select in-memory state, checking current and archived bytes first.

    Callers persist this projection under their existing job lock. Read-only
    preflights pass a copied state. No artifact is renamed or overwritten.
    """
    versions = state.setdefault('derivative_versions', {}).setdefault(stage, {})
    if not isinstance(versions, dict):
        raise ValueError()
    current = state['stages'].get(stage)
    for key, record in versions.items():
        if (not isinstance(key, str) or not isinstance(record, dict)
                or record.get('configuration_sha256') != key):
            raise ValueError()
        _intact(job, stage, record, raw_hash)
    if current is not None:
        _intact(job, stage, current, raw_hash)
        previous = current.get('configuration_sha256')
        if previous is not None:
            if previous in versions and versions[previous] != current:
                raise ValueError()
            versions[previous] = copy.deepcopy(current)
    selected = versions.get(configuration)
    if selected is not None:
        state['stages'][stage] = copy.deepcopy(selected)
    elif current is not None and current.get('configuration_sha256') == configuration:
        state['stages'][stage] = current
    else:
        state['stages'].pop(stage, None)
    return state['stages'].get(stage)


def remember_version(state, stage):
    """Record each completed/failed attempt after ordinary stage validation."""
    record = state['stages'].get(stage, {})
    configuration = record.get('configuration_sha256')
    if configuration is not None:
        state.setdefault('derivative_versions', {}).setdefault(stage, {})[configuration] = copy.deepcopy(record)


def next_enhancement_path(job, configuration):
    """Use the established path for a first output and additive paths thereafter."""
    legacy = job / 'derivative_readability.txt'
    if not legacy.exists() and not legacy.is_symlink():
        return legacy
    return enhancement_path(job, {'artifact': f'enhancement_{configuration}/derivative_readability.txt'})
