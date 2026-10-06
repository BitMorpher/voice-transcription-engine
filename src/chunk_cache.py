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
    """
    def __init__(self, root, binding, layout, validate):
        self.binding = {'contract': 1, **binding, 'layout': layout}
        self.identity = _fingerprint(self.binding)
        self.root = Path(root) / self.identity
        self.layout, self.validate = layout, validate
        self.saved = {}
        try:
            if any(parent.is_symlink() for parent in (self.root, *self.root.parents)):
                raise ChunkCacheError()
            if self.root.exists():
                expected = {f'chunk-{record["index"]:06d}' for record in layout}
                if any(path.name not in expected and not path.name.startswith('.checkpoint-')
                       for path in self.root.iterdir()):
                    raise ChunkCacheError()
                for record in layout:
                    path = self.path(record)
                    if os.path.lexists(path):
                        self.saved[record['index']] = self._load(record)
        except (OSError, ValueError, KeyError, TypeError, UnicodeError):
            raise ChunkCacheError() from None

    def path(self, record):
        return self.root / f'chunk-{record["index"]:06d}'

    def _load(self, record):
        path = self.path(record)
        if path.is_symlink() or not path.is_dir() or {p.name for p in path.iterdir()} != {'manifest.json', 'response.json'}:
            raise ChunkCacheError()
        state = _read(path / 'manifest.json')
        body = _read(path / 'response.json')
        if state != {'version': 1, 'binding': self.binding, 'chunk': record,
                     'response_sha256': digest(path / 'response.json')}:
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
        temporary = Path(tempfile.mkdtemp(prefix='.checkpoint-', dir=parent))
        try:
            text = json.dumps(body, ensure_ascii=False, sort_keys=True) + '\n'
            write_private(temporary / 'response.json', text)
            state = {'version': 1, 'binding': self.binding, 'chunk': record,
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
