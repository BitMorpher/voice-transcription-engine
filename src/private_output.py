"""Private artifact storage shared by the pipeline and the legacy audio CLI."""

import hashlib
import os
import sys
import tempfile
from pathlib import Path


class OutputError(ValueError):
    """Safe output storage guidance."""


def digest(path):
    value = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def output_directory(path):
    """Allow repository-local artifacts only in the ignored private/ or data/ tree."""
    path = Path(path).absolute()
    # macOS exposes its standard temporary roots through system symlinks.
    # Canonicalize those two known aliases, while rejecting arbitrary symlinks.
    if sys.platform == 'darwin':
        for alias in ('/var', '/tmp'):
            alias_path = Path(alias)
            if alias_path in path.parents and alias_path.resolve() == Path('/private' + alias):
                path = Path('/private' + alias) / path.relative_to(alias_path)
                break
    if path.is_symlink() or path.resolve() != path:
        raise OutputError('Output directories must not use symlinks or parent-directory traversal.')
    for ancestor in (path, *path.parents):
        if (ancestor / '.git').exists():
            relative = path.relative_to(ancestor)
            if not relative.parts or relative.parts[0] not in {'private', 'data'}:
                raise OutputError('Inside a repository, store artifacts under ignored private/ or data/.')
            break
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)
    return path


def write_private(path, text, *, replace=False):
    """Publish complete UTF-8 text with owner-only access; default never overwrites."""
    path = Path(path)
    if path.is_symlink():
        raise OutputError('Refusing a symlink output.')
    fd, temporary = tempfile.mkstemp(prefix='.write-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            os.replace(temporary, path)
        else:
            os.link(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
