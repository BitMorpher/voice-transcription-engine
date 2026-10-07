"""Synthetic comparison safety, actual counters, replay, and Unicode validation."""

import hashlib
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src import studio_cli, text_comparison as comparison
from src.private_output import digest


def run(tmp_path, *, raw=None, turns=None, cases=None, **options):
    fixture, fixture_turns = comparison.synthetic_fixture()
    return comparison.run_comparison(
        fixture if raw is None else raw, fixture_turns if turns is None else turns,
        tmp_path / 'output', cases or comparison.DEFAULT_CASES, **options)


def case_directory(tmp_path, stage):
    return next((tmp_path / 'output').glob(f'synthetic/{stage}/*/*/*/*'))


def test_default_fixture_reduces_requests_and_preserves_every_turn(tmp_path):
    report = run(tmp_path)
    assert report['provider_mode'] == 'synthetic'
    assert all(case['status'] == 'complete' for case in report['cases'])
    assert {case['model'] for case in report['cases']} == {'gpt-6.1-sol', 'gpt-6-astra'}
    grouped = report['cases'][-1]
    assert grouped['request_comparison'] == {
        'unbatched_nonempty_turn_requests': 103, 'planned_group_requests': 4,
        'measured_sdk_operations': 4, 'measured_reduction_fraction': 0.961165,
    }
    assert grouped['validation']['empty_or_whitespace_turns'] == 25
    assert grouped['validation']['synthetic_exact_text_preserved']
    assert grouped['validation']['word_symbol_fidelity_valid']
    for case in report['cases']:
        assert case['metrics']['usage_complete'] is False
        assert case['metrics']['usage_responses'] == 0
        assert case['metrics']['usage_missing'] == case['metrics']['sdk_operations_started']
        assert case['metrics']['sdk_internal_retries_observed'] is False
    reviews = [case for case in report['cases'] if case['stage'] == 'review']
    assert all(case['validation']['findings'] == 65 for case in reviews)
    assert all(case['validation']['exact_source_valid'] for case in reviews)
    assert all(case['validation']['complete_source_acknowledgement'] for case in reviews)
    assert all(case['validation']['human_quality_assessed'] is False for case in report['cases'])


def test_resume_revalidates_all_checkpoints_and_reuses_no_provider_operations(tmp_path):
    first = run(tmp_path)
    before = {path: path.read_bytes() for path in (tmp_path / 'output').rglob('*')
              if path.is_file() and path.name in {'binding.json', 'source.txt', 'completed.json',
                                                 'response.json', 'derivative_readability.txt',
                                                 'author_review.json'}}
    provider = MagicMock()
    provider.chat.completions.create.side_effect = AssertionError('Must use validated checkpoints.')
    resumed = run(tmp_path, resume=True, client=provider)
    provider.chat.completions.create.assert_not_called()
    assert all(case['completed_artifact_reused'] for case in resumed['cases'])
    assert all(case['metrics']['sdk_operations_started'] == 0 for case in resumed['cases'])
    assert [case['metrics']['cache_hits'] for case in resumed['cases']] == [2, 2, 2, 4]
    assert resumed['cases'][-1]['request_comparison']['measured_reduction_fraction'] is None
    assert first['source_sha256'] == resumed['source_sha256']
    assert all(path.read_bytes() == body for path, body in before.items())


def test_reuse_requires_explicit_resume_and_never_overwrites(tmp_path):
    run(tmp_path)
    prohibited = MagicMock()
    with pytest.raises(comparison.ComparisonError):
        run(tmp_path, client=prohibited)
    prohibited.chat.completions.create.assert_not_called()
    assert not (tmp_path / 'output' / '.comparison.lock').exists()


def test_all_existing_cases_preflight_before_new_provider_calls(tmp_path):
    run(tmp_path)
    last = case_directory(tmp_path, 'attributed-polish')
    response = next((last / 'checkpoints').rglob('response.json'))
    response.write_text('{"content":"PRIVATE_SENTINEL_TAMPER"}', encoding='utf-8')
    provider = MagicMock()
    # Force the earlier case to need a provider request, while a later case is corrupt.
    earlier = case_directory(tmp_path, 'polish')
    import shutil
    shutil.rmtree(earlier / 'artifacts')
    shutil.rmtree(earlier / 'checkpoints')
    with pytest.raises(Exception):
        run(tmp_path, resume=True, client=provider)
    provider.chat.completions.create.assert_not_called()


def test_recomputed_artifact_hash_does_not_bypass_fidelity(tmp_path):
    run(tmp_path, cases=[comparison.DEFAULT_CASES[0]])
    directory = case_directory(tmp_path, 'polish')
    target = directory / 'artifacts' / 'derivative_readability.txt'
    target.write_text('Invented synthetic content.', encoding='utf-8')
    completed = directory / 'artifacts' / 'completed.json'
    manifest = json.loads(completed.read_text())
    manifest['artifact_sha256'] = digest(target)
    completed.write_text(json.dumps(manifest), encoding='utf-8')
    provider = MagicMock()
    with pytest.raises(comparison.ComparisonError):
        run(tmp_path, cases=[comparison.DEFAULT_CASES[0]], resume=True, client=provider)
    provider.chat.completions.create.assert_not_called()


def test_source_and_settings_create_independent_case_directories(tmp_path):
    polish = comparison.DEFAULT_CASES[0]
    run(tmp_path, raw='Synthetic one.', cases=[polish])
    run(tmp_path, raw='Synthetic two.', cases=[polish])
    run(tmp_path, raw='Synthetic one.', cases=[comparison.ComparisonCase('polish', polish.model, 'medium')])
    source_files = list((tmp_path / 'output').rglob('source.txt'))
    assert len(source_files) == 3
    assert {path.read_text() for path in source_files} == {'Synthetic one.', 'Synthetic two.'}


def test_crlf_source_and_polish_artifact_replay_are_byte_exact(tmp_path):
    raw = 'Synthetic first line.\r\nSynthetic second line.\r\n'
    case = comparison.DEFAULT_CASES[0]
    run(tmp_path, raw=raw, cases=[case])
    directory = case_directory(tmp_path, 'polish')
    assert (directory / 'source.txt').read_bytes() == raw.encode()
    assert (directory / 'artifacts' / 'derivative_readability.txt').read_bytes() == raw.encode()
    provider = MagicMock()
    resumed = run(tmp_path, raw=raw, cases=[case], resume=True, client=provider)
    assert resumed['cases'][0]['status'] == 'complete'
    assert resumed['cases'][0]['metrics']['sdk_operations_started'] == 0
    provider.chat.completions.create.assert_not_called()


def test_preexisting_uncertainty_notice_is_preserved_as_source_text(tmp_path):
    raw = '[Speaker attribution uncertain in chunks: 1]\n\nSynthetic sentence.'
    report = run(tmp_path, raw=raw, cases=[comparison.DEFAULT_CASES[0]])
    assert report['cases'][0]['status'] == 'complete'
    assert report['cases'][0]['validation']['word_symbol_fidelity_valid']
    assert report['cases'][0]['validation']['local_uncertainty_notice'] is False


def test_input_snapshot_conflict_blocks_resume_before_requests(tmp_path):
    run(tmp_path, cases=[comparison.DEFAULT_CASES[0]])
    source = case_directory(tmp_path, 'polish') / 'source.txt'
    source.write_text('Changed private input.', encoding='utf-8')
    provider = MagicMock()
    with pytest.raises(comparison.ComparisonError):
        run(tmp_path, cases=[comparison.DEFAULT_CASES[0]], resume=True, client=provider)
    provider.chat.completions.create.assert_not_called()


def test_symlinked_input_and_parent_are_rejected(tmp_path):
    source = tmp_path / 'source.txt'
    source.write_text('Synthetic input.', encoding='utf-8')
    link = tmp_path / 'link.txt'
    link.symlink_to(source)
    with pytest.raises(comparison.ComparisonError):
        comparison._read_text(link)
    alias = tmp_path / 'alias'
    alias.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(comparison.ComparisonError):
        comparison._read_text(alias / 'source.txt')


def test_output_symlink_and_lock_fail_closed(tmp_path):
    real = tmp_path / 'real'
    real.mkdir()
    alias = tmp_path / 'output'
    alias.symlink_to(real, target_is_directory=True)
    with pytest.raises(ValueError):
        run(tmp_path)
    alias.unlink()
    alias.mkdir()
    (alias / '.comparison.lock').write_text('', encoding='utf-8')
    with pytest.raises(comparison.ComparisonError):
        run(tmp_path)


def test_interrupt_retains_checkpoints_and_unlocks(tmp_path):
    class InterruptingProvider(comparison.SyntheticClient):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def _create(self, **parameters):
            self.calls += 1
            if self.calls == 2:
                raise KeyboardInterrupt()
            return super()._create(**parameters)

    raw, turns = comparison.synthetic_fixture(64)
    case = comparison.ComparisonCase('attributed-polish', 'gpt-6.1-sol', 'low')
    with pytest.raises(KeyboardInterrupt):
        run(tmp_path, raw=raw, turns=turns, cases=[case], client=InterruptingProvider())
    assert not (tmp_path / 'output' / '.comparison.lock').exists()
    resumed = run(tmp_path, raw=raw, turns=turns, cases=[case], resume=True)
    assert resumed['cases'][0]['status'] == 'complete'
    assert resumed['cases'][0]['metrics']['cache_hits'] == 1
    assert resumed['cases'][0]['metrics']['sdk_operations_started'] == 1


def test_validation_recovery_counts_operations_and_sdk_usage(tmp_path):
    class MalformedOnce(comparison.SyntheticClient):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def _create(self, **parameters):
            self.calls += 1
            response = super()._create(**parameters)
            if self.calls == 1:
                response.choices[0].message.content = '{}'
            response.usage = SimpleNamespace(
                prompt_tokens=101, completion_tokens=21, total_tokens=122,
                completion_tokens_details=SimpleNamespace(reasoning_tokens=11),
                prompt_tokens_details=SimpleNamespace(cached_tokens=31))
            return response

    report = run(tmp_path, raw='Synthetic sentence.', cases=[comparison.DEFAULT_CASES[0]],
                 provider_retries=1, client=MalformedOnce())
    metrics = report['cases'][0]['metrics']
    assert metrics['sdk_operations_started'] == 2
    assert metrics['validation_retries'] == 1
    assert metrics['prompt_tokens'] == 202
    assert metrics['completion_tokens'] == 42
    assert metrics['reasoning_tokens'] == 22
    assert metrics['cached_prompt_tokens'] == 62
    assert metrics['usage_complete']


def test_exact_unicode_quote_failure_is_reported_without_leaking_text(tmp_path):
    class WrongQuote(comparison.SyntheticClient):
        def _create(self, **parameters):
            response = super()._create(**parameters)
            payload = json.loads(response.choices[0].message.content)
            for finding in payload['findings']:
                if finding['excerpt'].startswith('दिन'):
                    finding['excerpt'] = finding['excerpt'].replace('दिन', 'दीन')
            response.choices[0].message.content = json.dumps(payload)
            return response

    raw = 'दिन café क़ 가 ΐ 👩\u200d💻 €100.'
    case = comparison.DEFAULT_CASES[1]
    report = run(tmp_path, raw=raw, cases=[case], client=WrongQuote())
    result = report['cases'][0]
    assert result['status'] == 'failed'
    assert result['validation']['validation_source_failures'] == 1
    assert result['validation']['complete_source_acknowledgement'] is False
    assert raw not in json.dumps(report, ensure_ascii=False)
    assert not (case_directory(tmp_path, 'review') / 'artifacts').exists()


def test_request_limit_includes_review_operations(tmp_path):
    report = run(tmp_path, cases=[comparison.DEFAULT_CASES[1]], max_provider_requests=1)
    result = report['cases'][0]
    assert result['status'] == 'incomplete'
    assert result['metrics']['sdk_operations_started'] == 1
    assert result['validation']['complete_source_acknowledgement'] is False


def test_private_artifacts_have_owner_only_permissions(tmp_path):
    run(tmp_path, cases=[comparison.DEFAULT_CASES[0]])
    for path in (tmp_path / 'output').rglob('*'):
        if path.is_file():
            assert path.stat().st_mode & 0o077 == 0


def test_live_opt_in_requires_explicit_text_and_grouped_fixture_never_goes_live(tmp_path, capsys):
    with pytest.raises(SystemExit) as error:
        comparison.main(['--send-to-openai'])
    assert error.value.code == 2
    source = tmp_path / 'PRIVATE_SOURCE_NAME.txt'
    source.write_text('PRIVATE_TRANSCRIPT_SENTINEL', encoding='utf-8')
    with pytest.raises(SystemExit):
        comparison.main(['--send-to-openai', '--text-file', str(source), '--case',
                         'attributed-polish:gpt-6.1-sol:low'])
    output = capsys.readouterr()
    assert 'PRIVATE_SOURCE_NAME' not in output.out + output.err
    assert 'PRIVATE_TRANSCRIPT_SENTINEL' not in output.out + output.err


def test_unified_command_forwards_verbatim_and_returns_child_status(monkeypatch):
    child = MagicMock(return_value=130)
    monkeypatch.setattr(comparison, 'main', child)
    arguments = ['--output-folder', 'PRIVATE_OUTPUT', '--resume']
    assert studio_cli.main(['compare-text', *arguments]) == 130
    child.assert_called_once_with(arguments)


def test_unified_help_reaches_comparison_parser(capsys):
    with pytest.raises(SystemExit) as error:
        studio_cli.main(['compare-text', '--help'])
    assert error.value.code == 0
    output = capsys.readouterr()
    assert 'usage: interview compare-text ' in output.out
    assert 'synthetic local provider' in output.out
    assert output.err == ''


@pytest.mark.parametrize('arguments', [
    ['--case', 'PRIVATE_STAGE:PRIVATE_MODEL:PRIVATE_EFFORT'],
    ['--request-timeout', 'PRIVATE_TIMEOUT'], ['--chunk-bytes', 'PRIVATE_BYTES'],
    ['--PRIVATE_UNKNOWN', 'PRIVATE_VALUE'],
])
def test_invalid_cli_arguments_never_echo_supplied_values(arguments, capsys):
    with pytest.raises(SystemExit) as error:
        comparison.main(arguments)
    assert error.value.code == 2
    output = capsys.readouterr()
    assert 'PRIVATE_' not in output.out + output.err


def test_real_provider_is_never_created_for_default_offline_case(tmp_path, monkeypatch, capsys):
    original = comparison.Transcriber
    def transcriber(**kwargs):
        assert isinstance(kwargs.get('client'), (comparison.SyntheticClient, comparison._PreflightClient))
        return original(**kwargs)
    monkeypatch.setattr(comparison, 'Transcriber', transcriber)
    assert comparison.main(['--output-dir', str(tmp_path / 'output')]) == 0
    output = capsys.readouterr()
    assert 'The synthetic treasurer' not in output.out + output.err
    assert str(tmp_path) not in output.out + output.err
    report = json.loads(output.out)
    assert report['provider_mode'] == 'synthetic'


def test_live_case_would_initialize_only_after_all_preflight(tmp_path, monkeypatch):
    raw = 'Synthetic source.'
    case = comparison.DEFAULT_CASES[1]
    # Test explicit live mode through an injected synthetic provider, never the network.
    report = run(tmp_path, raw=raw, cases=[case], mode='openai', client=comparison.SyntheticClient())
    assert report['provider_mode'] == 'openai'
    binding_path = next((tmp_path / 'output').glob('openai/review/*/*/*/*/binding.json'))
    binding_path.write_text('{}', encoding='utf-8')
    original = comparison.Transcriber
    def prohibited(**kwargs):
        if kwargs.get('client') is None:
            raise AssertionError('Live client must not initialize before preflight.')
        return original(**kwargs)
    monkeypatch.setattr(comparison, 'Transcriber', prohibited)
    with pytest.raises(comparison.ComparisonError):
        run(tmp_path, raw=raw, cases=[case], mode='openai', resume=True)


@pytest.mark.parametrize('body', [b'', b' \n', b'\xff', b'x' * 65537])
def test_small_nonempty_utf8_input_bound(tmp_path, body):
    path = tmp_path / 'source.txt'
    path.write_bytes(body)
    with pytest.raises(comparison.ComparisonError):
        comparison._read_text(path)


def test_manifest_does_not_store_source_path_or_speaker_names(tmp_path):
    run(tmp_path)
    binding = json.loads((case_directory(tmp_path, 'attributed-polish') / 'binding.json').read_text())
    assert str(tmp_path) not in json.dumps(binding)
    assert 'turns_sha256' in binding and len(binding['turns_sha256']) == 64
    assert not any('text' in key or 'name' in key or 'path' in key for key in binding)
    source, _ = comparison.synthetic_fixture()
    assert binding['source_sha256'] == hashlib.sha256(source.encode()).hexdigest()
