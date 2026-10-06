"""Private atomic request checkpoints, distinct from completed recording artifacts."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

if __package__:
    from .model_config import _fingerprint
    from .private_output import digest, output_directory, write_private
else:
    from model_config import _fingerprint
    from private_output import digest, output_directory, write_private


class ChunkCacheError(ValueError):
    def __init__(self):
        super().__init__('Request checkpoint changed or is incomplete; retain it and use fresh output. No request was made for this checkpoint.')


def _read(path):
    if path.is_symlink() or not path.is_file():
        raise ChunkCacheError()
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ChunkCacheError()
            result[key] = value
        return result
    return json.loads(path.read_bytes().decode('utf-8'), object_pairs_hook=unique)


def descriptor(index, chunk):
    return {'index': index, 'total': chunk.total_chunks,
            'offset_seconds': chunk.offset_seconds, 'duration_seconds': chunk.duration_seconds,
            'wav_sha256': hashlib.sha256(chunk.getbuffer()).hexdigest()}


class ChunkCache:
    """Preflight every saved request before the first new provider call.

    Binding covers source bytes, normalized audio, settings, and exact WAV bytes
    including their offsets and total coverage. Responses never enter event logs.
    An absent checkpoint is unattempted; an incomplete/conflicting one fails closed.
    The full binding is stored once; new chunk manifests reference its fingerprint.
    Version-1 chunk manifests remain readable without rewriting saved responses.
    """
    def __init__(self, root, binding, layout, validate):
        self.binding = {'contract': 1, **binding, 'layout': layout}
        self.identity = _fingerprint(self.binding)
        self.root = Path(root) / self.identity
        self.layout, self.validate = layout, validate
        self.saved = {}
        self._binding_signature = None
        try:
            if any(parent.is_symlink() for parent in (self.root, *self.root.parents)):
                raise ChunkCacheError()
            if self.root.exists():
                expected = {'binding.json', *(f'chunk-{record["index"]:06d}' for record in layout)}
                if any(path.name not in expected and not path.name.startswith('.checkpoint-')
                       for path in self.root.iterdir()):
                    raise ChunkCacheError()
                self._check_binding()
                for record in layout:
                    path = self.path(record)
                    if os.path.lexists(path):
                        self.saved[record['index']] = self._load(record)
        except (OSError, ValueError, KeyError, TypeError, UnicodeError):
            raise ChunkCacheError() from None

    def path(self, record):
        return self.root / f'chunk-{record["index"]:06d}'

    def _check_binding(self):
        path = self.root / 'binding.json'
        if not os.path.lexists(path):
            if self._binding_signature is not None:
                raise ChunkCacheError()
            return False  # Legacy checkpoints embed the binding in each manifest.
        if path.is_symlink() or not path.is_file():
            raise ChunkCacheError()
        state = path.stat()
        signature = (state.st_dev, state.st_ino, state.st_size, state.st_mtime_ns, state.st_ctime_ns)
        # Re-read changed files, but avoid parsing the full layout for every chunk.
        if signature != self._binding_signature:
            if _read(path) != {'version': 1, 'binding': self.binding}:
                raise ChunkCacheError()
            self._binding_signature = signature
        return True

    def _load(self, record):
        path = self.path(record)
        if path.is_symlink() or not path.is_dir() or {p.name for p in path.iterdir()} != {'manifest.json', 'response.json'}:
            raise ChunkCacheError()
        state = _read(path / 'manifest.json')
        body = _read(path / 'response.json')
        shared_binding = self._check_binding()
        if not isinstance(state, dict):
            raise ChunkCacheError()
        if state.get('version') == 1:
            expected = {'version': 1, 'binding': self.binding, 'chunk': record,
                        'response_sha256': digest(path / 'response.json')}
        else:
            if not shared_binding:
                raise ChunkCacheError()
            expected = {'version': 2, 'binding_sha256': self.identity, 'chunk': record,
                        'response_sha256': digest(path / 'response.json')}
        if state != expected:
            raise ChunkCacheError()
        self.validate(body, record['duration_seconds'])
        return body

    def get(self, record):
        if record != self.layout[record['index'] - 1]:
            raise ChunkCacheError()
        if record['index'] in self.saved:
            try:
                body = self._load(record)
                if body != self.saved[record['index']]:
                    raise ChunkCacheError()
                return body
            except (OSError, ValueError, KeyError, TypeError, UnicodeError):
                raise ChunkCacheError() from None
        return None

    def put(self, record, body):
        self.validate(body, record['duration_seconds'])
        parent = output_directory(self.root)
        if not self._check_binding():
            write_private(parent / 'binding.json', json.dumps(
                {'version': 1, 'binding': self.binding}, sort_keys=True) + '\n')
            self._check_binding()
        temporary = Path(tempfile.mkdtemp(prefix='.checkpoint-', dir=parent))
        try:
            text = json.dumps(body, ensure_ascii=False, sort_keys=True) + '\n'
            write_private(temporary / 'response.json', text)
            state = {'version': 2, 'binding_sha256': self.identity, 'chunk': record,
                     'response_sha256': hashlib.sha256(text.encode()).hexdigest()}
            write_private(temporary / 'manifest.json', json.dumps(state, sort_keys=True) + '\n')
            if os.path.lexists(self.path(record)):
                raise ChunkCacheError()
            os.rename(temporary, self.path(record))
            self.saved[record['index']] = body
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)

    def verify(self):
        for record in self.layout:
            if record['index'] in self.saved:
                self.get(record)


def layout_for(chunks):
    layout = []
    for index, chunk in enumerate(chunks, 1):
        try:
            layout.append(descriptor(index, chunk))
        finally:
            chunk.close()
    return layout
