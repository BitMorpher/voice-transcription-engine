import pytest

from src.model_config import (
    EditingOptions,
    ModelConfigurationError,
    TranscriptionOptions,
    load_hints,
)


def test_quality_defaults_have_no_invented_hints():
    options = TranscriptionOptions()
    assert options.request_parameters() == {'model': 'gpt-transcribe', 'response_format': 'json'}
    assert EditingOptions().model == 'gpt-6-astra'
    assert EditingOptions().reasoning_effort == 'high'
    assert options.fingerprint == TranscriptionOptions(chunk_seconds=300).fingerprint


def test_gpt_transcribe_hints_use_documented_extra_body():
    options = TranscriptionOptions(context='Synthetic context.', keywords=('SyntheticTerm',), languages=('en', 'fr'))
    assert options.request_parameters() == {
        'model': 'gpt-transcribe', 'response_format': 'json', 'prompt': 'Synthetic context.',
        'extra_body': {'keywords': ['SyntheticTerm'], 'languages': ['en', 'fr']},
    }
    assert options.fingerprint != TranscriptionOptions().fingerprint
    assert 'SyntheticTerm' not in options.fingerprint


@pytest.mark.parametrize('model', ['whisper-1', 'gpt-4o-transcribe', 'gpt-4o-mini-transcribe'])
def test_legacy_capabilities_are_explicit(model):
    options = TranscriptionOptions(model=model, context='Synthetic context.', languages=('en',))
    assert options.request_parameters() == {
        'model': model, 'response_format': 'json', 'prompt': 'Synthetic context.', 'language': 'en',
    }
    with pytest.raises(ModelConfigurationError, match='Keyword hints'):
        TranscriptionOptions(model=model, keywords=('SyntheticTerm',))
    with pytest.raises(ModelConfigurationError, match='one ISO'):
        TranscriptionOptions(model=model, languages=('en', 'fr'))


@pytest.mark.parametrize('parameters', [
    {'model': 'undocumented-model'}, {'languages': ('EN',)}, {'languages': ('en-US',)},
    {'keywords': ('',)}, {'keywords': ('x' * 257,)}, {'context': 'x' * 8193},
    {'chunk_seconds': 0}, {'chunk_seconds': float('inf')}, {'chunk_seconds': 601},
])
def test_invalid_configuration_is_sanitized(parameters):
    with pytest.raises(ModelConfigurationError) as error:
        TranscriptionOptions(**parameters)
    assert 'undocumented-model' not in str(error.value)


def test_private_hints_are_only_user_supplied(tmp_path):
    context = tmp_path / 'context.txt'
    glossary = tmp_path / 'glossary.txt'
    context.write_text(' Synthetic context. ', encoding='utf-8')
    glossary.write_text('SyntheticTerm\n\nSyntheticTerm\nSyntheticOther\n', encoding='utf-8')
    assert load_hints(context_file=context, glossary_file=glossary) == ('Synthetic context.', ('SyntheticTerm', 'SyntheticOther'))
    assert load_hints() == ('', ())


def test_hint_errors_do_not_expose_names_or_paths(tmp_path):
    invalid = tmp_path / 'private-hint.txt'
    invalid.write_bytes(b'\xff')
    with pytest.raises(ModelConfigurationError) as error:
        load_hints(context_file=invalid)
    assert invalid.name not in str(error.value) and str(tmp_path) not in str(error.value)
    large = tmp_path / 'large.txt'
    large.write_bytes(b'x' * 65537)
    with pytest.raises(ModelConfigurationError, match='64 KiB'):
        load_hints(glossary_file=large)
    link = tmp_path / 'link.txt'
    link.symlink_to(invalid)
    with pytest.raises(ModelConfigurationError, match='symlink'):
        load_hints(context_file=link)
