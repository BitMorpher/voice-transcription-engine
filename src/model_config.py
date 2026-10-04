"""Validated model capabilities and private configuration fingerprints."""

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Tuple

DEFAULT_ASR_MODEL = 'gpt-transcribe'
DEFAULT_EDITING_MODEL = 'gpt-6-astra'
ASR_MODELS = (DEFAULT_ASR_MODEL, 'whisper-1', 'gpt-4o-transcribe', 'gpt-4o-mini-transcribe')
EDITING_MODELS = (DEFAULT_EDITING_MODEL, 'gpt-6.1-sol')


class ModelConfigurationError(ValueError):
    """Safe configuration guidance that never echoes hint content or filenames."""


def _fingerprint(value):
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class TranscriptionOptions:
    model: str = DEFAULT_ASR_MODEL
    context: str = ''
    keywords: Tuple[str, ...] = ()
    languages: Tuple[str, ...] = ()
    chunk_seconds: float = 300.0

    def __post_init__(self):
        if self.model not in ASR_MODELS:
            raise ModelConfigurationError('Unsupported ASR model; use a documented model listed in the usage guide.')
        if not isinstance(self.context, str) or len(self.context.encode('utf-8')) > 8192:
            raise ModelConfigurationError('Transcription context must be UTF-8 text of at most 8192 bytes.')
        if (not isinstance(self.keywords, tuple) or len(self.keywords) > 100
                or any(not isinstance(term, str) or not term.strip() or len(term.encode('utf-8')) > 256
                       for term in self.keywords)):
            raise ModelConfigurationError('Glossary must contain at most 100 nonempty terms, each at most 256 UTF-8 bytes.')
        if (not isinstance(self.languages, tuple) or len(self.languages) > 16
                or any(not isinstance(code, str) or not re.fullmatch(r'[a-z]{2,3}', code)
                       for code in self.languages)):
            raise ModelConfigurationError('Language hints must be lowercase ISO 639 codes of two or three letters (at most 16).')
        if self.model != DEFAULT_ASR_MODEL:
            if self.keywords:
                raise ModelConfigurationError('Keyword hints are supported only by gpt-transcribe in this CLI.')
            if len(self.languages) > 1 or any(len(code) != 2 for code in self.languages):
                raise ModelConfigurationError('Legacy ASR models accept one ISO 639-1 language hint; use gpt-transcribe for multiple languages.')
        if not isinstance(self.chunk_seconds, (int, float)) or not math.isfinite(self.chunk_seconds) or not 1 <= self.chunk_seconds <= 600:
            raise ModelConfigurationError('Audio chunk duration must be between 1 and 600 seconds.')

    @property
    def fingerprint(self):
        values = asdict(self)
        values['chunk_seconds'] = float(self.chunk_seconds)
        return _fingerprint({'contract': 1, 'max_upload_bytes': 20 * 1024 * 1024, **values})

    def request_parameters(self):
        parameters = {'model': self.model, 'response_format': 'json'}
        if self.context:
            parameters['prompt'] = self.context
        if self.model == DEFAULT_ASR_MODEL:
            hints = {}
            if self.keywords:
                hints['keywords'] = list(self.keywords)
            if self.languages:
                hints['languages'] = list(self.languages)
            if hints:
                # Current official Python examples use extra_body for these fields.
                parameters['extra_body'] = hints
        elif self.languages:
            parameters['language'] = self.languages[0]
        return parameters


@dataclass(frozen=True)
class EditingOptions:
    model: str = DEFAULT_EDITING_MODEL
    chunk_bytes: int = 6000
    reasoning_effort: str = 'high'

    def __post_init__(self):
        if self.model not in EDITING_MODELS:
            raise ModelConfigurationError('Unsupported editing model; use gpt-6-astra or gpt-6.1-sol.')
        if not isinstance(self.chunk_bytes, int) or not 64 <= self.chunk_bytes <= 6000:
            raise ModelConfigurationError('Editing chunks must be between 64 and 6000 UTF-8 bytes.')
        if self.reasoning_effort not in ('low', 'medium', 'high'):
            raise ModelConfigurationError('Editing reasoning effort must be low, medium, or high.')

    @property
    def fingerprint(self):
        return _fingerprint({'faithful_editing_contract': 3, **asdict(self)})


def _read_hint_file(path):
    try:
        source = Path(path)
        if source.is_symlink() or not source.is_file():
            raise ModelConfigurationError('Hint input must be a local regular UTF-8 text file, not a symlink.')
        with source.open('rb') as stream:
            data = stream.read(65537)
        if len(data) > 65536:
            raise ModelConfigurationError('Hint file exceeds the local 64 KiB safety limit.')
        return data.decode('utf-8')
    except (OSError, UnicodeError):
        raise ModelConfigurationError('Cannot read hint file; check local access and UTF-8 encoding.') from None


def load_hints(*, context_file=None, glossary_file=None):
    """Read only user-supplied hints; never infer glossary or language from names."""
    context = _read_hint_file(context_file).strip() if context_file else ''
    keywords = ()
    if glossary_file:
        keywords = tuple(dict.fromkeys(line.strip() for line in _read_hint_file(glossary_file).splitlines() if line.strip()))
    return context, keywords
