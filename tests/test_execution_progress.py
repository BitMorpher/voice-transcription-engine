"""Progress survives real CLI boundaries without mixing recordings or batches."""

import json

from src.batch.cli import main as batch_main
from src.cli import entrypoint
from src.transcriber import Transcriber


def rows(capsys):
    return [json.loads(line) for line in capsys.readouterr().out.splitlines()]


def test_folder_requests_belong_to_their_input_and_advance_after_completion(
        monkeypatch, synthetic_media, tmp_path, provider, capsys):
    first = synthetic_media('private-a.wav')
    synthetic_media('private-b.wav')
    monkeypatch.setattr('src.cli.Transcriber', lambda **kwargs: Transcriber(client=provider, **kwargs))
    assert entrypoint(['--pipeline', '--input-folder', str(first.parent),
                       '--output-folder', str(tmp_path / 'output'), '--progress', 'json']) == 0
    events = rows(capsys)
    requests = [event for event in events if 'chunk' in event]
    assert {event['item'] for event in requests} == {1, 2}
    assert all(event['selected'] == 2 for event in requests)
    results = [event for event in events if event['status'] == 'complete' and 'finished' in event]
    assert [event['finished'] for event in results] == [1, 2]
    assert events[-1]['finished'] == events[-1]['completed'] == 2
    assert 'item' not in events[-1]
    assert 'private-a' not in json.dumps(events)


def test_ordered_interview_sections_and_completed_parts_are_separate(
        monkeypatch, synthetic_media, tmp_path, provider, capsys):
    first, second = synthetic_media('part-a.wav'), synthetic_media('part-b.wav')
    manifest = tmp_path / 'recordings.json'
    manifest.write_text(json.dumps({'version': 1, 'interview_id': 'private-session',
                                   'parts': [{'id': 'a', 'path': first.name},
                                             {'id': 'b', 'path': second.name}]}))
    monkeypatch.setattr('src.cli.Transcriber', lambda **kwargs: Transcriber(client=provider, **kwargs))
    assert entrypoint(['--author-workflow', '--recordings-list', str(manifest),
                       '--steps', 'raw', '--output-folder', str(tmp_path / 'output'),
                       '--progress', 'json']) == 0
    events = rows(capsys)
    requests = [event for event in events if 'chunk' in event]
    assert {event['part'] for event in requests} == {1, 2}
    assert all(event['parts'] == 2 for event in requests)
    parts = [event for event in events if event.get('stage') == 'part_transcription'
             and event.get('stage_status') == 'complete' and 'part' in event]
    assert [event['part'] for event in parts] == [1, 2]
    assert 'part' not in events[-1] and events[-1]['finished'] == 1
    assert 'private-session' not in json.dumps(events)


def test_sparse_batch_selection_uses_selected_order_and_final_dispositions(tmp_path, capsys):
    interviews = []
    for index in range(4):
        source = tmp_path / f'private-{index}.wav'
        source.write_bytes(b'synthetic metadata only')
        manifest = tmp_path / f'recordings-{index}.json'
        manifest.write_text(json.dumps({'version': 1, 'interview_id': f'session-{index}',
                                       'parts': [{'id': 'a', 'path': source.name}]}))
        interviews.append({'id': f'entry-{index}', 'manifest': manifest.name,
                           'status': 'blocked' if index == 3 else 'ready'})
    plan = tmp_path / 'plan.json'
    plan.write_text(json.dumps({'version': 1, 'interviews': interviews}))
    assert batch_main(['inventory', '--batch-plan', str(plan), '--select', 'entry-1',
                       '--select', 'entry-3', '--progress', 'json']) == 1
    events = rows(capsys)
    starts = [event for event in events if event.get('stage_status') == 'running']
    assert [(event['item'], event['batch_position'], event['selected']) for event in starts] == [
        (2, 1, 2), (4, 2, 2)]
    results = [event for event in events if 'finished' in event and 'item' in event]
    assert [event['finished'] for event in results] == [1, 2]
    assert events[-1]['finished'] == 2 and events[-1]['completed'] == events[-1]['blocked'] == 1
    assert 'private-' not in json.dumps(events)
