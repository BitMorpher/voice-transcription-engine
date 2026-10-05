"""Exclusive staging, source snapshots, content verification and batch locks."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil

from ..ordered_interview import _local, _read_json
from ..private_output import digest, output_directory, write_private
from .plan import BatchError, encode, require, snapshot


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


def stage(root, item, *, hydration=False, progress):
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
        name = f'part-{index:04d}{source.suffix.lower()}'
        destination = inputs / name
        before = digest(source)
        with source.open('rb') as original, destination.open('xb') as copy:
            os.chmod(destination, 0o600)
            shutil.copyfileobj(original, copy, 1024 * 1024)
        require(snapshot(source) == part['snapshot'] and digest(source) == before
                and digest(destination) == before, 'Source changed or staging copy verification failed.')
        staged_parts.append({'id': part['id'], 'path': name, 'media_type': part['media_type']})
        ledger.append({'path': name, 'sha256': before, 'bytes': part['snapshot']['bytes']})
        progress('staging', 'complete', part=index, parts=len(parts))
    require(all(snapshot(Path(part['path'])) == part['snapshot'] for part in parts),
            'Source metadata changed during staging.')
    manifest = {'version': 1, 'interview_id': item['manifest']['interview_id'], 'parts': staged_parts}
    write_private(inputs / 'interview.json', encode(manifest))
    state = {'manifest': manifest, 'parts': ledger}
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
    expected = item['manifest']
    require(state['manifest'] == manifest and manifest['version'] == 1
            and manifest['interview_id'] == expected['interview_id']
            and len(manifest['parts']) == len(expected['parts']) == len(state['parts']),
            'Staged manifest changed.')
    for index, (part, record, original) in enumerate(zip(manifest['parts'], state['parts'], expected['parts']), 1):
        name = f'part-{index:04d}{Path(original["path"]).suffix.lower()}'
        source = directory / 'input' / name
        require(part == {'id': original['id'], 'path': name, 'media_type': original['media_type']}
                and record['path'] == name and source.stat().st_size == record['bytes']
                == original['snapshot']['bytes'] and digest(source) == record['sha256'],
                'Staged media changed; retain artifacts and use fresh staging.')
    return directory


def write_summary(root, reporter, rows, phase):
    report = {'run': reporter.run, 'phase': phase, 'items': rows,
              'processed': len(rows), 'failed': sum(row['status'] == 'failed' for row in rows),
              'blocked': sum(row['status'] == 'blocked' for row in rows),
              'interrupted': sum(row['status'] == 'interrupted' for row in rows)}
    write_private(root / ('summary-' + reporter.run + '.json'), encode(report))
    return report


def summaries(root):
    # Only summaries generated by this coordinator; return no plan, paths, IDs or content.
    result = []
    for path in sorted(root.glob('summary-*.json')):
        data = json.loads(path.read_text())
        result.append({**{key: data.get(key, 0) for key in ('processed', 'failed', 'blocked', 'interrupted')},
                       'phase': data.get('phase')})
    return result
