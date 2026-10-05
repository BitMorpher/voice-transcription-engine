import json
import hashlib
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from src.pipeline import Pipeline, PipelineError
from src.private_output import digest
from src.transcriber import Transcriber


def test_full_pipeline_and_verified_resume(synthetic_media, tmp_path, provider):
    source = synthetic_media()
    output = tmp_path / 'private-output'
    transcriber = Transcriber(client=provider)
    identity, stages = Pipeline(output).process(source, transcriber=transcriber)
    assert stages == {'conversion': 'complete', 'transcription': 'complete'}
    assert (output / identity / 'transcription.txt').read_text() == 'Synthetic transcript.'
    provider.chat.completions.create.assert_not_called()
    identity2, stages = Pipeline(output, resume=True).process(source, transcriber=transcriber)
    assert identity2 == identity
    assert stages == {'conversion': 'skipped', 'transcription': 'skipped'}
    assert provider.audio.transcriptions.create.call_count == 1
    manifest = (output / identity / 'manifest.json').read_text()
    assert source.name not in manifest and str(source.parent) not in manifest
    assert 'Synthetic transcript.' not in manifest
    assert (output / identity / 'manifest.json').stat().st_mode & 0o777 == 0o600
    assert (output / identity).stat().st_mode & 0o777 == 0o700


def test_extract_then_transcribe(synthetic_media, tmp_path, provider):
    source = synthetic_media()
    output = tmp_path / 'output'
    identity, stages = Pipeline(output).process(source, extract_only=True)
    assert stages == {'conversion': 'complete'}
    assert not (output / identity / 'transcription.txt').exists()
    _, stages = Pipeline(output, resume=True).process(source, transcriber=Transcriber(client=provider))
    assert stages == {'conversion': 'skipped', 'transcription': 'complete'}


def test_retries_failed_transcription_from_converted_audio(synthetic_media, tmp_path, provider):
    source = synthetic_media()
    output = tmp_path / 'output'
    provider.audio.transcriptions.create.side_effect = RuntimeError('PRIVATE_PROVIDER_ERROR')
    transcriber = Transcriber(client=provider)
    with pytest.raises(PipelineError) as failure:
        Pipeline(output).process(source, transcriber=transcriber)
    assert failure.value.stages == {'conversion': 'complete', 'transcription': 'failed'}
    job = next(path for path in output.iterdir() if path.is_dir())
    assert not (job / 'transcription.txt').exists()
    manifest = json.loads((job / 'manifest.json').read_text())
    assert manifest['stages']['transcription'] == {'status': 'failed'}
    assert 'PRIVATE_PROVIDER_ERROR' not in json.dumps(manifest)
    provider.audio.transcriptions.create.side_effect = None
    _, stages = Pipeline(output, resume=True).process(source, transcriber=transcriber)
    assert stages == {'conversion': 'skipped', 'transcription': 'complete'}


def test_collision_safe_same_stem(synthetic_media, tmp_path):
    first, second = synthetic_media('same.mp4'), synthetic_media('same.wav')
    pipeline = Pipeline(tmp_path / 'output')
    first_id, _ = pipeline.process(first, extract_only=True)
    second_id, _ = pipeline.process(second, extract_only=True)
    assert first_id != second_id


def test_repeated_run_and_tampered_output_refuse_overwrite(synthetic_media, tmp_path):
    source = synthetic_media()
    output = tmp_path / 'output'
    identity, _ = Pipeline(output).process(source, extract_only=True)
    with pytest.raises(PipelineError, match='already exists'):
        Pipeline(output).process(source, extract_only=True)
    target = output / identity / 'audio.wav'
    target.write_bytes(b'synthetic tampered bytes')
    with pytest.raises(PipelineError, match='Unverified output'):
        Pipeline(output, resume=True).process(source, extract_only=True)
    assert target.read_bytes() == b'synthetic tampered bytes'


def test_changed_source_gets_new_identity(synthetic_media, tmp_path):
    source = synthetic_media()
    pipeline = Pipeline(tmp_path / 'output')
    first_id, _ = pipeline.process(source, extract_only=True)
    source.write_bytes(source.read_bytes() + b'synthetic harmless trailer')
    second_id, _ = pipeline.process(source, extract_only=True)
    assert first_id != second_id


def test_model_change_refuses_resume(synthetic_media, tmp_path, provider):
    source = synthetic_media()
    output = tmp_path / 'output'
    Pipeline(output).process(source, transcriber=Transcriber(client=provider))
    with pytest.raises(PipelineError, match='configuration changed'):
        Pipeline(output, resume=True, model='whisper-1').process(source, extract_only=True)


def test_locked_job_cannot_run(synthetic_media, tmp_path):
    source = synthetic_media()
    output = tmp_path / 'output'
    identity, _ = Pipeline(output).process(source, extract_only=True)
    (output / identity / '.lock').touch()
    with pytest.raises(PipelineError, match='locked'):
        Pipeline(output, resume=True).process(source, extract_only=True)


def test_enhancement_adds_label_preserves_original(synthetic_media, tmp_path, provider):
    output = tmp_path / 'output'
    identity, _ = Pipeline(output).process(synthetic_media(), transcriber=Transcriber(client=provider), enhance=True)
    job = output / identity
    assert (job / 'transcription.txt').read_text() == 'Synthetic transcript.'
    assert (job / 'derivative_readability.txt').read_text().startswith('AI readability derivative;')


def test_deleted_output_regenerates_verified_stage(synthetic_media, tmp_path, provider):
    source = synthetic_media()
    output = tmp_path / 'output'
    transcriber = Transcriber(client=provider)
    identity, _ = Pipeline(output).process(source, transcriber=transcriber)
    (output / identity / 'transcription.txt').unlink()
    _, stages = Pipeline(output, resume=True).process(source, transcriber=transcriber)
    assert stages == {'conversion': 'skipped', 'transcription': 'complete'}


def test_failed_enhancement_resumes_without_another_transcription(synthetic_media, tmp_path, provider):
    source = synthetic_media()
    output = tmp_path / 'output'
    transcriber = Transcriber(client=provider)
    provider.chat.completions.create.return_value.choices[0].finish_reason = 'length'
    with pytest.raises(PipelineError) as failure:
        Pipeline(output).process(source, transcriber=transcriber, enhance=True)
    assert failure.value.stages == {'conversion': 'complete', 'transcription': 'complete', 'enhancement': 'failed'}
    provider.chat.completions.create.return_value.choices[0].finish_reason = 'stop'
    _, stages = Pipeline(output, resume=True).process(source, transcriber=transcriber, enhance=True)
    assert stages == {'conversion': 'skipped', 'transcription': 'skipped', 'enhancement': 'complete'}
    assert provider.audio.transcriptions.create.call_count == 1


def test_invalid_manifest_and_symlink_artifact_fail_closed(synthetic_media, tmp_path):
    source = synthetic_media()
    output = tmp_path / 'output'
    identity, _ = Pipeline(output).process(source, extract_only=True)
    manifest = output / identity / 'manifest.json'
    original = manifest.read_text()
    manifest.write_text('{not valid JSON')
    with pytest.raises(PipelineError, match='invalid'):
        Pipeline(output, resume=True).process(source, extract_only=True)
    manifest.write_text(original)
    audio = output / identity / 'audio.wav'
    outside = tmp_path / 'outside.wav'
    audio.rename(outside)
    audio.symlink_to(outside)
    with pytest.raises(PipelineError, match='Unverified output'):
        Pipeline(output, resume=True).process(source, extract_only=True)


def test_mismatched_transcriber_model_is_rejected(tmp_path, provider):
    source = tmp_path / 'synthetic.wav'
    source.touch()
    with pytest.raises(PipelineError, match='must match'):
        Pipeline(tmp_path / 'output').process(source, transcriber=Transcriber(model_name='whisper-1', client=provider))
    provider.audio.transcriptions.create.assert_not_called()


def test_context_change_preserves_existing_raw_transcript(synthetic_media, tmp_path, provider):
    from src.model_config import TranscriptionOptions
    source = synthetic_media()
    output = tmp_path / 'output'
    original = TranscriptionOptions(keywords=('SyntheticFirst',))
    identity, _ = Pipeline(output, options=original).process(source, transcriber=Transcriber(client=provider, options=original))
    raw = output / identity / 'transcription.txt'
    before = raw.read_bytes()
    changed = TranscriptionOptions(keywords=('SyntheticSecond',))
    with pytest.raises(PipelineError, match='configuration changed'):
        Pipeline(output, resume=True, options=changed).process(source, transcriber=Transcriber(client=provider, options=changed))
    assert raw.read_bytes() == before
    assert provider.audio.transcriptions.create.call_count == 1
    manifest = (output / identity / 'manifest.json').read_text()
    assert 'SyntheticFirst' not in manifest and 'SyntheticSecond' not in manifest


def test_hints_can_be_added_after_extract_only(synthetic_media, tmp_path, provider):
    from src.model_config import TranscriptionOptions
    source = synthetic_media()
    output = tmp_path / 'output'
    Pipeline(output).process(source, extract_only=True)
    options = TranscriptionOptions(keywords=('SyntheticTerm',), languages=('en', 'fr'))
    _, stages = Pipeline(output, resume=True, options=options).process(source, transcriber=Transcriber(client=provider, options=options))
    assert stages == {'conversion': 'skipped', 'transcription': 'complete'}


def test_unfaithful_editor_never_publishes_or_modifies_raw(synthetic_media, tmp_path, provider):
    output = tmp_path / 'output'
    provider.chat.completions.create.return_value.choices[0].message.content = json.dumps({
        'chunk_index': 1, 'text': 'Interviewer: Invented question.', 'speaker_uncertain': False,
    })
    with pytest.raises(PipelineError, match='invented'):
        Pipeline(output).process(synthetic_media(), transcriber=Transcriber(client=provider), enhance=True)
    job = next(output.iterdir())
    assert (job / 'transcription.txt').read_text() == 'Synthetic transcript.'
    assert not (job / 'derivative_readability.txt').exists()


def test_editing_model_change_cannot_overwrite_derivative(synthetic_media, tmp_path, provider):
    from src.model_config import EditingOptions
    source = synthetic_media()
    output = tmp_path / 'output'
    identity, _ = Pipeline(output).process(source, transcriber=Transcriber(client=provider), enhance=True)
    derivative = output / identity / 'derivative_readability.txt'
    before = derivative.read_bytes()
    editor = EditingOptions(model='gpt-6.1-sol')
    with pytest.raises(PipelineError, match='Unverified output'):
        Pipeline(output, resume=True, editing_options=editor).process(
            source, transcriber=Transcriber(client=provider, editing_options=editor), enhance=True)
    assert derivative.read_bytes() == before
    assert provider.audio.transcriptions.create.call_count == 1


def test_version_one_manifest_fails_without_overwriting(synthetic_media, tmp_path):
    output = tmp_path / 'output'
    source = synthetic_media()
    identity, _ = Pipeline(output).process(source, extract_only=True)
    manifest = output / identity / 'manifest.json'
    state = json.loads(manifest.read_text())
    state['version'] = 1
    manifest.write_text(json.dumps(state))
    before = manifest.read_bytes()
    with pytest.raises(PipelineError, match='configuration changed'):
        Pipeline(output, resume=True).process(source, extract_only=True)
    assert manifest.read_bytes() == before


@pytest.mark.parametrize('replacement', [
    'Synthetic replacement.', 'Synthetic transcript! ', 'synthetic transcript.',
])
def test_regenerated_raw_cannot_reuse_derivative_of_different_bytes(
        synthetic_media, tmp_path, provider, replacement):
    source = synthetic_media()
    output = tmp_path / 'output'
    transcriber = Transcriber(client=provider)
    identity, _ = Pipeline(output).process(source, transcriber=transcriber, enhance=True)
    job = output / identity
    derivative = job / 'derivative_readability.txt'
    original_derivative = derivative.read_bytes()
    (job / 'transcription.txt').unlink()
    provider.audio.transcriptions.create.return_value = SimpleNamespace(text=replacement)
    with pytest.raises(PipelineError, match='Unverified output') as failure:
        Pipeline(output, resume=True).process(source, transcriber=transcriber, enhance=True)
    assert failure.value.stages == {
        'conversion': 'skipped', 'transcription': 'complete', 'enhancement': 'failed',
    }
    assert (job / 'transcription.txt').read_text() == replacement
    assert derivative.read_bytes() == original_derivative
    assert provider.chat.completions.create.call_count == 1
    state = json.loads((job / 'manifest.json').read_text())
    assert state['stages']['enhancement']['transcription_sha256'] != digest(job / 'transcription.txt')


def test_identical_regenerated_raw_can_reuse_bound_derivative(synthetic_media, tmp_path, provider):
    source = synthetic_media()
    output = tmp_path / 'output'
    transcriber = Transcriber(client=provider)
    identity, _ = Pipeline(output).process(source, transcriber=transcriber, enhance=True)
    job = output / identity
    original = (job / 'derivative_readability.txt').read_bytes()
    (job / 'transcription.txt').unlink()
    _, stages = Pipeline(output, resume=True).process(source, transcriber=transcriber, enhance=True)
    assert stages == {'conversion': 'skipped', 'transcription': 'complete', 'enhancement': 'skipped'}
    assert (job / 'derivative_readability.txt').read_bytes() == original
    assert provider.audio.transcriptions.create.call_count == 2
    assert provider.chat.completions.create.call_count == 1
    state = json.loads((job / 'manifest.json').read_text())
    assert state['stages']['enhancement']['transcription_sha256'] == digest(job / 'transcription.txt')


@pytest.mark.parametrize('derivative_present', [True, False])
def test_unbound_legacy_enhancement_is_not_reused(
        synthetic_media, tmp_path, provider, derivative_present):
    source = synthetic_media()
    output = tmp_path / 'output'
    transcriber = Transcriber(client=provider)
    identity, _ = Pipeline(output).process(source, transcriber=transcriber, enhance=True)
    job = output / identity
    target = job / 'derivative_readability.txt'
    original = target.read_bytes()
    manifest = job / 'manifest.json'
    state = json.loads(manifest.read_text())
    # Match the pre-binding version-2 record: only editor options and output checksum.
    state['stages']['enhancement'].pop('transcription_sha256', None)
    state['stages']['enhancement']['configuration_sha256'] = transcriber.editing_options.fingerprint
    manifest.write_text(json.dumps(state))
    if derivative_present:
        before = manifest.read_bytes()
        with pytest.raises(PipelineError, match='Unverified output'):
            Pipeline(output, resume=True).process(source, transcriber=transcriber, enhance=True)
        assert target.read_bytes() == original
        assert manifest.read_bytes() == before
        assert provider.chat.completions.create.call_count == 1
    else:
        target.unlink()
        _, stages = Pipeline(output, resume=True).process(source, transcriber=transcriber, enhance=True)
        assert stages['enhancement'] == 'complete'
        state = json.loads(manifest.read_text())
        assert state['stages']['enhancement']['transcription_sha256'] == digest(job / 'transcription.txt')
    assert provider.audio.transcriptions.create.call_count == 1


def test_raw_change_during_editing_cannot_publish_derivative(synthetic_media, tmp_path, provider):
    source = synthetic_media()
    output = tmp_path / 'output'
    transcriber = Transcriber(client=provider)
    identity, _ = Pipeline(output).process(source, transcriber=transcriber)
    job = output / identity
    default_edit = provider.chat.completions.create.side_effect

    def change_raw(**kwargs):
        (job / 'transcription.txt').write_text('Synthetic changed while editing.')
        return default_edit(**kwargs)

    provider.chat.completions.create.side_effect = change_raw
    with pytest.raises(PipelineError, match='transcript changed'):
        Pipeline(output, resume=True).process(source, transcriber=transcriber, enhance=True)
    assert not (job / 'derivative_readability.txt').exists()


def test_raw_change_between_stage_checks_cannot_skip_derivative(
        monkeypatch, synthetic_media, tmp_path, provider):
    source = synthetic_media()
    output = tmp_path / 'output'
    transcriber = Transcriber(client=provider)
    identity, _ = Pipeline(output).process(source, transcriber=transcriber, enhance=True)
    raw = output / identity / 'transcription.txt'
    derivative = output / identity / 'derivative_readability.txt'
    original = derivative.read_bytes()
    changed = False

    def change_after_verification(path):
        nonlocal changed
        checksum = digest(path)
        if path == raw and not changed:
            changed = True
            raw.write_text('Synthetic changed between stages.')
        return checksum

    monkeypatch.setattr('src.pipeline.digest', change_after_verification)
    with pytest.raises(PipelineError, match='transcript changed') as failure:
        Pipeline(output, resume=True).process(source, transcriber=transcriber, enhance=True)
    assert failure.value.stages['enhancement'] == 'failed'
    assert derivative.read_bytes() == original
    assert provider.chat.completions.create.call_count == 1


@pytest.mark.parametrize('derivative_present', [True, False])
def test_symbol_preservation_contract_invalidates_bound_legacy_derivative(
        synthetic_media, tmp_path, provider, derivative_present):
    source = synthetic_media()
    output = tmp_path / 'output'
    provider.audio.transcriptions.create.return_value = SimpleNamespace(text='Pay €100.')
    transcriber = Transcriber(client=provider)
    identity, _ = Pipeline(output).process(source, transcriber=transcriber, enhance=True)
    job = output / identity
    target = job / 'derivative_readability.txt'
    # Simulate a checksum-valid, raw-bound derivative accepted by contract 2.
    target.write_text(target.read_text().replace('€', '$'))
    manifest = job / 'manifest.json'
    state = json.loads(manifest.read_text())
    legacy_editor = hashlib.sha256(json.dumps({
        'faithful_editing_contract': 2, **asdict(transcriber.editing_options),
    }, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    legacy_binding = hashlib.sha256(json.dumps({
        'contract': 1, 'editing_configuration_sha256': legacy_editor,
        'transcription_sha256': digest(job / 'transcription.txt'),
    }, sort_keys=True).encode()).hexdigest()
    state['stages']['enhancement']['configuration_sha256'] = legacy_binding
    state['stages']['enhancement']['sha256'] = digest(target)
    manifest.write_text(json.dumps(state))
    before = target.read_bytes()
    if derivative_present:
        with pytest.raises(PipelineError, match='Unverified output'):
            Pipeline(output, resume=True).process(source, transcriber=transcriber, enhance=True)
        assert target.read_bytes() == before
        assert provider.chat.completions.create.call_count == 1
    else:
        target.unlink()
        _, stages = Pipeline(output, resume=True).process(source, transcriber=transcriber, enhance=True)
        assert stages['enhancement'] == 'complete'
        assert '€100' in target.read_text() and '$100' not in target.read_text()
        assert provider.chat.completions.create.call_count == 2
    assert provider.audio.transcriptions.create.call_count == 1


def test_expected_source_hash_refuses_new_job_before_conversion(
        synthetic_media, tmp_path, provider, monkeypatch):
    from unittest.mock import MagicMock
    source = synthetic_media()
    expected = digest(source)
    source.write_bytes(source.read_bytes() + b'synthetic changed trailer')
    conversion = MagicMock(side_effect=AssertionError('Must not convert changed part'))
    monkeypatch.setattr('src.pipeline.prepare_audio', conversion)
    output = tmp_path / 'output'
    with pytest.raises(PipelineError, match='changed after validation'):
        Pipeline(output).process(source, transcriber=Transcriber(client=provider),
                                 expected_source_sha256=expected)
    conversion.assert_not_called()
    provider.audio.transcriptions.create.assert_not_called()
    assert list(output.iterdir()) == []


def test_source_rechecked_immediately_before_asr(synthetic_media, tmp_path, provider):
    source = synthetic_media()
    expected = digest(source)
    output = tmp_path / 'output'
    identity, _ = Pipeline(output).process(source, extract_only=True,
                                          expected_source_sha256=expected)
    def change_before_asr(stage, status):
        if (stage, status) == ('transcription', 'running'):
            source.write_bytes(source.read_bytes() + b'synthetic changed trailer')
    with pytest.raises(PipelineError, match='changed after validation'):
        Pipeline(output, resume=True, progress=change_before_asr).process(
            source, transcriber=Transcriber(client=provider), expected_source_sha256=expected)
    provider.audio.transcriptions.create.assert_not_called()
    assert not (output / identity / 'transcription.txt').exists()
