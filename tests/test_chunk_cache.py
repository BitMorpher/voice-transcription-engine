"""Synthetic storage scaling, legacy recovery, and shared-binding integrity."""

import json

import pytest

from src import chunk_cache
from src.chunk_cache import ChunkCache, ChunkCacheError


def layout(count):
    return [{'index': index, 'total': count, 'offset_seconds': index - 1,
             'duration_seconds': 1, 'wav_sha256': f'{index:064x}'}
            for index in range(1, count + 1)]


def cache(root, records):
    def validate(body, duration):
        assert body == {'text': 'Synthetic words.'} and duration == 1
    return ChunkCache(root, {'source_sha256': 'a' * 64, 'settings_sha256': 'b' * 64},
                      records, validate)


def test_checkpoint_metadata_and_resume_parsing_scale_linearly(tmp_path, monkeypatch):
    sizes = []
    for count in (128, 256):
        records = layout(count)
        saved = cache(tmp_path / str(count), records)
        for record in records:
            saved.put(record, {'text': 'Synthetic words.'})
        manifests = list(saved.root.glob('chunk-*/manifest.json'))
        binding_path = saved.root / 'binding.json'
        binding = json.loads(binding_path.read_text())
        assert binding['binding']['layout'] == records
        assert binding_path.stat().st_mode & 0o777 == 0o600
        assert saved.root.stat().st_mode & 0o777 == 0o700
        assert len(manifests) == count
        for path, record in zip(sorted(manifests), records):
            state = json.loads(path.read_text())
            assert state['version'] == 2 and state['binding_sha256'] == saved.identity
            assert state['chunk'] == record and 'binding' not in state
            assert path.stat().st_size < 512
            assert path.stat().st_mode & 0o777 == 0o600
        sizes.append(binding_path.stat().st_size + sum(path.stat().st_size for path in manifests))

        reads = []
        original_read = chunk_cache._read
        with monkeypatch.context() as patch:
            def read(path):
                reads.append(path)
                return original_read(path)
            patch.setattr(chunk_cache, '_read', read)
            resumed = cache(tmp_path / str(count), records)
            for record in records:
                assert resumed.get(record) == {'text': 'Synthetic words.'}
            resumed.verify()
        assert reads.count(binding_path) == 1
        assert len(reads) == 1 + 6 * count  # response/manifest per chunk in each pass
    assert sizes[1] < 2.1 * sizes[0]


def test_legacy_checkpoints_resume_without_rewriting_and_accept_compact_additions(tmp_path):
    records = layout(3)
    saved = cache(tmp_path, records)
    legacy = {}
    for record in records[:2]:
        saved.put(record, {'text': 'Synthetic words.'})
        path = saved.path(record) / 'manifest.json'
        state = json.loads(path.read_text())
        state.pop('binding_sha256')
        state.update(version=1, binding=saved.binding)
        path.write_text(json.dumps(state))
        legacy[path] = path.read_bytes()
        response = path.with_name('response.json')
        legacy[response] = response.read_bytes()
    (saved.root / 'binding.json').unlink()

    resumed = cache(tmp_path, records)
    for record in records[:2]:
        assert resumed.get(record) == {'text': 'Synthetic words.'}
    assert not (saved.root / 'binding.json').exists()
    resumed.put(records[2], {'text': 'Synthetic words.'})
    mixed = cache(tmp_path, records)
    mixed.verify()
    assert len(mixed.saved) == 3
    assert all(path.read_bytes() == content for path, content in legacy.items())


@pytest.mark.parametrize('tamper', ['missing', 'source', 'layout', 'symlink', 'duplicate'])
def test_shared_binding_tamper_fails_preflight_and_active_reuse(tmp_path, tamper):
    records = layout(2)
    saved = cache(tmp_path / 'cache', records)
    saved.put(records[0], {'text': 'Synthetic words.'})
    path = saved.root / 'binding.json'
    state = json.loads(path.read_text())
    if tamper == 'missing':
        path.unlink()
    elif tamper == 'source':
        state['binding']['source_sha256'] = 'c' * 64
        path.write_text(json.dumps(state))
    elif tamper == 'layout':
        state['binding']['layout'][1]['offset_seconds'] += 1
        path.write_text(json.dumps(state))
    elif tamper == 'symlink':
        target = tmp_path / 'synthetic-binding.json'
        target.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(target)
    else:
        path.write_text('{"version":1,"version":1,"binding":{}}')
    with pytest.raises(ChunkCacheError):
        cache(tmp_path / 'cache', records)
    with pytest.raises(ChunkCacheError):
        saved.get(records[0])
    with pytest.raises(ChunkCacheError):
        saved.verify()
    with pytest.raises(ChunkCacheError):
        saved.put(records[1], {'text': 'Synthetic words.'})
    assert not saved.path(records[1]).exists()


@pytest.mark.parametrize('tamper', ['digest', 'chunk', 'non_object'])
def test_compact_manifest_tamper_fails_closed(tmp_path, tamper):
    records = layout(2)
    saved = cache(tmp_path, records)
    saved.put(records[0], {'text': 'Synthetic words.'})
    path = saved.path(records[0]) / 'manifest.json'
    state = json.loads(path.read_text())
    if tamper == 'digest':
        state['binding_sha256'] = 'c' * 64
    elif tamper == 'chunk':
        state['chunk']['index'] = 2
    else:
        state = []
    path.write_text(json.dumps(state))
    with pytest.raises(ChunkCacheError):
        cache(tmp_path, records)
