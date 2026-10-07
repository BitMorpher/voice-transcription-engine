"""Synthetic batch selection, immutable staging, gates and isolated failures."""

import json
from pathlib import Path

import pytest

from src.batch.cli import main, parser
from src.batch.plan import BatchError, load_plan, select
from src.batch.storage import lock, read_snapshot, save_snapshot, stage, target, verify
from src.cli import entrypoint
from src.transcriber import Transcriber


@pytest.mark.parametrize('phase', ['raw', 'review', 'chapters'])
@pytest.mark.parametrize('mode', ['auto', 'plain', 'json'])
@pytest.mark.parametrize('workers', [1, 2])
def test_nested_engine_preserves_batch_display_and_totals(
        staged, monkeypatch, phase, mode, workers):
    import io
    from src import cli
    from src.progress import CURRENT

    class Terminal(io.StringIO):
        def isatty(self):
            return True

    root, _ = staged
    stream = Terminal()
    monkeypatch.setenv('TERM', 'xterm-256color')
    monkeypatch.setenv('COLUMNS', '110')
    monkeypatch.setenv('LINES', '24')
    monkeypatch.setattr('sys.stdout', stream)
    monkeypatch.setattr(cli, 'require_ffmpeg', lambda: None)
    monkeypatch.setattr(cli, 'Transcriber', lambda **kwargs: object())
    # Exercise the real coordinator -> run_one -> engine chain. Only provider
    # work and the text prerequisites are replaced with synthetic results.
    monkeypatch.setattr('src.batch.runner.gate', lambda *args: None)
    monkeypatch.setattr('src.batch.runner.validate_approved_review', lambda *args, **kwargs: None)

    observed = []
    class Interview:
        def __init__(self, *args, **kwargs):
            pass

        def preflight(self, **kwargs):
            pass

        def process(self, **kwargs):
            reporter = CURRENT.get()
            observed.append((reporter.output, reporter.context.copy()))
            cli._report(status='progress', stage='transcription', stage_status='running')
            return 'a' * 32, {'transcription': 'complete'}

    monkeypatch.setattr(cli, 'OrderedInterview', Interview)
    flags = (['--human-reviewed', '--select', 'entry-1', '--select', 'entry-3',
              '--chapter-style', 'interview'] if phase == 'chapters' else
             ['--select', 'entry-1', '--select', 'entry-3'])
    assert main(['run', '--batch', str(root), '--phase', phase, '--send-to-openai',
                 '--progress', mode, '--parallel-interviews', str(workers), *flags]) == 0
    assert len(observed) == 2
    assert all(output == mode and context['selected'] == 2 for output, context in observed)
    assert {context['item'] for _, context in observed} == {1, 3}
    assert {context['batch_position'] for _, context in observed} == {1, 2}
    rows = [json.loads(line) for line in next((root / 'execution-logs').glob('*.jsonl'))
            .read_text().splitlines()]
    assert all(row['selected'] == 2 for row in rows if 'selected' in row)
    inner = [row for row in rows if row['status'] == 'summary' and 'batch_position' in row]
    assert len(inner) == 2 and all('finished' not in row for row in inner)
    assert rows[-1]['selected'] == rows[-1]['finished'] == rows[-1]['completed'] == 2
    output = stream.getvalue()
    if mode == 'json':
        assert '\x1b' not in output
        assert [json.loads(line) for line in output.splitlines()] == rows
    elif mode == 'plain':
        assert '\x1b' not in output and 'Interview 2 of 2 (plan item 3)' in output
    else:
        assert '2/2 interviews finished' in output


@pytest.fixture
def plan_file(tmp_path):
    entries = []
    for index in range(1, 4):
        source = tmp_path / f'synthetic-private-{index}.wav'
        source.write_bytes(b'synthetic-media-' + bytes([index]))
        manifest = tmp_path / f'interview-{index}.json'
        manifest.write_text(json.dumps({'version': 1, 'interview_id': f'private-id-{index}',
                                       'parts': [{'id': 'part-a', 'path': source.name, 'media_type': 'audio'}]}))
        entries.append({'id': f'entry-{index}', 'manifest': manifest.name})
    path = tmp_path / 'plan.json'
    path.write_text(json.dumps({'version': 1, 'interviews': entries}))
    return path


@pytest.fixture
def staged(plan_file, tmp_path):
    plan = load_plan(plan_file)
    root = save_snapshot(tmp_path / 'batch', plan, plan['interviews'])
    for item in plan['interviews']:
        stage(root, item, progress=lambda *a, **kw: None)
    return root, plan


def test_inventory_never_opens_media(plan_file, monkeypatch, capsys):
    original = Path.open
    def guarded(path, *args, **kwargs):
        if path.suffix == '.wav':
            raise AssertionError('Metadata inventory must not open media.')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', guarded)
    assert main(['inventory', '--plan', str(plan_file), '--select', 'entry-3', '--select', 'entry-1']) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [row['item'] for row in rows if row['status'] == 'complete'] == [1, 3]
    assert 'private-id' not in json.dumps(rows) and str(plan_file.parent) not in json.dumps(rows)


def test_selection_plan_order_and_exclusion(plan_file):
    plan = load_plan(plan_file)
    assert [i['position'] for i in select(plan, ['entry-3', 'entry-1'])] == [1, 3]
    assert [i['position'] for i in select(plan, exclude=['entry-2'])] == [1, 3]
    with pytest.raises(BatchError):
        select(plan, ['unknown'])
    with pytest.raises(BatchError):
        select(plan, exclude=['entry-1', 'entry-2', 'entry-3'])


def test_duplicate_json_keys_rejected(plan_file):
    plan_file.write_text('{"version":1,"version":1,"interviews":[]}')
    with pytest.raises(BatchError):
        load_plan(plan_file)


def test_duplicate_source_identity_rejected(plan_file):
    document = json.loads(plan_file.read_text())
    document['interviews'][1]['manifest'] = document['interviews'][0]['manifest']
    plan_file.write_text(json.dumps(document))
    with pytest.raises(BatchError):
        load_plan(plan_file)


def test_stage_exclusive_and_verification(staged):
    root, plan = staged
    item = plan['interviews'][0]
    directory = verify(root, item)
    copy = directory / 'input/part-0001.wav'
    assert copy.read_bytes() == Path(item['manifest']['parts'][0]['path']).read_bytes()
    assert copy.stat().st_ino != Path(item['manifest']['parts'][0]['path']).stat().st_ino
    assert copy.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        stage(root, item, progress=lambda *a, **kw: None)
    with pytest.raises(BatchError):
        save_snapshot(root, plan, plan['interviews'])


@pytest.mark.parametrize('relative', ['input/part-0001.wav', 'input/interview.json', 'staging.json'])
def test_staged_tampering_fails(staged, relative):
    root, plan = staged
    item = plan['interviews'][0]
    (target(root, item) / relative).write_text('synthetic-tampering')
    with pytest.raises((BatchError, ValueError)):
        verify(root, item)


def test_snapshot_tampering_fails(staged):
    root, _ = staged
    (root / 'batch-plan.json').write_text('{}')
    with pytest.raises(BatchError):
        read_snapshot(root)


def test_symlink_staging_rejected(staged, tmp_path):
    root, plan = staged
    copy = target(root, plan['interviews'][0]) / 'input/part-0001.wav'
    copy.unlink()
    copy.symlink_to(tmp_path / 'synthetic-private-1.wav')
    with pytest.raises(BatchError):
        verify(root, plan['interviews'][0])


def test_lock_is_exclusive_and_released(staged):
    root, _ = staged
    with pytest.raises(RuntimeError):
        with lock(root):
            with pytest.raises(BatchError):
                with lock(root):
                    pass
            raise RuntimeError('synthetic')
    assert not (root / '.batch.lock').exists()


def test_hydration_requires_explicit_flag(plan_file, tmp_path):
    plan = load_plan(plan_file)
    item = plan['interviews'][0]
    item['manifest']['parts'][0]['dataless'] = True
    root = save_snapshot(tmp_path / 'batch', plan, [item])
    with pytest.raises(BatchError, match='hydration'):
        stage(root, item, progress=lambda *a, **kw: None)
    assert not target(root, item).exists()
    stage(root, item, hydration=True, progress=lambda *a, **kw: None)


def test_changed_source_before_staging(plan_file, tmp_path):
    plan = load_plan(plan_file)
    item = plan['interviews'][0]
    Path(item['manifest']['parts'][0]['path']).write_bytes(b'changed')
    root = save_snapshot(tmp_path / 'batch', plan, [item])
    with pytest.raises(BatchError):
        stage(root, item, progress=lambda *a, **kw: None)


def test_run_serial_failure_isolation_and_safe_logs(staged, monkeypatch, capsys):
    root, _ = staged
    seen = []
    def run(root, item, args):
        seen.append(item['position'])
        if item['position'] == 2:
            raise RuntimeError('SYNTHETIC_SECRET provider payload transcript /personal/source')
    monkeypatch.setattr('src.batch.cli.run_one', run)
    monkeypatch.setattr('src.batch.cli.shutil.which', lambda _: 'synthetic-tool')
    assert main(['run', '--batch', str(root), '--send-to-openai']) == 1
    assert seen == [1, 2, 3]
    output = capsys.readouterr().out
    assert 'SYNTHETIC_SECRET' not in output and 'personal' not in output
    assert 'private-id' not in output and str(root) not in output
    summary = json.loads(next(root.glob('summary-*.json')).read_text())
    assert [row['status'] for row in summary['items']] == ['complete', 'failed', 'complete']
    assert summary['failed'] == 1
    assert next((root / 'execution-logs').glob('*.jsonl')).read_text() == output


@pytest.mark.parametrize('phase,flags', [('raw', []), ('chapters', ['--send-to-openai']),
                                       ('chapters', ['--send-to-openai', '--human-reviewed'])])
def test_provider_and_chapter_gates(staged, monkeypatch, phase, flags):
    root, _ = staged
    monkeypatch.setattr('src.batch.cli.shutil.which', lambda _: 'synthetic-tool')
    monkeypatch.setattr('src.batch.cli.run_one', lambda *a: pytest.fail('Gate must stop before pipeline.'))
    assert main(['run', '--batch', str(root), '--phase', phase, *flags]) == 1


def test_interrupt_retains_summary_and_releases_lock(staged, monkeypatch, capsys):
    root, _ = staged
    def interrupted(*args):
        raise KeyboardInterrupt()
    monkeypatch.setattr('src.batch.cli.shutil.which', lambda _: 'synthetic-tool')
    monkeypatch.setattr('src.batch.cli.run_one', interrupted)
    assert main(['run', '--batch', str(root), '--send-to-openai']) == 130
    assert not (root / '.batch.lock').exists()
    assert 'interrupted' in next(root.glob('summary-*.json')).read_text()
    assert 'interrupted' in capsys.readouterr().out


def test_prepare_requires_consent(plan_file, tmp_path, monkeypatch):
    monkeypatch.setattr('src.batch.cli.shutil.which', lambda _: 'synthetic-tool')
    root = tmp_path / 'batch'
    assert main(['prepare', '--plan', str(plan_file), '--batch', str(root)]) == 1
    assert not root.exists()


def test_staging_selection_is_saved(plan_file, tmp_path, monkeypatch):
    monkeypatch.setattr('src.batch.cli.shutil.which', lambda _: 'synthetic-tool')
    root = tmp_path / 'batch'
    assert main(['prepare', '--plan', str(plan_file), '--batch', str(root), '--copy-local-files',
                 '--select', 'entry-2']) == 0
    assert read_snapshot(root)['selected_ids'] == ['entry-2']
    assert main(['verify', '--batch', str(root)]) == 0
    assert not (root / 'item-0001').exists()


def test_batch_parse_privacy(capsys):
    with pytest.raises(SystemExit):
        parser().parse_args(['run', '--provider-retries', 'SYNTHETIC_SECRET'])
    assert 'SYNTHETIC_SECRET' not in capsys.readouterr().err


def test_installed_style_entrypoint_extract_resume(synthetic_media, tmp_path, capsys):
    source = synthetic_media('synthetic.wav')
    output = tmp_path / 'output'
    argv = ['--extract-only', '--input', str(source), '--output-folder', str(output)]
    assert entrypoint(argv) == 0
    assert entrypoint(argv + ['--resume']) == 0
    text = capsys.readouterr().out
    assert 'cache_reused' in text and str(source) not in text
    assert len(list((output / 'execution-logs').glob('*.jsonl'))) == 2


def test_end_to_end_raw_review_chapter_gates(synthetic_media, tmp_path, provider, monkeypatch, capsys):
    source = synthetic_media('synthetic.wav')
    manifest = tmp_path / 'interview.json'
    manifest.write_text(json.dumps({'version': 1, 'interview_id': 'synthetic-session',
                                   'parts': [{'id': 'part-a', 'path': source.name}]}))
    plan = tmp_path / 'plan.json'
    plan.write_text(json.dumps({'version': 1, 'interviews': [{'id': 'entry-a', 'manifest': manifest.name}]}))
    root = tmp_path / 'batch'
    monkeypatch.setattr('src.cli.Transcriber', lambda **kwargs: Transcriber(client=provider, **kwargs))
    assert main(['prepare', '--plan', str(plan), '--batch', str(root), '--copy-local-files']) == 0
    assert main(['run', '--batch', str(root), '--send-to-openai']) == 0
    calls = provider.audio.transcriptions.create.call_count
    assert main(['run', '--batch', str(root), '--send-to-openai']) == 0
    assert provider.audio.transcriptions.create.call_count == calls
    assert main(['run', '--batch', str(root), '--phase', 'chapters', '--send-to-openai',
                 '--human-reviewed', '--select', 'entry-a']) == 1
    assert 'Synthetic transcript.' not in capsys.readouterr().out


@pytest.fixture
def complete_provider(provider):
    from types import SimpleNamespace
    def respond(**kwargs):
        supplied = json.loads(kwargs['messages'][-1]['content'])
        name = kwargs['response_format']['json_schema']['name']
        if name == 'faithful_transcript_edit':
            body = {'chunk_index': supplied['chunk_index'], 'text': supplied['text'], 'speaker_uncertain': False}
        elif name == 'faithful_turn_group_edit':
            body = dict(group_index=supplied['group_index'], edits=[{**turn, 'speaker_uncertain': False} for turn in supplied['turns']])
        elif name == 'source_grounded_author_review':
            body = {'chunk_index': supplied['chunk_index'], 'fully_reviewed': True,
                    'reviewed_start': supplied['core_start'], 'reviewed_end': supplied['core_end'], 'findings': []}
        else:
            body = {'chunk_index': supplied['chunk_index'], 'passages': [
                {'unit_ids': [unit['unit_id']], 'text': unit['text'].strip(), 'kind': 'verbatim_excerpt'}
                for unit in supplied['source_units']], 'coverage_omissions': []}
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(
            content=json.dumps(body), refusal=None))])
    provider.chat.completions.create.side_effect = respond
    return provider


@pytest.fixture
def reviewed_batch(synthetic_media, tmp_path, complete_provider, monkeypatch):
    source = synthetic_media('synthetic.wav')
    manifest = tmp_path / 'interview.json'
    manifest.write_text(json.dumps({'version': 1, 'interview_id': 'synthetic-session',
                                   'parts': [{'id': 'part-a', 'path': source.name}]}))
    plan = tmp_path / 'plan.json'
    plan.write_text(json.dumps({'version': 1, 'interviews': [{'id': 'entry-a', 'manifest': manifest.name}]}))
    root = tmp_path / 'batch'
    monkeypatch.setattr('src.cli.Transcriber', lambda **kwargs: Transcriber(client=complete_provider, **kwargs))
    assert main(['prepare', '--plan', str(plan), '--batch', str(root), '--copy-local-files']) == 0
    assert main(['run', '--batch', str(root), '--send-to-openai']) == 0
    assert main(['run', '--batch', str(root), '--phase', 'review', '--send-to-openai']) == 0
    return root, complete_provider


def test_completed_review_chapters_and_resume(reviewed_batch, capsys):
    root, provider = reviewed_batch
    asr_calls = provider.audio.transcriptions.create.call_count
    args = ['run', '--batch', str(root), '--phase', 'chapters', '--send-to-openai',
            '--select', 'entry-a', '--human-reviewed']
    assert main(args) == 0
    chat_calls = provider.chat.completions.create.call_count
    assert main(args) == 0
    assert provider.chat.completions.create.call_count == chat_calls
    assert provider.audio.transcriptions.create.call_count == asr_calls
    assert len(list(root.rglob('chapter_drafts.json'))) == 1
    text = capsys.readouterr().out
    assert 'Synthetic transcript.' not in text and str(root) not in text


@pytest.mark.parametrize('artifact', ['transcription.txt', 'review_report.json', 'review_report.xlsx', 'provenance.json'])
def test_tampered_review_gate_prevents_chapter_calls(reviewed_batch, artifact):
    root, provider = reviewed_batch
    path = next((root / 'item-0001/output/interviews').rglob(artifact))
    path.write_text('synthetic-tamper')
    calls = provider.chat.completions.create.call_count
    assert main(['run', '--batch', str(root), '--phase', 'chapters', '--send-to-openai',
                 '--select', 'entry-a', '--human-reviewed']) == 1
    assert provider.chat.completions.create.call_count == calls


def test_changed_asr_settings_gate_requires_new_raw(reviewed_batch):
    root, provider = reviewed_batch
    calls = provider.audio.transcriptions.create.call_count
    assert main(['run', '--batch', str(root), '--phase', 'review', '--send-to-openai',
                 '--audio-chunk-seconds', '60']) == 1
    assert provider.audio.transcriptions.create.call_count == calls


def review_calls(provider):
    return sum(call.kwargs['response_format']['json_schema']['name'] == 'source_grounded_author_review'
               for call in provider.chat.completions.create.call_args_list)


def test_chapters_use_exact_human_gated_bundle_without_fresh_review(reviewed_batch):
    root, provider = reviewed_batch
    original = next(root.rglob('review_report.json'))
    approved_json, approved_xlsx = original.read_bytes(), original.with_suffix('.xlsx').read_bytes()
    expected_calls = review_calls(provider)
    respond = provider.chat.completions.create.side_effect
    def forbid_fresh_review(**kwargs):
        if kwargs['response_format']['json_schema']['name'] == 'source_grounded_author_review':
            pytest.fail('Chapters must not request an unapproved review generation.')
        return respond(**kwargs)
    provider.chat.completions.create.side_effect = forbid_fresh_review
    args = ['run', '--batch', str(root), '--phase', 'chapters', '--send-to-openai',
            '--select', 'entry-a', '--human-reviewed']
    assert main(args) == 0
    assert review_calls(provider) == expected_calls == 1
    reports = list(root.rglob('review_report.json'))
    assert len(reports) == 2  # New chapter generation, same exact approved review bytes.
    assert all(path.read_bytes() == approved_json for path in reports)
    assert all(path.with_suffix('.xlsx').read_bytes() == approved_xlsx for path in reports)
    chapter_manifest = next(root.rglob('chapter_drafts.json')).parent.parent / 'manifest.json'
    state = json.loads(chapter_manifest.read_text())
    import hashlib
    assert state['stages']['chapters']['review_sha256'] == hashlib.sha256(approved_json).hexdigest()
    # A second chapter style/person configuration also reuses this approved review.
    assert main(args + ['--chapters', 'narrative', '--narrative-person', 'third']) == 0
    assert review_calls(provider) == expected_calls


@pytest.mark.parametrize('artifact', ['review_report.json', 'review_report.xlsx', 'provenance.json', 'manifest.json'])
def test_approval_change_between_gate_and_execution_fails_before_requests(reviewed_batch, monkeypatch, artifact, capsys):
    from src.batch import runner
    root, provider = reviewed_batch
    gate = runner.gate
    def changed(*args, **kwargs):
        approval = gate(*args, **kwargs)
        path = (next(approval.job.rglob(artifact)) if artifact.startswith('review_')
                else approval.job / artifact)
        path.write_text('SYNTHETIC_SECRET tampered after approval')
        return approval
    monkeypatch.setattr(runner, 'gate', changed)
    calls = provider.chat.completions.create.call_count
    assert main(['run', '--batch', str(root), '--phase', 'chapters', '--send-to-openai',
                 '--select', 'entry-a', '--human-reviewed']) == 1
    assert provider.chat.completions.create.call_count == calls
    assert not list(root.rglob('chapter_drafts.json'))
    text = capsys.readouterr().out
    assert 'review_prerequisite' in text and 'blocked' in text
    assert 'SYNTHETIC_SECRET' not in text


def test_existing_unapproved_target_review_is_never_replaced(reviewed_batch, monkeypatch):
    from src import ordered_interview
    root, provider = reviewed_batch
    reuse = ordered_interview.reuse_approved_review
    def seed_conflicting_review(approval, job, state, options, provenance, save):
        state['stages']['author_review'] = {'status': 'failed'}
        save()
        return reuse(approval, job, state, options, provenance, save)
    monkeypatch.setattr(ordered_interview, 'reuse_approved_review', seed_conflicting_review)
    calls = provider.chat.completions.create.call_count
    assert main(['run', '--batch', str(root), '--phase', 'chapters', '--send-to-openai',
                 '--select', 'entry-a', '--human-reviewed']) == 1
    assert provider.chat.completions.create.call_count == calls
    assert not list(root.rglob('chapter_drafts.json'))
    # The conflicting failed record is retained for inspection, never overwritten.
    assert any(json.loads(path.read_text())['stages'].get('author_review') == {'status': 'failed'}
               for path in root.rglob('manifest.json'))
