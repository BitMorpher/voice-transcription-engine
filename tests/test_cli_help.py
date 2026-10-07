"""Readable help and compatibility aliases must retain privacy and mode gates."""

from unittest.mock import MagicMock

import pytest

from src.batch.cli import parser as batch_parser
from src.cli import CURRENT, main, transcription_parser


SHARED_ALIASES = [
    ('--transcription-model', '--model', 'gpt-transcribe'),
    ('--review-model', '--author-model', 'gpt-6-astra'),
    ('--separate-speakers', '--interview', None),
    ('--speaker-model', '--interview-model', 'gpt-4o-transcribe-diarize'),
    ('--speaker-chunk-seconds', '--diarization-chunk-seconds', '120'),
    ('--chapter-style', '--chapters', 'both'),
    ('--logs-folder', '--log-directory', 'private/logs'),
    ('--status-interval', '--heartbeat-seconds', '15'),
    ('--request-timeout', '--provider-timeout', '90'),
    ('--request-retries', '--provider-retries', '0'),
    ('--max-requests', '--max-provider-requests', '4'),
    ('--failure-limit', '--provider-failure-limit', '3'),
]
ENGINE_ALIASES = [
    *SHARED_ALIASES,
    ('--recordings-list', '--interview-manifest', 'private/config/interview.json'),
    ('--author-workflow', '--workflow', None),
    ('--prepare-audio', '--extract-only', None),
    ('--polish-text', '--enhance-for-reading', None),
    ('--polish-text', '--enhance_for_reading', None),
    ('--steps', '--stages', 'raw,review'),
    ('--input-folder', '--input_folder', 'private/input'),
    ('--output-folder', '--output_folder', 'private/output'),
    ('--format-as-interview', '--format_as_interview', None),
]
BATCH_ALIASES = [
    *SHARED_ALIASES,
    ('--batch-plan', '--plan', 'private/config/batch.json'),
    ('--batch-folder', '--batch', 'private/batches/demo'),
    ('--download-cloud-files', '--allow-hydration', None),
    ('--step', '--phase', 'review'),
]


def options(flag, value):
    return [flag] if value is None else [flag, value]


@pytest.mark.parametrize('friendly,previous,value', ENGINE_ALIASES)
def test_engine_aliases_preserve_values_and_supplied_option_gates(friendly, previous, value):
    parser = transcription_parser()
    inputs = [] if friendly in {'--recordings-list', '--input-folder'} else ['--input', 'unused']
    new = inputs + options(friendly, value)
    old = inputs + options(previous, value)
    assert vars(parser.parse_args(new)) == vars(parser.parse_args(old))
    assert parser.supplied_options(new) == parser.supplied_options(old)


@pytest.mark.parametrize('friendly,previous,value', BATCH_ALIASES)
def test_batch_aliases_preserve_values(friendly, previous, value):
    parser = batch_parser()
    assert vars(parser.parse_args(['run', *options(friendly, value)])) == vars(
        parser.parse_args(['run', *options(previous, value)]))


@pytest.mark.parametrize('build', [transcription_parser, batch_parser])
def test_help_explains_steps_display_and_grouped_options(build):
    help_text = build().format_help()
    for text in ('Transcription models and hints', 'Speaker labels',
                 'Progress, logs, and request limits', '--progress',
                 'JSON when redirected', 'Previous option spellings remain supported'):
        assert text in help_text
    assert '--transcription-model' in help_text and '--model' in help_text
    assert '--request-timeout' in help_text and '--provider-timeout' in help_text


def test_batch_help_defines_each_action():
    help_text = batch_parser().format_help()
    for explanation in ('without reading recordings', 'fresh private batch folder',
                        'without reading original recordings', 'raw (transcribe)',
                        'results recorded by previous batch commands'):
        assert explanation in help_text


@pytest.mark.parametrize('arguments', [
    ['--pipeline', '--review-model', 'gpt-6-astra'],
    ['--pipeline', '--steps', 'raw'],
    ['--pipeline', '--chapter-style', 'both'],
    ['--pipeline', '--speaker-chunk-seconds', '120'],
    ['--pipeline', '--speaker-model', 'gpt-4o-transcribe-diarize'],
    ['--separate-speakers'],
    ['--author-workflow', '--prepare-audio'],
    ['--prepare-audio', '--polish-text'],
])
def test_friendly_aliases_cannot_bypass_mode_checks(arguments, monkeypatch, capsys):
    prohibited = MagicMock(side_effect=AssertionError('Invalid modes must stop before local/provider work.'))
    monkeypatch.setattr('src.cli.load_hints', prohibited)
    monkeypatch.setattr('src.cli.require_ffmpeg', prohibited)
    monkeypatch.setattr('src.cli.Transcriber', prohibited)
    with pytest.raises(SystemExit) as error:
        main(['--input', 'SYNTHETIC_PRIVATE_PATH', *arguments])
    assert error.value.code == 2
    prohibited.assert_not_called()
    captured = capsys.readouterr()
    assert 'SYNTHETIC_PRIVATE_PATH' not in captured.out + captured.err


@pytest.mark.parametrize('build,base', [(transcription_parser, ['--input', 'unused']),
                                       (batch_parser, ['run'])])
@pytest.mark.parametrize('argument', [
    '--separate-speakers=SYNTHETIC_SECRET',
    '--request-timeout=SYNTHETIC_SECRET',
    '--request-retries=SYNTHETIC_SECRET',
    '--progress=SYNTHETIC_SECRET',
])
def test_new_alias_parser_errors_redact_user_values(build, base, argument, capsys):
    with pytest.raises(SystemExit) as error:
        build().parse_args([*base, argument])
    assert error.value.code == 2
    captured = capsys.readouterr()
    assert 'SYNTHETIC_SECRET' not in captured.out + captured.err
    assert '--help' in captured.err


def test_nested_engine_keeps_batch_display_mode(monkeypatch):
    reporter = MagicMock()
    reporter.context = {'scope': 'batch'}
    token = CURRENT.set(reporter)
    monkeypatch.setattr('src.cli.load_hints', MagicMock(side_effect=ValueError('Fixed test failure.')))
    try:
        assert main(['--pipeline', '--input', 'unused']) == 1
        reporter.set_output.assert_not_called()
        assert main(['--pipeline', '--input', 'unused', '--progress', 'plain']) == 1
        reporter.set_output.assert_called_once_with('plain')
    finally:
        CURRENT.reset(token)


@pytest.mark.parametrize('build,base', [(transcription_parser, ['--input', 'unused']),
                                       (batch_parser, ['run'])])
def test_validation_failure_limit_default_and_explicit_setting(build, base):
    assert build().parse_args(base).validation_failure_limit == 3
    assert build().parse_args([*base, '--validation-failure-limit', '1']).validation_failure_limit == 1
    assert '--validation-failure-limit' in build().format_help()
