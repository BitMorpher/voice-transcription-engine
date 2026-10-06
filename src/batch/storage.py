"""Exclusive staging, source snapshots, content verification and batch locks."""

from contextlib import contextmanager
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil

from ..ordered_interview import _local, _read_json
from ..progress import safe_configuration, safe_families
from ..private_output import digest, output_directory, write_private
from .plan import BatchError, encode, require, snapshot
from .probe import probe_video, suffix_from_probe


@contextmanager
def lock(root):
    path = root / '.batch.lock'
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise BatchError('Batch is locked; check for an active run before inspecting a stale lock.') from None
    os.close(fd)
    try:
        yield
    finally:
        path.unlink()


def target(root, item):
    return root / f'item-{item["position"]:04d}'


def save_snapshot(root, plan, items):
    require(not root.exists() and not root.is_symlink(), 'Staging requires a fresh batch directory.')
    root = output_directory(root)
    document = {'plan': plan, 'selected_ids': [item['id'] for item in items]}
    write_private(root / 'batch-plan.json', encode(document))
    write_private(root / 'batch-plan.sha256', digest(root / 'batch-plan.json'))
    return root


def read_snapshot(root):
    require(root == _local(root) and root.is_dir() and not root.is_symlink(), 'Batch directory is missing or unsafe.')
    require(not (root / 'batch-plan.json').is_symlink()
            and not (root / 'batch-plan.sha256').is_symlink()
            and digest(root / 'batch-plan.json') == (root / 'batch-plan.sha256').read_text(),
            'Batch snapshot changed; use a fresh staging directory.')
    return _read_json(root / 'batch-plan.json')


def stage(root, item, *, hydration=False, progress, timeout=3600):
    require(item['status'] == 'ready', 'Entry is blocked in the private plan.')
    parts = item['manifest']['parts']
    # Check every source before creating a partial copy or hydrating an offline file.
    for part in parts:
        source = Path(part['path'])
        require(not source.is_symlink() and snapshot(source) == part['snapshot'],
                'Source changed after metadata checks; refresh the private plan.')
        require(not part['dataless'] or hydration,
                'Offline sources require --allow-hydration before staging.')
    directory = target(root, item)
    directory.mkdir(mode=0o700)
    inputs = output_directory(directory / 'input')
    staged_parts, ledger = [], []
    for index, part in enumerate(parts, 1):
        progress('staging', 'running', part=index, parts=len(parts))
        source = Path(part['path'])
        extensionless = not source.suffix
        name = f'part-{index:04d}{source.suffix.lower()}'
        destination = inputs / (f'.part-{index:04d}.pending' if extensionless else name)
        before = digest(source)
        with source.open('rb') as original, destination.open('xb') as copy:
            os.chmod(destination, 0o600)
            shutil.copyfileobj(original, copy, 1024 * 1024)
        require(snapshot(source) == part['snapshot'] and digest(source) == before
                and digest(destination) == before, 'Source changed or staging copy verification failed.')
        probe = None
        if extensionless:
            require(part['media_type'] == 'video', 'Extensionless media requires explicit video type.')
            probe = probe_video(destination, timeout)
            require(snapshot(source) == part['snapshot'] and digest(destination) == before,
                    'Source changed or staging copy verification failed.')
            name = f'part-{index:04d}{suffix_from_probe(probe)}'
            os.link(destination, inputs / name)
            destination.unlink()
        staged_parts.append({'id': part['id'], 'path': name, 'media_type': part['media_type']})
        ledger.append({'path': name, 'sha256': before, 'bytes': part['snapshot']['bytes'],
                       **({'probe': probe} if probe is not None else {})})
        progress('staging', 'complete', part=index, parts=len(parts))
    require(all(snapshot(Path(part['path'])) == part['snapshot'] for part in parts),
            'Source metadata changed during staging.')
    manifest = {'version': 1, 'interview_id': item['manifest']['interview_id'], 'parts': staged_parts}
    write_private(inputs / 'interview.json', encode(manifest))
    state = {'manifest': manifest, 'parts': ledger,
             **({'version': 2} if any('probe' in record for record in ledger) else {})}
    write_private(directory / 'staging.json', encode(state))
    # Bind staged records to the immutable batch snapshot, including original source metadata.
    write_private(directory / 'staging.sha256', digest(directory / 'staging.json'))
    verify(root, item)


def verify(root, item):
    directory = target(root, item)
    require(not directory.is_symlink() and directory.is_dir(), 'Staged entry is missing or unsafe.')
    for parent, dirs, files in os.walk(directory):
        require(all(not (Path(parent) / name).is_symlink() for name in dirs + files),
                'Symlink in staged batch; use fresh staging.')
    state = _read_json(directory / 'staging.json')
    require(digest(directory / 'staging.json') == (directory / 'staging.sha256').read_text(),
            'Staging ledger changed.')
    manifest = _read_json(directory / 'input/interview.json')
    version = state.get('version', 1)
    require(type(version) is int and version in (1, 2)
            and set(state) == ({'manifest', 'parts'} if version == 1 else {'version', 'manifest', 'parts'}),
            'Staging ledger changed.')
    expected = item['manifest']
    require(state['manifest'] == manifest and type(manifest['version']) is int and manifest['version'] == 1
            and manifest['interview_id'] == expected['interview_id']
            and len(manifest['parts']) == len(expected['parts']) == len(state['parts']),
            'Staged manifest changed.')
    for index, (part, record, original) in enumerate(zip(manifest['parts'], state['parts'], expected['parts']), 1):
        suffix = Path(original['path']).suffix.lower()
        if not suffix:
            require(version == 2 and original['media_type'] == 'video', 'Staging ledger changed.')
            suffix = suffix_from_probe(record['probe'])
        require(set(record) == ({'path', 'sha256', 'bytes', 'probe'} if not Path(original['path']).suffix
                                else {'path', 'sha256', 'bytes'}), 'Staging ledger changed.')
        name = f'part-{index:04d}{suffix}'
        source = directory / 'input' / name
        require(part == {'id': original['id'], 'path': name, 'media_type': original['media_type']}
                and record['path'] == name and source.stat().st_size == record['bytes']
                == original['snapshot']['bytes'] and digest(source) == record['sha256'],
                'Staged media changed; retain artifacts and use fresh staging.')
    return directory


def write_summary(root, reporter, rows, phase):
    report = {'version': 2, 'run': reporter.run, 'started_at': reporter.started_at,
              'configuration': reporter.configuration, 'phase': phase, 'items': rows,
              'processed': sum(row['status'] != 'not_attempted' for row in rows), 'failed': sum(row['status'] == 'failed' for row in rows),
              'blocked': sum(row['status'] == 'blocked' for row in rows),
              'interrupted': sum(row['status'] == 'interrupted' for row in rows),
              'not_attempted': sum(row['status'] == 'not_attempted' for row in rows),
              'completed': sum(row['status'] == 'complete' for row in rows),
              'incomplete': sum(row['status'] == 'incomplete' for row in rows)}
    write_private(root / ('summary-' + reporter.run + '.json'), encode(report))
    return report


def summaries(root):
    """Chronological recorded history, then latest observed stages per family.

    This is log history, not artifact verification. Legacy summaries retain their
    identity and filesystem date; their missing family data is never inferred.
    """
    history = []
    for path in root.glob('summary-*.json'):
        if path.is_symlink():
            raise BatchError('Batch snapshot changed; use a fresh staging directory.')
        data = _read_json(path)
        stamp = data.get('started_at') or datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
        history.append((stamp, path.name, data))
    result, latest = [], {}
    for stamp, _, data in sorted(history):
        base = {'historical_run': data.get('run'), 'started_at': stamp, 'phase': data.get('phase')}
        result.append({**base, **{key: data.get(key, 0) for key in
            ('processed', 'failed', 'blocked', 'interrupted', 'not_attempted', 'completed', 'incomplete')},
            'configuration': safe_configuration(data.get('configuration'))})
        for row in data.get('items', []):
            if type(row.get('item')) is not int or row['item'] < 1:
                continue
            families = safe_families(row.get('families'))
            for family, stages in families.items():
                key = row['item'], family, data.get('phase', '')
                latest[key] = {'item': row['item'], 'family': family,
                    'latest_run': data.get('run'), 'started_at': stamp, 'phase': data.get('phase'),
                    'recorded_status': ('not_attempted' if set(stages.values()) == {'not_attempted'}
                        else 'interrupted' if row.get('status') == 'interrupted'
                        and any(status in {'pending', 'running'} for status in stages.values())
                        else 'failed' if 'failed' in stages.values() else 'incomplete'
                        if any(status in {'pending', 'running', 'not_attempted', 'interrupted', 'incomplete'}
                               for status in stages.values()) else 'complete'), 'stages': stages}
    return result + [{'status': 'latest', **row} for _, row in sorted(latest.items())]
