"""Metadata-only private batch plans and exact serial selection."""

import json

from ..ordered_interview import _identifier, _local, _read_json
from ..media import AUDIO_EXTENSIONS, MEDIA_EXTENSIONS, VIDEO_EXTENSIONS


class BatchError(ValueError):
    """Fixed, safe batch guidance."""


def require(value, message):
    if not value:
        raise BatchError(message)


def snapshot(path):
    value = path.stat()
    return {'bytes': value.st_size, 'mtime_ns': value.st_mtime_ns,
            'device': value.st_dev, 'inode': value.st_ino}


def load_plan(path):
    """Read JSON and stat sources only; never hash, probe, hydrate or open media."""
    try:
        path = _local(path)
        plan = _read_json(path)
        require(set(plan) == {'version', 'interviews'} and type(plan['version']) is int
                and plan['version'] == 1 and isinstance(plan['interviews'], list)
                and plan['interviews'], 'Use a version 1 batch plan with interviews.')
        ids, sources, items = set(), set(), []
        for position, item in enumerate(plan['interviews'], 1):
            require(isinstance(item, dict) and {'id', 'manifest'} <= set(item)
                    and not set(item) - {'id', 'manifest', 'status'}, 'Invalid batch entry fields.')
            require(_identifier(item['id']) and item['id'] not in ids,
                    'Use unique simple batch IDs.')
            require(item.get('status', 'ready') in {'ready', 'blocked'}, 'Invalid readiness status.')
            require(isinstance(item['manifest'], str) and item['manifest'], 'Supply a local manifest.')
            manifest_path = _local(path.parent / item['manifest'])
            manifest = _read_json(manifest_path)
            require(set(manifest) == {'version', 'interview_id', 'parts'}
                    and type(manifest['version']) is int and manifest['version'] == 1
                    and _identifier(manifest['interview_id'])
                    and isinstance(manifest['parts'], list) and manifest['parts'],
                    'Use version 1 ordered interview manifests.')
            part_ids, parts = set(), []
            for part in manifest['parts']:
                require(isinstance(part, dict) and {'id', 'path'} <= set(part)
                        and not set(part) - {'id', 'path', 'media_type'}
                        and _identifier(part['id']) and part['id'] not in part_ids,
                        'Invalid ordered part fields or duplicate IDs.')
                require(isinstance(part['path'], str) and part['path']
                        and '://' not in part['path'] and not part['path'].startswith('file:'),
                        'Use local media paths.')
                source = _local(manifest_path.parent / part['path'])
                kind = part.get('media_type', 'auto')
                allowed = {'audio': AUDIO_EXTENSIONS, 'video': VIDEO_EXTENSIONS,
                           'auto': MEDIA_EXTENSIONS}.get(kind, set())
                value = source.stat()
                identity = value.st_dev, value.st_ino
                require(source.is_file() and value.st_size > 0 and source.suffix.lower() in allowed
                        and identity not in sources, 'Media is missing, empty, unsupported or duplicated.')
                sources.add(identity)
                part_ids.add(part['id'])
                parts.append({**part, 'path': str(source), 'media_type': kind,
                              'snapshot': snapshot(source),
                              'dataless': bool(getattr(value, 'st_flags', 0) & 0x40000000)})
            ids.add(item['id'])
            items.append({'id': item['id'], 'position': position,
                          'status': item.get('status', 'ready'),
                          'manifest': {**manifest, 'parts': parts}})
        return {'version': 1, 'interviews': items}
    except BatchError:
        raise
    except Exception:
        raise BatchError('Cannot read batch plan metadata; check JSON schema and local source access.') from None


def select(plan, include=(), exclude=()):
    available = {item['id'] for item in plan['interviews']}
    require(set(include) <= available and set(exclude) <= available, 'Unknown batch selection.')
    items = [item for item in plan['interviews']
             if (not include or item['id'] in include) and item['id'] not in exclude]
    require(items, 'Selection is empty.')
    return items


def encode(value):
    return json.dumps(value, sort_keys=True, indent=2) + '\n'
