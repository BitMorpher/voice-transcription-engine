"""Offline workflow matrix, privacy, provenance and fail-closed draft gates."""

import shutil
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.author_review import ReviewOptions
from src.author_workflow import AuthorOptions
from src.chapters import ChapterOptions
from src.cli import main
from src.pipeline import Pipeline, PipelineError
from src.private_output import digest
from src.transcriber import Transcriber


@pytest.fixture
def author_provider():
    client = MagicMock()
    client.audio.transcriptions.create.return_value = SimpleNamespace(text='Synthetic testimony.\nAnother synthetic line.')
    client.priority = None
    client.review_failure = None

    def respond(**kwargs):
        supplied = json.loads(kwargs['messages'][-1]['content'])
        name = kwargs['response_format']['json_schema']['name']
        if name == 'faithful_transcript_edit':
            body = {'chunk_index': supplied['chunk_index'], 'text': supplied['text'],
                    'speaker_uncertain': False}
        elif name == 'source_grounded_author_review':
            findings = []
            if client.priority:
                code = {'low': 'personal_opinion', 'medium': 'contextual_criticism',
                        'high': 'serious_allegation'}[client.priority]
                category = 'allegation' if client.priority == 'high' else 'criticism'
                findings = [{'category': category, 'severity': client.priority, 'reason_code': code,
                             'excerpt': supplied['text'], 'start': 0, 'end': len(supplied['text'])}]
            body = {'chunk_index': supplied['chunk_index'], 'fully_reviewed': True,
                    'reviewed_start': supplied['core_start'], 'reviewed_end': supplied['core_end'],
                    'findings': findings}
        else:
            body = {'chunk_index': supplied['chunk_index'], 'passages': [
                {'unit_ids': [unit['unit_id']], 'text': unit['text'].strip(),
                 'kind': 'verbatim_excerpt'} for unit in supplied['source_units']],
                'coverage_omissions': []}
        finish = client.review_failure if name == 'source_grounded_author_review' else 'stop'
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason=finish or 'stop',
            message=SimpleNamespace(content=json.dumps(body), refusal=None))])

    client.chat.completions.create.side_effect = respond
    return client


def artifact(job, state, stage, name):
    return next(job / path for path in state['stages'][stage]['artifacts'] if path.endswith('/' + name))


@pytest.mark.parametrize('extension', ['.wav', '.mp4'])
@pytest.mark.parametrize('stages,chapters', [
    ('raw', 'none'), ('raw,polish', 'none'), ('raw,review', 'none'),
    ('raw,polish,review', 'none'), ('raw,review', 'interview'),
    ('raw,polish,review,chapters', 'both'), ('chapters', 'narrative'),
])
def test_cli_matrix_and_resume(monkeypatch, synthetic_media, tmp_path, author_provider,
                               extension, stages, chapters, capsys):
    source = synthetic_media('synthetic' + extension)
    transcriber = Transcriber(client=author_provider)
    monkeypatch.setattr('src.cli.Transcriber', lambda **kwargs: transcriber)
    output = tmp_path / 'output'
    args = ['--workflow', '--input', str(source), '--output-folder', str(output),
            '--media-type', 'audio' if extension == '.wav' else 'video',
            '--stages', stages, '--chapters', chapters]
    assert main(args) == 0
    job = next(output.iterdir())
    state = json.loads((job / 'manifest.json').read_text())
    assert (job / 'transcription.txt').read_text() == 'Synthetic testimony.\nAnother synthetic line.'
    raw_bytes = (job / 'transcription.txt').read_bytes()
    if 'polish' in stages:
        assert (job / 'derivative_readability.txt').is_file()
    if 'review' in stages or chapters != 'none':
        report = json.loads(artifact(job, state, 'author_review', 'review_report.json').read_text())
        assert report['status'] == 'complete' and report['findings'] == []
        assert report['raw_sha256'] == hashlib.sha256(raw_bytes).hexdigest()
        assert artifact(job, state, 'author_review', 'review_report.xlsx').is_file()
    if chapters != 'none':
        chapter = json.loads(artifact(job, state, 'chapters', 'chapter_drafts.json').read_text())
        assert set(chapter['chapters']) == ({'interview', 'narrative'} if chapters == 'both' else {chapters})
        for style in chapter['chapters']:
            assert 'HUMAN REVIEW REQUIRED' in artifact(job, state, 'chapters', f'chapter_{style}.txt').read_text()
    calls = author_provider.chat.completions.create.call_count
    assert main([*args, '--resume']) == 0
    assert author_provider.chat.completions.create.call_count == calls
    assert author_provider.audio.transcriptions.create.call_count == 1
    assert (job / 'transcription.txt').read_bytes() == raw_bytes
    output_log = capsys.readouterr().out
    assert str(tmp_path) not in output_log and source.name not in output_log
    assert 'Synthetic testimony' not in output_log


def test_high_gate_preserves_report_raw_and_explicit_override(synthetic_media, tmp_path, author_provider):
    source = synthetic_media()
    author_provider.priority = 'high'
    options = AuthorOptions(chapter_options=ChapterOptions())
    output = tmp_path / 'output'
    transcriber = Transcriber(client=author_provider)
    with pytest.raises(PipelineError) as failure:
        Pipeline(output, author_options=options).process(source, transcriber=transcriber)
    assert failure.value.stages['author_review'] == 'complete'
    assert failure.value.stages['chapters'] == 'failed'
    job = next(output.iterdir())
    state = json.loads((job / 'manifest.json').read_text())
    report_path = artifact(job, state, 'author_review', 'review_report.json')
    before = report_path.read_bytes()
    raw_before = (job / 'transcription.txt').read_bytes()
    assert not list(job.glob('chapters_*'))
    _, stages = Pipeline(output, resume=True, author_options=AuthorOptions(
        chapter_options=ChapterOptions(), allow_unresolved_high=True)).process(source, transcriber=transcriber)
    assert stages['author_review'] == 'skipped' and stages['chapters'] == 'complete'
    state = json.loads((job / 'manifest.json').read_text())
    assert 'unresolved HIGH' in artifact(job, state, 'chapters', 'chapter_interview.txt').read_text()
    assert report_path.read_bytes() == before and (job / 'transcription.txt').read_bytes() == raw_before
    assert author_provider.audio.transcriptions.create.call_count == 1


@pytest.mark.parametrize('priority', ['low', 'medium'])
def test_lower_priority_allows_visible_drafts(synthetic_media, tmp_path, author_provider, priority):
    author_provider.priority = priority
    output = tmp_path / 'output'
    _, stages = Pipeline(output, author_options=AuthorOptions(chapter_options=ChapterOptions(
        styles=('interview',)))).process(synthetic_media(), transcriber=Transcriber(client=author_provider))
    assert stages['chapters'] == 'complete'
    job = next(output.iterdir())
    state = json.loads((job / 'manifest.json').read_text())
    assert 'Unresolved review findings:' in artifact(job, state, 'chapters', 'chapter_interview.txt').read_text()


def test_failed_review_retained_retried_distinct_attempt(synthetic_media, tmp_path, author_provider):
    source = synthetic_media()
    output = tmp_path / 'output'
    author_provider.review_failure = 'length'
    transcriber = Transcriber(client=author_provider)
    options = AuthorOptions(chapter_options=ChapterOptions(styles=('interview',)))
    with pytest.raises(PipelineError) as failure:
        Pipeline(output, author_options=options).process(source, transcriber=transcriber)
    assert failure.value.stages['author_review'] == 'failed'
    assert failure.value.stages['chapters'] == 'pending'
    job = next(output.iterdir())
    state = json.loads((job / 'manifest.json').read_text())
    failed = artifact(job, state, 'author_review', 'review_report.json')
    assert json.loads(failed.read_text())['status'] == 'failed'
    assert artifact(job, state, 'author_review', 'review_report.xlsx').exists()
    author_provider.review_failure = None
    _, stages = Pipeline(output, resume=True, author_options=options).process(source, transcriber=transcriber)
    assert stages['author_review'] == 'complete'
    assert failed.exists() and len(list(job.glob('author_review_*'))) == 2
    assert author_provider.audio.transcriptions.create.call_count == 1


@pytest.mark.parametrize('changed', ['settings', 'report', 'xlsx', 'raw'])
def test_resume_changed_artifacts_or_settings_refuses_without_overwrite(
        synthetic_media, tmp_path, author_provider, changed):
    source = synthetic_media()
    output = tmp_path / 'output'
    transcriber = Transcriber(client=author_provider)
    options = AuthorOptions()
    identity, _ = Pipeline(output, author_options=options).process(source, transcriber=transcriber)
    job = output / identity
    state = json.loads((job / 'manifest.json').read_text())
    report = artifact(job, state, 'author_review', 'review_report.json')
    if changed == 'settings':
        options = AuthorOptions(review_options=ReviewOptions(model='gpt-6.1-sol'))
    elif changed == 'raw':
        (job / 'transcription.txt').write_text('Synthetic changed raw.')
    else:
        target = report if changed == 'report' else artifact(job, state, 'author_review', 'review_report.xlsx')
        target.write_bytes(target.read_bytes() + b' ')
    before = report.read_bytes()
    calls = author_provider.chat.completions.create.call_count
    if changed == 'settings':
        _, stages = Pipeline(output, resume=True, author_options=options).process(source, transcriber=transcriber)
        assert stages['author_review'] == 'complete'
        assert len(list(job.glob('author_review_*'))) == 2
        assert report.read_bytes() == before
        assert author_provider.chat.completions.create.call_count > calls
        return
    with pytest.raises(PipelineError):
        Pipeline(output, resume=True, author_options=options).process(source, transcriber=transcriber)
    assert report.read_bytes() == before
    assert author_provider.chat.completions.create.call_count == calls


def test_regenerated_raw_invalidates_review_binding(synthetic_media, tmp_path, author_provider):
    source = synthetic_media()
    output = tmp_path / 'output'
    transcriber = Transcriber(client=author_provider)
    identity, _ = Pipeline(output, author_options=AuthorOptions()).process(source, transcriber=transcriber)
    job = output / identity
    old = json.loads((job / 'manifest.json').read_text())['stages']['author_review']
    (job / 'transcription.txt').unlink()
    # Remove synthetic checkpoints to exercise a different new raw result.
    shutil.rmtree(job / 'asr-chunks')
    author_provider.audio.transcriptions.create.return_value.text = 'Synthetic replacement.'
    with pytest.raises(PipelineError, match='settings changed'):
        Pipeline(output, resume=True, author_options=AuthorOptions()).process(source, transcriber=transcriber)
    current = json.loads((job / 'manifest.json').read_text())['stages']['author_review']
    assert current == old and current['transcription_sha256'] != digest(job / 'transcription.txt')


@pytest.mark.parametrize('args', [
    ['--workflow', '--input', '', '--stages', 'raw'],
    ['--workflow', '--input', 'unused', '--author-model', 'SYNTHETIC_PRIVATE_INVALID'],
    ['--workflow', '--input', 'unused', '--media-type', 'SYNTHETIC_PRIVATE_INVALID'],
    ['--workflow', '--input', 'unused', '--stages', 'SYNTHETIC_PRIVATE_INVALID'],
    ['--workflow', '--input', 'unused', '--stages', 'chapters'],
    ['--workflow', '--input', 'unused', '--draft-with-unresolved-high'],
    ['--workflow', '--input', 'unused', '--extract-only'],
    ['--pipeline', '--input', 'unused', '--chapters', 'both'],
])
def test_validation_private_and_before_provider(monkeypatch, args, capsys):
    transcriber = MagicMock(side_effect=AssertionError('No provider initialization'))
    monkeypatch.setattr('src.cli.Transcriber', transcriber)
    try:
        result = main(args)
        assert result == 1
    except SystemExit as error:
        assert error.code == 2
    logs = capsys.readouterr()
    assert 'SYNTHETIC_PRIVATE' not in logs.out + logs.err
    assert 'Traceback' not in logs.out + logs.err
    transcriber.assert_not_called()


def test_wrong_media_type_before_provider(monkeypatch, synthetic_media, tmp_path, capsys):
    source = synthetic_media('synthetic.wav')
    transcriber = MagicMock(side_effect=AssertionError('No provider initialization'))
    monkeypatch.setattr('src.cli.Transcriber', transcriber)
    assert main(['--workflow', '--input', str(source), '--media-type', 'video',
                 '--output-folder', str(tmp_path / 'output')]) == 1
    transcriber.assert_not_called()
    assert str(tmp_path) not in capsys.readouterr().out
