"""Offline checks for explicit text presets, effort propagation and legacy reuse."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.author_review import ReviewError, ReviewOptions, review_transcript, validate_review_report
from src.batch import runner
from src.batch.cli import parser as batch_parser
from src.cli import CURRENT, main, transcription_parser
from src.model_config import EditingOptions, TranscriptionOptions, text_profile_settings


@pytest.mark.parametrize('build,base', [(transcription_parser, ['--input', 'unused']),
                                       (batch_parser, ['run'])])
def test_text_defaults_preserve_existing_models_and_fingerprints(build, base):
    args = build().parse_args(base)
    assert args.text_profile == 'legacy'
    assert args.editing_model == args.author_model == 'gpt-6-astra'
    assert args.editing_reasoning_effort == args.review_reasoning_effort == 'high'
    assert EditingOptions(model=args.editing_model,
                          reasoning_effort=args.editing_reasoning_effort) == EditingOptions()
    assert ReviewOptions(model=args.author_model,
                         reasoning_effort=args.review_reasoning_effort) == ReviewOptions()
    assert TranscriptionOptions().fingerprint == TranscriptionOptions(chunk_seconds=300).fingerprint


@pytest.mark.parametrize('build,base', [(transcription_parser, ['--input', 'unused']),
                                       (batch_parser, ['run'])])
@pytest.mark.parametrize('preset_first', [True, False])
def test_balanced_profile_is_opt_in_and_explicit_overrides_win(build, base, preset_first):
    balanced = build().parse_args([*base, '--text-profile', 'balanced'])
    assert balanced.editing_model == balanced.author_model == 'gpt-6.1-sol'
    assert balanced.editing_reasoning_effort == 'low'
    assert balanced.review_reasoning_effort == 'medium'
    profile = ['--text-profile', 'balanced']
    override = ['--author-model', 'gpt-6-astra', '--author-reasoning-effort', 'high',
                '--editing-reasoning-effort', 'medium']
    args = build().parse_args([*base, *(profile + override if preset_first else override + profile)])
    assert args.author_model == 'gpt-6-astra' and args.review_reasoning_effort == 'high'
    assert args.editing_model == 'gpt-6.1-sol' and args.editing_reasoning_effort == 'medium'


def test_equivalent_explicit_settings_have_same_stage_fingerprints():
    settings = text_profile_settings('balanced')
    assert EditingOptions(model=settings['editing_model'],
                          reasoning_effort=settings['editing_reasoning_effort']).fingerprint == (
        EditingOptions(model='gpt-6.1-sol', reasoning_effort='low').fingerprint)
    assert ReviewOptions(model=settings['author_model'],
                         reasoning_effort=settings['review_reasoning_effort']).fingerprint == (
        ReviewOptions(model='gpt-6.1-sol', reasoning_effort='medium').fingerprint)
    assert EditingOptions(reasoning_effort='low').fingerprint != EditingOptions().fingerprint
    assert ReviewOptions(reasoning_effort='medium').fingerprint != ReviewOptions().fingerprint


@pytest.mark.parametrize('build,base', [(transcription_parser, ['--input', 'unused']),
                                       (batch_parser, ['run'])])
@pytest.mark.parametrize('flag', ['--editing-reasoning-effort', '--review-reasoning-effort',
                                 '--author-reasoning-effort', '--text-profile'])
def test_invalid_effort_or_profile_never_logs_supplied_value(build, base, flag, capsys):
    with pytest.raises(SystemExit) as error:
        build().parse_args([*base, flag, 'SYNTHETIC_PRIVATE_VALUE'])
    assert error.value.code == 2
    captured = capsys.readouterr()
    assert 'SYNTHETIC_PRIVATE_VALUE' not in captured.out + captured.err


def test_review_effort_requires_author_workflow(monkeypatch):
    prohibited = MagicMock(side_effect=AssertionError('No provider or input work allowed.'))
    monkeypatch.setattr('src.cli.Transcriber', prohibited)
    monkeypatch.setattr('src.cli.load_hints', prohibited)
    with pytest.raises(SystemExit) as error:
        main(['--pipeline', '--input', 'unused', '--author-reasoning-effort', 'medium'])
    assert error.value.code == 2
    prohibited.assert_not_called()


def test_direct_cli_propagates_efforts_to_polish_review_and_chapters(monkeypatch, tmp_path):
    source = tmp_path / 'synthetic.wav'
    source.touch()
    pipeline = MagicMock()
    pipeline.process.return_value = ('synthetic-job', {'transcription': 'complete'})
    pipeline_factory = MagicMock(return_value=pipeline)
    transcriber_factory = MagicMock()
    monkeypatch.setattr('src.cli.Pipeline', pipeline_factory)
    monkeypatch.setattr('src.cli.Transcriber', transcriber_factory)
    monkeypatch.setattr('src.cli.require_ffmpeg', lambda: None)
    assert main(['--author-workflow', '--input', str(source), '--chapter-style', 'narrative',
                 '--text-profile', 'balanced', '--output-folder', str(tmp_path / 'output')]) == 0
    parameters = pipeline_factory.call_args.kwargs
    assert parameters['editing_options'] == EditingOptions(model='gpt-6.1-sol', reasoning_effort='low')
    author = parameters['author_options']
    assert author.review_options == ReviewOptions(model='gpt-6.1-sol', reasoning_effort='medium')
    assert author.chapter_options.model == 'gpt-6.1-sol'
    assert author.chapter_options.reasoning_effort == 'medium'
    assert transcriber_factory.call_args.kwargs['editing_options'] == parameters['editing_options']


def test_batch_runner_forwards_profile_and_efforts_and_gates_matching_review(monkeypatch, tmp_path):
    args = batch_parser().parse_args(['run', '--text-profile', 'balanced', '--step', 'review'])
    args.interview_options_by_id = {}
    monkeypatch.setattr(runner, 'verify', lambda *unused: tmp_path)
    gate = MagicMock(return_value=None)
    child = MagicMock(return_value=0)
    monkeypatch.setattr(runner, 'gate', gate)
    monkeypatch.setattr(runner, 'engine_main', child)
    reporter = SimpleNamespace(context={'scope': 'batch'}, heartbeat=30, emit=MagicMock())
    token = CURRENT.set(reporter)
    try:
        assert runner.run_one(tmp_path, {'id': 'synthetic-entry'}, args) == 'complete'
    finally:
        CURRENT.reset(token)
    forwarded = transcription_parser().parse_args(child.call_args.args[0])
    assert forwarded.text_profile == 'balanced'
    assert forwarded.editing_model == forwarded.author_model == 'gpt-6.1-sol'
    assert forwarded.editing_reasoning_effort == 'low'
    assert forwarded.review_reasoning_effort == 'medium'
    assert gate.call_args.args[4].review_options == ReviewOptions(
        model='gpt-6.1-sol', reasoning_effort='medium')


def test_review_report_records_effort_with_legacy_report_compatibility():
    client = MagicMock()
    def response(**kwargs):
        payload = json.loads(kwargs['messages'][-1]['content'])
        result = {'chunk_index': payload['chunk_index'], 'fully_reviewed': True,
                  'reviewed_start': payload['core_start'], 'reviewed_end': payload['core_end'],
                  'findings': []}
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(
            content=json.dumps(result), refusal=None))])
    client.chat.completions.create.side_effect = response
    raw = 'Synthetic café and देवनागरी testimony.'
    options = ReviewOptions(model='gpt-6.1-sol', reasoning_effort='medium')
    report = review_transcript(raw, client, options)
    assert report['reasoning_effort'] == 'medium'
    assert client.chat.completions.create.call_args.kwargs['extra_body']['reasoning_effort'] == 'medium'
    validate_review_report(raw, report, options)
    report['reasoning_effort'] = 'high'
    with pytest.raises(ReviewError):
        validate_review_report(raw, report, options)
    del report['reasoning_effort']
    validate_review_report(raw, report, options)
