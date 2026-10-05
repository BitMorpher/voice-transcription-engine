"""Explicit per-entry attribution settings, separate from immutable staging."""

from ..interview_attribution import InterviewOptions, DIARIZATION_MODEL
from ..model_config import ModelConfigurationError
from ..ordered_interview import _local, _read_json
from .plan import BatchError, require

SAFE_SPEAKERS = 'Invalid private speaker configuration; use version 1, known entry IDs, optional display names and scoped confirmed mappings.'


def configurations(path, plan, model=DIARIZATION_MODEL):
    try:
        entries = []
        if path is not None:
            document = _read_json(_local(path))
            require(isinstance(document, dict) and set(document) == {'version', 'interviews'}
                    and type(document['version']) is int and document['version'] == 1
                    and isinstance(document['interviews'], list), SAFE_SPEAKERS)
            entries = document['interviews']
        result = {item['id']: InterviewOptions(None, None, model=model, allow_unnamed=True)
                  for item in plan['interviews']}
        seen = set()
        for item in entries:
            require(isinstance(item, dict) and {'id'} <= set(item)
                    and not set(item) - {'id', 'enabled', 'interviewer_name', 'interviewee_name', 'speaker_map'}
                    and isinstance(item['id'], str) and item['id'] in result and item['id'] not in seen
                    and type(item.get('enabled', True)) is bool
                    and isinstance(item.get('speaker_map', []), list), SAFE_SPEAKERS)
            seen.add(item['id'])
            if not item.get('enabled', True):
                require(not item.get('speaker_map') and item.get('interviewer_name') is None
                        and item.get('interviewee_name') is None, SAFE_SPEAKERS)
                result[item['id']] = None
            else:
                result[item['id']] = InterviewOptions(item.get('interviewer_name'), item.get('interviewee_name'),
                    tuple(item.get('speaker_map', [])), model, allow_unnamed=True)
        return result
    except (OSError, ValueError, KeyError, TypeError, ModelConfigurationError):
        raise BatchError(SAFE_SPEAKERS) from None
