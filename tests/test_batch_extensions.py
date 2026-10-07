"""Synthetic extensionless staging, scoped identity and independent approval gates."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.batch.cli import main
from src.batch.plan import BatchError, load_plan
from src.batch.probe import probe_video
from src.batch.storage import read_snapshot, save_snapshot, stage, target, verify
from src.private_output import digest
from src.transcriber import Transcriber


def config(tmp_path, sources, *, kind='auto'):
    rows = []
    for index, source in enumerate(sources, 1):
        manifest = tmp_path / f'manifest-{index}.json'
        manifest.write_text(json.dumps({'version': 1, 'interview_id': f'session-{index}',
            'parts': [{'id': 'recording', 'path': str(source), 'media_type': kind}]}))
        rows.append({'id': f'entry-{index}', 'manifest': manifest.name})
    plan = tmp_path / 'plan.json'
    plan.write_text(json.dumps({'version': 1, 'interviews': rows}))
    return plan


def frozen(plan_path, tmp_path):
    plan = load_plan(plan_path)
    root = save_snapshot(tmp_path / 'batch', plan, plan['interviews'])
    return root, plan['interviews'][0]


@pytest.mark.parametrize('suffix', ['.mp4', '.mkv', '.avi'])
def test_probed_suffix_preserves_original_and_exact_bytes(synthetic_media, tmp_path, suffix):
    source = synthetic_media('synthetic' + suffix)
    original = source.with_suffix('')
    source.rename(original)
    expected = digest(original)
    plan_path = config(tmp_path, [original], kind='video')
    root, item = frozen(plan_path, tmp_path)
    stage(root, item, progress=lambda *a, **kw: None)
    directory = verify(root, item)
    state = json.loads((directory / 'staging.json').read_bytes())
    assert state['version'] == 2
    copy = directory / 'input' / state['parts'][0]['path']
    assert copy.suffix == suffix
    assert digest(copy) == expected == digest(original)
    assert copy.stat().st_ino != original.stat().st_ino
    assert original.exists() and not original.with_suffix(suffix).exists()
    assert state['parts'][0]['probe']['has_video'] is True
    assert not list((directory / 'input').glob('*.pending'))
    assert read_snapshot(root)['plan']['interviews'][0]['manifest']['parts'][0]['path'] == str(original)


def test_extensionless_inventory_check_never_opens_or_probes(tmp_path, monkeypatch, capsys):
    source = tmp_path / 'SYNTHETIC_PRIVATE_VIDEO'
    source.write_bytes(b'not codec validated')
    plan = config(tmp_path, [source], kind='video')
    original = Path.open
    def guarded(path, *args, **kwargs):
        if path == source:
            pytest.fail('Metadata actions must never read the source.')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', guarded)
    monkeypatch.setattr('src.batch.storage.probe_video', lambda *a: pytest.fail('No content probe.'))
    monkeypatch.setattr('src.batch.cli.shutil.which', lambda _: 'synthetic')
    assert main(['inventory', '--plan', str(plan)]) == 0
    assert main(['check', '--plan', str(plan)]) == 0
    logs = capsys.readouterr().out
    assert source.name not in logs and str(tmp_path) not in logs


@pytest.mark.parametrize('kind', ['auto', 'audio'])
def test_extensionless_requires_explicit_video(tmp_path, kind):
    source = tmp_path / 'synthetic'
    source.write_bytes(b'opaque')
    with pytest.raises(BatchError):
        load_plan(config(tmp_path, [source], kind=kind))


@pytest.mark.parametrize('failure', ['no_audio', 'audio_only', 'invalid', 'playlist'])
def test_bad_extensionless_never_publishes_accepted_staging(synthetic_media, tmp_path, failure, capsys):
    if failure == 'no_audio':
        source = synthetic_media('synthetic.mp4', audio=False)
    elif failure == 'audio_only':
        source = synthetic_media('synthetic.wav')
    else:
        source = tmp_path / 'synthetic.txt'
        source.write_text('PRIVATE_DIAGNOSTIC\n' if failure == 'invalid'
                          else '#EXTM3U\nhttps://example.invalid/private-stream\n')
    original = source.with_suffix('')
    source.rename(original)
    before = original.read_bytes()
    plan = config(tmp_path, [original], kind='video')
    root = tmp_path / 'batch'
    assert main(['prepare', '--plan', str(plan), '--batch', str(root), '--copy-local-files']) == 1
    assert not list(root.rglob('staging.json'))
    assert not list(root.rglob('interview.json'))
    assert before == original.read_bytes()
    assert original.name not in capsys.readouterr().out


def test_probe_only_receives_copied_file_and_detects_mutation(synthetic_media, tmp_path, monkeypatch):
    source = synthetic_media('synthetic.mp4').with_suffix('')
    source.with_suffix('.mp4').rename(source)
    root, item = frozen(config(tmp_path, [source], kind='video'), tmp_path)
    def probe(path, timeout):
        assert path != source and path.parent == target(root, item) / 'input'
        assert path.suffix == '.pending' and path.read_bytes() == source.read_bytes()
        result = probe_video(path, timeout)
        path.write_bytes(b'changed during probe')
        return result
    monkeypatch.setattr('src.batch.storage.probe_video', probe)
    with pytest.raises(BatchError):
        stage(root, item, progress=lambda *a, **kw: None)
    assert not (target(root, item) / 'staging.json').exists()


def test_hydration_gate_precedes_copy_and_probe(synthetic_media, tmp_path, monkeypatch):
    source = synthetic_media('synthetic.mp4').with_suffix('')
    source.with_suffix('.mp4').rename(source)
    root, item = frozen(config(tmp_path, [source], kind='video'), tmp_path)
    item['manifest']['parts'][0]['dataless'] = True
    monkeypatch.setattr('src.batch.storage.probe_video', lambda *a: pytest.fail('No hydration approval.'))
    with pytest.raises(BatchError):
        stage(root, item, progress=lambda *a, **kw: None)
    assert not target(root, item).exists()


@pytest.mark.parametrize('change', ['suffix', 'family', 'video', 'unknown_field', 'version', 'bytes'])
def test_probe_ledger_cannot_rewrite_suffix_or_contract(synthetic_media, tmp_path, change):
    source = synthetic_media('synthetic.mp4').with_suffix('')
    source.with_suffix('.mp4').rename(source)
    root, item = frozen(config(tmp_path, [source], kind='video'), tmp_path)
    stage(root, item, progress=lambda *a, **kw: None)
    directory = target(root, item)
    state = json.loads((directory / 'staging.json').read_bytes())
    record = state['parts'][0]
    if change == 'suffix':
        record['probe']['staged_suffix'] = '.wav'
    elif change == 'family':
        record['probe']['container_family'] = 'playlist'
    elif change == 'video':
        record['probe']['has_video'] = False
    elif change == 'unknown_field':
        record['probe']['original_path'] = '/synthetic/private'
    elif change == 'bytes':
        record['bytes'] += 1
    else:
        state['version'] = 1
    (directory / 'staging.json').write_text(json.dumps(state))
    (directory / 'staging.sha256').write_text(digest(directory / 'staging.json'))
    with pytest.raises((BatchError, KeyError)):
        verify(root, item)


@pytest.mark.parametrize('failure', ['timeout', 'oversize', 'malformed', 'attached_picture'])
def test_probe_is_bounded_and_diagnostics_private(tmp_path, monkeypatch, failure):
    import subprocess
    def run(command, **kwargs):
        assert kwargs['timeout'] == 30 and kwargs['stderr'] == subprocess.DEVNULL
        assert kwargs['stdin'] == subprocess.DEVNULL
        assert command[command.index('-protocol_whitelist') + 1] == 'file'
        assert '-format_whitelist' in command
        if failure == 'timeout':
            raise subprocess.TimeoutExpired(command, 30, stderr=b'PRIVATE_DIAGNOSTIC')
        if failure == 'oversize':
            kwargs['stdout'].write(b' ' * 65537)
        elif failure == 'malformed':
            kwargs['stdout'].write(b'PRIVATE_DIAGNOSTIC')
        else:
            kwargs['stdout'].write(json.dumps({'format': {'format_name': 'mov,mp4,m4a,3gp,3g2,mj2'},
                'streams': [{'codec_type': 'audio'}, {'codec_type': 'video', 'disposition': {'attached_pic': 1}}]}).encode())
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr('src.batch.probe.subprocess.run', run)
    with pytest.raises(BatchError) as caught:
        probe_video(tmp_path / 'private-input', 300)
    assert 'PRIVATE_DIAGNOSTIC' not in str(caught.value) and str(tmp_path) not in str(caught.value)


@pytest.fixture
def interview_client():
    client = MagicMock()
    client.high_family = None
    def audio(**kw):
        if kw['model'] != 'gpt-4o-transcribe-diarize':
            return SimpleNamespace(text='Original exact testimony.\r\nNext line.')
        return {'text': 'A: A café question?\r\nB: An answer.\nC: Third voice.', 'segments': [
            {'speaker': 'A', 'start': 0.0, 'end': 0.4, 'text': 'A café question?'},
            {'speaker': 'B', 'start': 0.3, 'end': 0.8, 'text': 'An answer.'},
            {'speaker': 'C', 'start': 0.8, 'end': 1.0, 'text': 'Third voice.'}]}
    def chat(**kw):
        supplied = json.loads(kw['messages'][-1]['content'])
        name = kw['response_format']['json_schema']['name']
        if name == 'faithful_transcript_edit':
            body = dict(chunk_index=supplied['chunk_index'], text=supplied['text'], speaker_uncertain=False)
        elif name == 'faithful_turn_group_edit':
            body = dict(group_index=supplied['group_index'], edits=[{**turn, 'speaker_uncertain': False} for turn in supplied['turns']])
        elif name == 'source_grounded_author_review':
            supplied['text'] = ''.join(p['text'] for p in supplied['evidence_pieces'])
            attributed = 'unidentified' in supplied['text'] or 'user_confirmed_mapping' in supplied['text']
            high = client.high_family == ('attributed' if attributed else 'original')
            findings = [dict(category='allegation', severity='high', reason_code='serious_allegation',
                excerpt=supplied['text'], piece_ids=[p['piece_id'] for p in supplied['evidence_pieces']])] if high else []
            body = dict(chunk_index=supplied['chunk_index'], fully_reviewed=True,
                contract_version=supplied['contract_version'], reviewed_piece_ids=supplied['core_piece_ids'], findings=findings)
        else:
            body = dict(chunk_index=supplied['chunk_index'], passages=[dict(unit_ids=[u['unit_id']],
                text=u['text'].strip(), kind='verbatim_excerpt') for u in supplied['source_units']], coverage_omissions=[])
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop',
            message=SimpleNamespace(content=json.dumps(body), refusal=None))])
    client.audio.transcriptions.create.side_effect = audio
    client.chat.completions.create.side_effect = chat
    return client


@pytest.fixture
def interview_batch(synthetic_media, tmp_path, monkeypatch, interview_client):
    sources = [synthetic_media('session-one.wav'), synthetic_media('session-two.wav')]
    plan = config(tmp_path, sources)
    root = tmp_path / 'batch'
    monkeypatch.setattr('src.cli.Transcriber', lambda **kw: Transcriber(client=interview_client, **kw))
    assert main(['prepare', '--plan', str(plan), '--batch', str(root), '--copy-local-files']) == 0
    return root


def run(root, phase='raw', *flags):
    return main(['run', '--batch', str(root), '--phase', phase, '--send-to-openai', '--interview', *flags])


def latest_summary(root):
    path = max(root.glob('summary-*.json'), key=lambda path: path.stat().st_mtime_ns)
    return json.loads(path.read_text())


def test_missing_attribution_allows_parallel_original_review_and_cache_reuse(interview_batch, interview_client, capsys):
    root = interview_batch
    assert main(['run', '--batch', str(root), '--send-to-openai']) == 0
    originals = {p: p.read_bytes() for p in root.rglob('transcription.txt')}
    audio_calls = interview_client.audio.transcriptions.create.call_count
    assert run(root, 'review', '--parallel-interviews', '2') == 1
    summary = latest_summary(root)
    assert summary['blocked'] == 2 and summary['failed'] == summary['completed'] == 0
    assert interview_client.audio.transcriptions.create.call_count == audio_calls
    assert len(list(root.rglob('review_report.json'))) == 2
    for row in summary['items']:
        assert row['families']['original']['author_review'] == 'complete'
        assert row['families']['attributed']['author_review'] == 'blocked'
        assert row['family_blockers'] == {'attributed': 'raw_prerequisite'}
    calls = interview_client.chat.completions.create.call_count
    assert run(root, 'review', '--parallel-interviews', '2') == 1
    assert interview_client.chat.completions.create.call_count == calls
    assert all(path.read_bytes() == value for path, value in originals.items())
    capsys.readouterr()
    assert main(['status', '--batch', str(root)]) == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    latest = [r for r in events if r['status'] == 'latest' and r['phase'] == 'review']
    assert len(latest) == 4
    assert all(r['recorded_status'] == ('blocked' if r['family'] == 'attributed' else 'complete') for r in latest)
    assert all(r.get('blocked_reason') == 'raw_prerequisite' for r in latest if r['family'] == 'attributed')


def test_missing_all_raw_reports_blocked_without_initializing_provider(interview_batch, monkeypatch, capsys):
    monkeypatch.setattr('src.cli.Transcriber', lambda **kw: pytest.fail('A prerequisite must block before provider initialization.'))
    capsys.readouterr()
    assert run(interview_batch, 'review', '--parallel-interviews', '2') == 1
    summary = latest_summary(interview_batch)
    assert summary['blocked'] == 2 and summary['failed'] == 0
    assert all(row['family_blockers'] == dict(original='raw_prerequisite', attributed='raw_prerequisite')
               for row in summary['items'])
    text = capsys.readouterr().out
    assert 'OPENAI_API_KEY' not in text and 'provider_unknown' not in text
    assert json.loads(text.splitlines()[-1])['provider_requests'] == 0


def test_attributed_review_can_continue_when_combined_original_is_invalid(interview_batch, interview_client):
    root = interview_batch
    assert run(root) == 0
    original = next((root / 'item-0001/output/interviews').rglob('transcription.txt'))
    original.write_bytes(b'SYNTHETIC_PRIVATE_TAMPERING')
    audio = interview_client.audio.transcriptions.create.call_count
    assert run(root, 'review', '--select', 'entry-1') == 1
    row = latest_summary(root)['items'][0]
    assert row['status'] == 'blocked' and row['family_blockers'] == {'original': 'raw_prerequisite'}
    assert row['families']['attributed']['author_review'] == 'complete'
    assert len(list(family(root).rglob('review_report.json'))) == 1
    assert not list(original.parent.rglob('review_report.json'))
    assert original.read_bytes() == b'SYNTHETIC_PRIVATE_TAMPERING'
    assert interview_client.audio.transcriptions.create.call_count == audio


@pytest.mark.parametrize('artifact', ['transcription.txt', 'audio.wav', 'manifest.json'])
def test_invalid_shared_part_blocks_both_families_without_requests(interview_batch, interview_client, artifact):
    root = interview_batch
    assert run(root) == 0
    path = next((root / 'item-0001/output/parts').rglob(artifact))
    path.write_bytes(b'SYNTHETIC_PRIVATE_TAMPERING')
    calls = interview_client.chat.completions.create.call_count
    assert run(root, 'review', '--select', 'entry-1') == 1
    assert interview_client.chat.completions.create.call_count == calls
    row = latest_summary(root)['items'][0]
    assert row['status'] == 'blocked' and set(row['family_blockers']) == {'original', 'attributed'}
    assert path.read_bytes() == b'SYNTHETIC_PRIVATE_TAMPERING'


def test_original_text_validation_failure_does_not_block_ready_attribution(interview_batch, interview_client):
    from src.progress import CURRENT
    root = interview_batch
    assert run(root) == 0
    respond = interview_client.chat.completions.create.side_effect
    def chat(**kwargs):
        if CURRENT.get().context.get('family') == 'original':
            supplied = json.loads(kwargs['messages'][-1]['content'])
            body = dict(chunk_index=supplied['chunk_index'], text='Invented extra words.', speaker_uncertain=False)
            return SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop',
                message=SimpleNamespace(content=json.dumps(body), refusal=None))])
        return respond(**kwargs)
    interview_client.chat.completions.create.side_effect = chat
    assert run(root, 'review', '--select', 'entry-1') == 1
    assert latest_summary(root)['failed'] == 1
    assert latest_summary(root)['items'][0]['families']['attributed']['author_review'] == 'complete'
    assert not list((root / 'item-0001/output/interviews').rglob('review_report.json'))


def test_parallel_text_phases_share_request_allowance_after_family_split(interview_batch, interview_client, capsys):
    import threading
    root = interview_batch
    assert run(root) == 0
    barrier = threading.Barrier(2)
    respond = interview_client.chat.completions.create.side_effect
    def chat(**kwargs):
        barrier.wait(timeout=5)
        return respond(**kwargs)
    interview_client.chat.completions.create.side_effect = chat
    capsys.readouterr()
    assert run(root, 'review', '--parallel-interviews', '2', '--max-provider-requests', '2', '--provider-retries', '0') == 1
    assert interview_client.chat.completions.create.call_count == 2
    summary = latest_summary(root)
    assert summary['incomplete'] == 2 and summary['failed'] == summary['blocked'] == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert events[-1]['provider_requests'] == 2 and events[-1]['stop_reason'] == 'request_limit'
    assert all(row['families']['attributed']['preflight'] == 'not_attempted' for row in summary['items'])


def test_missing_human_approval_is_blocked_per_family_with_no_requests(interview_batch, interview_client):
    assert run(interview_batch) == 0
    assert run(interview_batch, 'review') == 0
    calls = interview_client.chat.completions.create.call_count
    assert run(interview_batch, 'chapters', '--select', 'entry-1') == 1
    row = latest_summary(interview_batch)['items'][0]
    assert row['status'] == 'blocked'
    assert row['family_blockers'] == dict(original='human_review_required', attributed='human_review_required')
    assert interview_client.chat.completions.create.call_count == calls


def family(root, item=1):
    return next(p.parent for p in (root / f'item-{item:04d}/output/attributed').rglob('transcription.txt'))


def settings(tmp_path, rows):
    path = tmp_path / 'speakers.json'
    path.write_text(json.dumps({'version': 1, 'interviews': rows}))
    return path


def test_unknown_names_all_families_resume_and_exact_approved_review(interview_batch, interview_client, capsys):
    root = interview_batch
    assert run(root) == 0
    assert interview_client.audio.transcriptions.create.call_count == 4
    for index in (1, 2):
        job = family(root, index)
        state = json.loads((job / 'manifest.json').read_bytes())
        provenance = json.loads((job / 'provenance.json').read_bytes())
        assert state['version'] == 'attributed-interview-v3'
        assert all(t['role'] is None and t['identity_evidence'] == 'unidentified' for t in provenance['attribution']['turns'])
        assert 'Speaker A' in (job / 'transcription.txt').read_text()
    assert run(root, 'review') == 0
    before = {p: p.read_bytes() for p in root.rglob('review_report.*')}
    calls = interview_client.audio.transcriptions.create.call_count
    reviews = sum(c.kwargs['response_format']['json_schema']['name'] == 'source_grounded_author_review'
                  for c in interview_client.chat.completions.create.call_args_list)
    flags = ('--select', 'entry-1', '--human-reviewed', '--chapters', 'interview')
    assert run(root, 'chapters', *flags) == 0
    assert len(list(root.rglob('chapter_drafts.json'))) == 2
    assert all(p.read_bytes() == data for p, data in before.items())
    assert interview_client.audio.transcriptions.create.call_count == calls
    assert sum(c.kwargs['response_format']['json_schema']['name'] == 'source_grounded_author_review'
               for c in interview_client.chat.completions.create.call_args_list) == reviews
    chats = interview_client.chat.completions.create.call_count
    assert run(root, 'chapters', *flags) == 0
    assert interview_client.chat.completions.create.call_count == chats
    assert str(root) not in capsys.readouterr().out


def test_per_entry_mapping_changes_reuse_asr_but_require_new_attributed_raw(interview_batch, interview_client, tmp_path, capsys):
    root = interview_batch
    path = settings(tmp_path, [{'id': 'entry-1', 'interviewer_name': 'Synthetic Host',
        'speaker_map': ['1:1:A=interviewer']}, {'id': 'entry-2', 'enabled': False}])
    flags = ('--speaker-config', str(path))
    assert run(root, 'raw', *flags) == 0
    assert not (root / 'item-0002/output/attributed').exists()
    assert 'Synthetic Host' in (family(root) / 'transcription.txt').read_text()
    assert 'Speaker B' in (family(root) / 'transcription.txt').read_text()
    calls = interview_client.audio.transcriptions.create.call_count
    old = family(root)
    before = {p: p.read_bytes() for p in old.rglob('*') if p.is_file()}
    path.write_text(json.dumps({'version': 1, 'interviews': [{'id': 'entry-1',
        'interviewee_name': 'Synthetic Guest', 'speaker_map': ['1:1:B=interviewee']}, {'id': 'entry-2', 'enabled': False}]}))
    assert run(root, 'review', *flags) == 1  # Never buy another raw pass in review.
    assert interview_client.audio.transcriptions.create.call_count == calls
    assert run(root, 'raw', *flags) == 0
    assert interview_client.audio.transcriptions.create.call_count == calls
    assert before == {p: p.read_bytes() for p in old.rglob('*') if p.is_file()}
    assert len(list((root / 'item-0001/output/attributed').rglob('transcription.txt'))) == 2
    logs = capsys.readouterr().out
    assert 'Synthetic Host' not in logs and 'Synthetic Guest' not in logs and str(path) not in logs


@pytest.mark.parametrize('row', [
    {'id': 'unknown'}, {'id': 'entry-1', 'interviewer_name': ''},
    {'id': 'entry-1', 'speaker_map': ['1:1:A=interviewer']},
    {'id': 'entry-1', 'interviewer_name': 'Host', 'speaker_map': ['A=interviewer']},
    {'id': 'entry-1', 'enabled': 'yes'}, {'id': 'entry-1', 'reference_audio': '/synthetic/private'},
    {'id': 'entry-1', 'interviewer_name': 'Same', 'interviewee_name': 'same'},
    {'id': 'entry-1', 'enabled': False, 'interviewer_name': 'Host'}])
def test_invalid_settings_fail_before_provider(interview_batch, interview_client, tmp_path, row):
    path = settings(tmp_path, [row])
    assert run(interview_batch, 'raw', '--speaker-config', str(path)) == 1
    assert interview_client.audio.transcriptions.create.call_count == 0


@pytest.mark.parametrize('flag', ['--speaker-config', '--interview-model'])
def test_speaker_flags_require_opt_in(interview_batch, interview_client, flag):
    assert main(['run', '--batch', str(interview_batch), '--send-to-openai', flag, 'synthetic']) == 1
    assert interview_client.audio.transcriptions.create.call_count == 0


@pytest.mark.parametrize('high_family', ['original', 'attributed'])
def test_each_familys_high_gate_blocks_only_its_chapters(interview_batch, interview_client, high_family):
    interview_client.high_family = high_family
    assert run(interview_batch) == 0
    assert run(interview_batch, 'review') == 0
    calls = interview_client.chat.completions.create.call_count
    assert run(interview_batch, 'chapters', '--human-reviewed', '--select', 'entry-1') == 1
    assert interview_client.chat.completions.create.call_count > calls
    drafts = list(interview_batch.rglob('chapter_drafts.json'))
    assert len(drafts) == 1
    assert ('attributed' in drafts[0].parts) == (high_family == 'original')
    summary = latest_summary(interview_batch)
    assert summary['blocked'] == 1 and summary['failed'] == 0
    assert summary['items'][0]['family_blockers'] == {high_family: 'high_findings'}


@pytest.mark.parametrize('artifact', ['transcription.txt', 'provenance.json', 'review_report.json', 'review_report.xlsx', 'manifest.json'])
def test_changed_attributed_approval_preserves_eligible_original_chapters(interview_batch, interview_client, monkeypatch, artifact):
    from src.batch import runner
    root = interview_batch
    assert run(root) == 0
    assert run(root, 'review') == 0
    gate = runner.gate_attribution
    def changed(*a, **kw):
        approval = gate(*a, **kw)
        path = next(approval.job.rglob(artifact)) if artifact.startswith('review_') else approval.job / artifact
        path.write_bytes(b'SYNTHETIC_PRIVATE_TAMPERING')
        return approval
    monkeypatch.setattr(runner, 'gate_attribution', changed)
    calls = interview_client.chat.completions.create.call_count
    assert run(root, 'chapters', '--human-reviewed', '--select', 'entry-1') == 1
    assert interview_client.chat.completions.create.call_count > calls
    drafts = list(root.rglob('chapter_drafts.json'))
    assert len(drafts) == 1 and 'interviews' in drafts[0].parts
    assert latest_summary(root)['items'][0]['family_blockers'] == {'attributed': 'review_prerequisite'}


def test_unknown_mode_requires_provider_consent_and_action(interview_batch, interview_client):
    assert main(['run', '--batch', str(interview_batch), '--interview']) == 1
    assert main(['status', '--batch', str(interview_batch), '--interview']) == 1
    assert run(interview_batch, 'raw', '--interview-model', 'whisper-1') == 1
    assert interview_client.audio.transcriptions.create.call_count == 0


def test_confirmed_guest_never_assigns_unknown_interviewer(interview_batch, interview_client, tmp_path):
    cfg = settings(tmp_path, [{'id': 'entry-1', 'interviewee_name': 'Synthetic Guest',
                            'speaker_map': ['1:1:B=interviewee']}])
    assert run(interview_batch, 'raw', '--speaker-config', str(cfg), '--select', 'entry-1') == 0
    provenance = json.loads((family(interview_batch) / 'provenance.json').read_bytes())
    turns = provenance['attribution']['turns']
    assert [t['role'] for t in turns] == [None, 'interviewee', None]
    assert turns[0]['display_name'] == 'Speaker A'
    assert turns[1]['display_name'] == 'Synthetic Guest'


def test_duplicate_config_ids_and_json_keys_fail_before_requests(interview_batch, interview_client, tmp_path):
    cfg = settings(tmp_path, [{'id': 'entry-1'}, {'id': 'entry-1'}])
    assert run(interview_batch, 'raw', '--speaker-config', str(cfg)) == 1
    cfg.write_text('{"version":1,"version":1,"interviews":[]}')
    assert run(interview_batch, 'raw', '--speaker-config', str(cfg)) == 1
    assert interview_client.audio.transcriptions.create.call_count == 0


def test_missing_attributed_review_or_mapping_never_buy_attributed_chapters(interview_batch, interview_client, tmp_path):
    root = interview_batch
    assert run(root) == 0
    # Original-only review cannot approve the attributed family.
    assert main(['run', '--batch', str(root), '--phase', 'review', '--send-to-openai']) == 0
    calls = interview_client.chat.completions.create.call_count
    assert run(root, 'chapters', '--select', 'entry-1', '--human-reviewed') == 1
    assert interview_client.chat.completions.create.call_count > calls
    assert not list((root / 'item-0001/output/attributed').rglob('chapter_drafts.json'))
    assert run(root, 'review') == 0
    cfg = settings(tmp_path, [{'id': 'entry-1', 'interviewee_name': 'Synthetic Guest',
                             'speaker_map': ['1:1:B=interviewee']}])
    calls = interview_client.chat.completions.create.call_count
    assert run(root, 'chapters', '--select', 'entry-1', '--human-reviewed', '--speaker-config', str(cfg)) == 1
    assert interview_client.chat.completions.create.call_count == calls
    assert not list((root / 'item-0001/output/attributed').rglob('chapter_drafts.json'))


def test_changed_complete_attributed_chapter_settings_preserve_its_existing_drafts(interview_batch, interview_client):
    root = interview_batch
    assert run(root) == 0
    assert run(root, 'review') == 0
    assert run(root, 'chapters', '--select', 'entry-1', '--human-reviewed', '--chapters', 'interview') == 0
    calls = interview_client.chat.completions.create.call_count
    before = {p: p.read_bytes() for p in (root / 'item-0001/output/attributed').rglob('*') if p.is_file()}
    assert run(root, 'chapters', '--select', 'entry-1', '--human-reviewed', '--chapters', 'narrative') == 0
    assert interview_client.chat.completions.create.call_count > calls  # eligible original generation
    assert all(p.read_bytes() == content for p, content in before.items() if p.name != 'manifest.json')
    assert len(list((root / 'item-0001/output/attributed').rglob('chapter_drafts.json'))) == 2


def test_original_approval_cannot_be_substituted_for_attributed_family(interview_batch, interview_client, monkeypatch):
    from src.batch import runner
    root = interview_batch
    assert run(root) == 0
    assert run(root, 'review') == 0
    def swapped(root, item, phase, options, author_options, *args):
        return runner.gate(root, item, phase, options, author_options)
    monkeypatch.setattr(runner, 'gate_attribution', swapped)
    calls = interview_client.chat.completions.create.call_count
    assert run(root, 'chapters', '--select', 'entry-1', '--human-reviewed') == 1
    assert interview_client.chat.completions.create.call_count > calls
    drafts = list(root.rglob('chapter_drafts.json'))
    assert len(drafts) == 1 and 'interviews' in drafts[0].parts
    assert latest_summary(root)['items'][0]['family_blockers'] == {'attributed': 'review_prerequisite'}


def test_batch_v3_provenance_checksum_is_required_on_raw_resume(interview_batch, interview_client):
    root = interview_batch
    assert run(root) == 0
    job = family(root)
    path = job / 'manifest.json'
    state = json.loads(path.read_bytes())
    state['provenance_sha256'] = '0' * 64
    path.write_text(json.dumps(state))
    before = path.read_bytes()
    calls = interview_client.audio.transcriptions.create.call_count
    assert run(root, 'raw', '--select', 'entry-1') == 1
    assert interview_client.audio.transcriptions.create.call_count == calls
    assert path.read_bytes() == before


@pytest.mark.parametrize('target_stage', ['enhancement', 'author_review'])
def test_breaker_stop_preserves_partial_family_and_reports_incomplete(
        interview_batch, interview_client, target_stage, capsys):
    from src.progress import CURRENT
    from src.provider_control import CURRENT_CONTROL
    root = interview_batch
    assert run(root) == 0
    normal = interview_client.chat.completions.create.side_effect
    def answer(**kw):
        result = normal(**kw)
        name = kw['response_format']['json_schema']['name']
        matching = ('group_edit' in name if target_stage == 'enhancement' else 'review' in name)
        if CURRENT.get().context.get('family') == 'attributed' and matching:
            # A separate worker trips the shared breaker while this valid call drains.
            CURRENT_CONTROL.get().reason = 'validation_failures'
        return result
    interview_client.chat.completions.create.side_effect = answer
    # Enhancement stops the subsequent review; a final admitted review drains.
    capsys.readouterr()
    code = run(root, 'review', '--select', 'entry-1')
    summary = latest_summary(root)
    if target_stage == 'enhancement':
        assert code == 1
        assert summary['incomplete'] == 1 and summary['failed'] == 0
        row = summary['items'][0]
        assert row['families']['original']['phase_result'] == 'complete'
        assert row['families']['attributed']['enhancement'] == 'complete'
        assert row['families']['attributed']['author_review'] == 'incomplete'
        assert list(root.rglob('review_report.xlsx'))
    else:
        # A single final review chunk that was already admitted is complete.
        assert code == 0 and summary['completed'] == 1 and summary['failed'] == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert not any(e.get('stage_status') == 'failed' for e in events)


def test_primary_validation_trigger_remains_failed_while_later_family_is_unattempted(
        interview_batch, interview_client):
    from src.progress import CURRENT
    root = interview_batch
    assert run(root) == 0
    raw_snapshots = {p: p.read_bytes() for p in root.rglob('transcription.txt')}
    normal = interview_client.chat.completions.create.side_effect
    def answer(**kw):
        if CURRENT.get().context.get('family') == 'original':
            payload = json.loads(kw['messages'][-1]['content'])
            body = dict(chunk_index=payload['chunk_index'], text='Altered words.', speaker_uncertain=False)
            return SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop',
                message=SimpleNamespace(content=json.dumps(body), refusal=None))])
        return normal(**kw)
    interview_client.chat.completions.create.side_effect = answer
    assert run(root, 'review', '--select', 'entry-1', '--validation-failure-limit', '1') == 1
    summary = latest_summary(root)
    assert summary['failed'] == 1 and summary['incomplete'] == 0
    assert summary['items'][0]['families']['original']['phase_result'] == 'failed'
    assert summary['items'][0]['families']['attributed']['phase_result'] == 'not_attempted'
    assert interview_client.chat.completions.create.call_count == 2
    assert all(p.read_bytes() == value for p, value in raw_snapshots.items())
