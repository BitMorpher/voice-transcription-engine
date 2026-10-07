"""Synthetic workflow cache binding and concurrent source-change regressions."""

import copy
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.author_review import ReviewOptions
from src.author_workflow import AuthorOptions, AuthorWorkflowError, run_author_stages
from src.chapters import ChapterOptions
from src.private_output import digest


@pytest.fixture
def integrity_job(tmp_path):
    raw = 'Synthetic source testimony.\nAnother synthetic line.'
    job = tmp_path / 'job'
    job.mkdir()
    target = job / 'transcription.txt'
    target.write_text(raw, encoding='utf-8')
    state = {'stages': {'transcription': {'status': 'complete', 'sha256': digest(target)}}}
    client = MagicMock()
    def respond(**kwargs):
        payload = json.loads(kwargs['messages'][-1]['content'])
        body = {'chunk_index': payload['chunk_index'], 'fully_reviewed': True,
                'contract_version': payload['contract_version'], 'reviewed_piece_ids': payload['core_piece_ids'],
                'findings': []}
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop',
            message=SimpleNamespace(content=json.dumps(body), refusal=None))])
    client.chat.completions.create.side_effect = respond
    return job, state, SimpleNamespace(client=client), AuthorOptions()


def run(value, *, resume=False, options=None):
    job, state, transcriber, original_options = value
    summary = {}
    run_author_stages(job, state, transcriber, options or original_options,
                      resume=resume, save=lambda: None, summary=summary)
    return summary


def report_path(value):
    job, state, *_ = value
    record = state['stages']['author_review']
    return next(job / path for path in record['artifacts'] if path.endswith('/review_report.json'))


@pytest.mark.parametrize('mutation', ['model', 'settings', 'prompt', 'source'])
def test_cache_source_model_settings_and_prompt_changes_fail_without_overwrite(
        integrity_job, monkeypatch, mutation):
    run(integrity_job)
    job, state, transcriber, options = integrity_job
    target = report_path(integrity_job)
    before = target.read_bytes()
    calls = transcriber.client.chat.completions.create.call_count
    if mutation == 'model':
        options = AuthorOptions(review_options=ReviewOptions(model='gpt-6.1-sol'))
    elif mutation == 'settings':
        options = AuthorOptions(review_options=ReviewOptions(chunk_bytes=128))
    elif mutation == 'prompt':
        monkeypatch.setattr('src.author_review._prompt', lambda: 'Synthetic changed prompt.')
    else:
        (job / 'transcription.txt').write_text('Synthetic changed raw.')
    if mutation == 'source':
        with pytest.raises(AuthorWorkflowError):
            run(integrity_job, resume=True, options=options)
        assert transcriber.client.chat.completions.create.call_count == calls
    else:
        run(integrity_job, resume=True, options=options)
        assert transcriber.client.chat.completions.create.call_count > calls
        assert len(state['derivative_versions']['author_review']) == 2
    assert target.read_bytes() == before
    assert state['stages']['author_review']['status'] == 'complete'


def test_raw_change_during_review_cannot_publish_report(integrity_job):
    job, state, transcriber, _ = integrity_job
    default = transcriber.client.chat.completions.create.side_effect
    def mutate(**kwargs):
        (job / 'transcription.txt').write_text('Synthetic replacement during review.')
        return default(**kwargs)
    transcriber.client.chat.completions.create.side_effect = mutate
    with pytest.raises(AuthorWorkflowError, match='Raw transcript changed'):
        run(integrity_job)
    assert not list(job.glob('author_review_*'))
    assert 'author_review' not in state['stages']


def test_raw_change_between_cached_bundle_checks_cannot_skip_review(integrity_job, monkeypatch):
    run(integrity_job)
    job, state, transcriber, _ = integrity_job
    target = report_path(integrity_job)
    before = target.read_bytes()
    changed = False
    def mutate(path):
        nonlocal changed
        checksum = digest(path)
        if path == target and not changed:
            changed = True
            (job / 'transcription.txt').write_text('Synthetic replacement during cache check.')
        return checksum
    monkeypatch.setattr('src.author_workflow.digest', mutate)
    with pytest.raises(AuthorWorkflowError, match='Raw transcript changed'):
        run(integrity_job, resume=True)
    assert target.read_bytes() == before
    assert state['stages']['author_review']['status'] == 'complete'
    assert transcriber.client.chat.completions.create.call_count == 1


@pytest.mark.parametrize('same', [True, False])
def test_deleted_then_recreated_raw_reuses_only_identical_source(integrity_job, same):
    run(integrity_job)
    job, state, transcriber, _ = integrity_job
    target = job / 'transcription.txt'
    original = target.read_bytes()
    report = report_path(integrity_job)
    before = report.read_bytes()
    old_record = copy.deepcopy(state['stages']['author_review'])
    target.unlink()
    target.write_bytes(original if same else b'Synthetic regenerated different transcript.')
    state['stages']['transcription']['sha256'] = hashlib.sha256(target.read_bytes()).hexdigest()
    if same:
        assert run(integrity_job, resume=True)['author_review'] == 'skipped'
    else:
        with pytest.raises(AuthorWorkflowError, match='settings changed'):
            run(integrity_job, resume=True)
    assert report.read_bytes() == before and state['stages']['author_review'] == old_record
    assert transcriber.client.chat.completions.create.call_count == 1


@pytest.mark.parametrize('mutation', ['json', 'keys', 'missing_file', 'traversal', 'coverage', 'raw_hash'])
def test_invalid_cached_bundle_is_safe_and_never_a_clean_skip(integrity_job, mutation):
    run(integrity_job)
    job, state, transcriber, _ = integrity_job
    target = report_path(integrity_job)
    record = state['stages']['author_review']
    if mutation in {'json', 'coverage', 'raw_hash'}:
        if mutation == 'json':
            target.write_text('SYNTHETIC_PRIVATE_BAD_JSON')
        else:
            report = json.loads(target.read_text())
            if mutation == 'coverage':
                report['coverage']['complete'] = False
            else:
                report['raw_sha256'] = '0' * 64
            target.write_text(json.dumps(report))
        record['artifacts'][str(target.relative_to(job))] = digest(target)
    elif mutation == 'keys':
        record['artifacts'] = {5: 'synthetic-invalid'}
    elif mutation == 'missing_file':
        target.unlink()
    else:
        record['artifacts'] = {'../review_report.json': '0' * 64,
                               '../review_report.xlsx': '0' * 64}
    with pytest.raises(AuthorWorkflowError) as error:
        run(integrity_job, resume=True)
    assert 'SYNTHETIC_PRIVATE' not in str(error.value)
    assert str(job) not in str(error.value)
    assert transcriber.client.chat.completions.create.call_count == 1


def test_incomplete_bundles_remain_and_are_not_overwritten_on_retry(integrity_job):
    job, state, transcriber, options = integrity_job
    # Many chunks allow complete acknowledgement followed by one truncated chunk.
    (job / 'transcription.txt').write_text('Synthetic source line. ' * 20)
    state['stages']['transcription']['sha256'] = digest(job / 'transcription.txt')
    options = AuthorOptions(review_options=ReviewOptions(chunk_bytes=64))
    default = transcriber.client.chat.completions.create.side_effect
    def partial(**kwargs):
        response = default(**kwargs)
        if json.loads(kwargs['messages'][-1]['content'])['chunk_index'] == 2:
            response.choices[0].finish_reason = 'length'
        return response
    transcriber.client.chat.completions.create.side_effect = partial
    with pytest.raises(AuthorWorkflowError, match='incomplete'):
        run(integrity_job, options=options)
    old = report_path(integrity_job)
    before = old.read_bytes()
    assert state['stages']['author_review']['status'] == 'incomplete'
    transcriber.client.chat.completions.create.side_effect = default
    assert run(integrity_job, resume=True, options=options)['author_review'] == 'complete'
    assert old.read_bytes() == before and old != report_path(integrity_job)
    assert len(list(job.glob('author_review_*'))) == 2


def test_cached_chapter_skip_rechecks_bound_report_after_bundle_checks(integrity_job, monkeypatch):
    options = AuthorOptions(chapter_options=ChapterOptions(styles=('interview',)))
    run(integrity_job, options=options)
    job, state, _, _ = integrity_job
    report = report_path(integrity_job)
    chapter = next(job / path for path in state['stages']['chapters']['artifacts']
                   if path.endswith('/chapter_drafts.json'))
    before = chapter.read_bytes()
    changed = False
    def mutate(path):
        nonlocal changed
        checksum = digest(path)
        if path == chapter and not changed:
            changed = True
            report.write_bytes(report.read_bytes() + b' ')
        return checksum
    monkeypatch.setattr('src.author_workflow.digest', mutate)
    with pytest.raises(AuthorWorkflowError, match='review changed'):
        run(integrity_job, resume=True, options=options)
    assert chapter.read_bytes() == before


def test_invalid_bundle_cannot_mix_files_from_distinct_attempts(integrity_job):
    run(integrity_job)
    job, state, transcriber, _ = integrity_job
    record = state['stages']['author_review']
    original = next(job / path for path in record['artifacts'] if path.endswith('/review_report.xlsx'))
    alternate = job / 'author_review_synthetic_other_attempt'
    alternate.mkdir()
    replacement = alternate / original.name
    replacement.write_bytes(original.read_bytes())
    checksum = record['artifacts'].pop(str(original.relative_to(job)))
    record['artifacts'][str(replacement.relative_to(job))] = checksum
    with pytest.raises(AuthorWorkflowError):
        run(integrity_job, resume=True)
    assert transcriber.client.chat.completions.create.call_count == 1


def test_cached_bundle_read_error_does_not_expose_private_path(integrity_job, monkeypatch):
    run(integrity_job)
    report = report_path(integrity_job)
    def inaccessible(path):
        if path == report:
            raise OSError('SYNTHETIC_PRIVATE_PATH /private/synthetic/source')
        return digest(path)
    monkeypatch.setattr('src.author_workflow.digest', inaccessible)
    with pytest.raises(AuthorWorkflowError) as error:
        run(integrity_job, resume=True)
    assert 'SYNTHETIC_PRIVATE_PATH' not in str(error.value)
    assert '/private/synthetic/source' not in str(error.value)
