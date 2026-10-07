"""Measured outcomes, nested coordinators, terminal rendering and output modes."""

import io
import json

import pytest

from src.console_progress import ProgressState
from src.progress import CURRENT, Reporter, emit_progress


def event(**fields):
    return {'elapsed_seconds': 1, 'scope': 'batch', **fields}


def test_parallel_items_stages_and_outcome_accounting():
    state = ProgressState('batch')
    state.update(event(status='progress', selected=4, item=2, stage='transcription',
                       stage_status='running', part=1, parts=2, chunk=2, chunks=5))
    state.update(event(status='progress', item=7, stage='author_review', stage_status='running'))
    assert state.counts['active'] == 2 and state.counts['queued'] == 2
    assert state.active[2]['stage'] == 'transcription'
    assert state.active[7]['stage'] == 'author_review'
    state.update(event(status='progress', item=2, stage='transcription', stage_status='failed',
                       part=1, chunk=2, error_category='quota'))
    state.update(event(status='failed', item=2))
    state.update(event(status='failed', item=2))
    state.update(event(status='complete', item=7))
    state.update(event(status='blocked', item=8))
    assert state.counts == {'succeeded': 1, 'failed': 1, 'blocked': 1, 'interrupted': 0,
                            'resolved': 3, 'active': 0, 'errors': 1, 'queued': 1}


def test_nested_interview_cannot_finish_batch_item_or_replace_total():
    state = ProgressState('batch')
    state.update(event(status='progress', selected=3, item=5, stage='preflight', stage_status='running'))
    state.update(event(scope='interview', status='progress', item=5, stage='author_review',
                       stage_status='complete', chunk=1, chunks=1))
    state.update(event(scope='interview', status='complete', item=5))
    state.update(event(scope='interview', status='summary', selected=1, processed=1, failed=0))
    assert state.selected == 3 and state.counts['active'] == 1
    assert state.counts['succeeded'] == 0 and not state.finished
    state.update(event(status='complete', item=5))
    state.update(event(status='summary', selected=3, processed=1, failed=0))
    assert state.counts['succeeded'] == 1 and state.finished


def test_chunk_and_stage_failures_count_once_but_distinct_chunks_count_separately():
    state = ProgressState('batch')
    for chunk in (1, 2):
        state.update(event(status='progress', item=3, stage='author_review', stage_status='failed',
                           chunk=chunk, chunks=3))
    state.update(event(status='progress', item=3, stage='author_review', stage_status='failed'))
    state.update(event(status='failed', item=3))
    assert state.counts['errors'] == 2
    assert state.counts['failed'] == 1


def test_chunk_failures_in_different_recordings_are_distinct():
    state = ProgressState('batch')
    for part in (1, 2):
        state.update(event(status='progress', item=3, stage='diarization', stage_status='running',
                           part=part, parts=2, family='attributed'))
        state.update(event(status='progress', item=3, stage='diarization', stage_status='failed',
                           chunk=1, chunks=3))
    state.update(event(status='failed', item=3))
    assert state.counts['errors'] == 2


def test_plain_historical_summaries_keep_phase_and_do_not_deduplicate():
    stream = io.StringIO()
    reporter = Reporter(stream)
    reporter.context = {'scope': 'batch', 'phase': 'status'}
    reporter.configure_console(mode='plain')
    for phase in ('raw', 'raw', 'review'):
        reporter.emit(status='summary', phase=phase, processed=3, failed=1, blocked=1, interrupted=0)
    reporter.close()
    assert stream.getvalue().count('Summary (raw)') == 2
    assert 'Summary (review)' in stream.getvalue()
    assert stream.getvalue().count('1 succeeded') == 3


def test_new_stage_and_part_reset_chunk_counts_and_global_stages_clear_parts():
    state = ProgressState('interview')
    state.update(event(scope='interview', status='progress', item=1, stage='transcription',
                       stage_status='complete', part=1, parts=2, chunk=8, chunks=8))
    state.update(event(scope='interview', status='progress', item=1, stage='transcription',
                       stage_status='running', part=2, parts=2))
    assert 'chunk' not in state.active[1] and 'chunks' not in state.active[1]
    state.update(event(scope='interview', status='progress', item=1, stage='author_review',
                       stage_status='running', chunk=1, chunks=3))
    assert 'part' not in state.active[1] and 'parts' not in state.active[1]


def test_interrupt_preserves_completed_work_without_counting_active_as_finished():
    state = ProgressState('batch')
    state.update(event(status='complete', selected=3, item=1))
    state.update(event(status='progress', item=2, stage='staging', stage_status='running'))
    state.update(event(status='interrupted'))
    assert state.counts['succeeded'] == 1 and state.counts['resolved'] == 1
    assert state.counts['active'] == 0 and state.counts['interrupted'] == 1
    assert state.counts['queued'] == 1 and '1 interrupted' in state.summary()


def test_plain_output_is_readable_private_and_durable_json_is_unchanged(tmp_path):
    stream = io.StringIO()
    reporter = Reporter(stream)
    reporter.context = {'scope': 'interview', 'item': 1, 'selected': 2}
    reporter.configure_console(mode='plain')
    reporter.start(tmp_path / 'logs')
    reporter.emit(status='progress', stage='transcription', stage_status='running', chunk=1, chunks=3,
                  message='SYNTHETIC_SECRET', path='/private/source')
    reporter.emit(status='progress', stage='transcription', stage_status='failed', chunk=1,
                  error_category='quota')
    reporter.emit(status='failed')
    reporter.context['item'] = 2
    reporter.emit(status='progress', stage='conversion', stage_status='skipped')
    reporter.emit(status='complete')
    reporter.emit(status='summary', processed=2, failed=1)
    reporter.close()
    text = stream.getvalue()
    assert 'Transcribing audio' in text and 'chunk 1/3' in text
    assert 'Prepared audio (cache reused)' in text
    assert '1 succeeded' in text and '1 failed' in text and '1 errors' in text
    assert '\x1b' not in text and '\r' not in text and 'SYNTHETIC_SECRET' not in text
    assert '/private/source' not in text
    rows = [json.loads(line) for line in next((tmp_path / 'logs').glob('*.jsonl')).read_text().splitlines()]
    assert rows[-1]['status'] == 'summary' and rows[-1]['failed'] == 1
    assert rows[1]['chunk'] == 1 and rows[2]['error_category'] == 'quota'


@pytest.mark.parametrize('mode', ['auto', 'plain', 'json'])
def test_quiet_retains_every_private_event(tmp_path, mode):
    stream = io.StringIO()
    reporter = Reporter(stream)
    reporter.configure_console(mode=mode, quiet=True)
    reporter.start(tmp_path / 'logs')
    reporter.emit(status='failed', error_category='authentication')
    reporter.close()
    assert stream.getvalue() == ''
    rows = [json.loads(line) for line in next((tmp_path / 'logs').glob('*.jsonl')).read_text().splitlines()]
    assert [row['status'] for row in rows] == ['started', 'failed']


class Terminal(io.StringIO):
    def isatty(self):
        return True


@pytest.mark.parametrize('width', [40, 80, 120])
@pytest.mark.parametrize('no_color', [True, False])
def test_live_rendering_width_cleanup_and_color(monkeypatch, width, no_color):
    monkeypatch.setenv('TERM', 'xterm-256color')
    monkeypatch.delenv('NO_COLOR', raising=False)
    stream = Terminal()
    reporter = Reporter(stream)
    reporter.context = {'scope': 'batch', 'selected': 3}
    reporter.configure_console(no_color=no_color)
    view = reporter.console
    view.console.width = width
    reporter.emit(status='progress', item=1, stage='transcription', stage_status='running', chunk=2, chunks=5)
    reporter.emit(status='progress', item=2, stage='author_review', stage_status='running', chunk=1, chunks=3)
    # Inspect a static frame separately from terminal redraw controls.
    with view.console.capture() as capture:
        view.console.print(view.render())
    frame = capture.get()
    assert 'Item 1' in frame and 'Item 2' in frame and '2 active' in frame
    assert '0/3 items finished' in frame and 'chunk' in frame
    reporter.emit(status='heartbeat')
    reporter.emit(status='complete', item=1)
    reporter.emit(status='failed', item=2)
    reporter.emit(status='summary', processed=2, failed=1)
    reporter.close()
    assert not view.started and not view.live.is_started
    assert '\x1b[?25h' in stream.getvalue()  # Cursor restored after transient view.
    if no_color:
        assert '\x1b[31m' not in frame and '\x1b[36m' not in frame


def test_no_color_environment_and_dumb_terminal(monkeypatch):
    monkeypatch.setenv('TERM', 'xterm')
    monkeypatch.setenv('NO_COLOR', '')
    reporter = Reporter(Terminal())
    reporter.configure_console()
    assert reporter.console.console.no_color
    reporter.close()
    monkeypatch.setenv('TERM', 'dumb')
    stream = Terminal()
    reporter = Reporter(stream)
    reporter.configure_console()
    reporter.emit(status='complete')
    reporter.close()
    assert reporter.console is None and json.loads(stream.getvalue())['status'] == 'complete'


def test_heartbeat_does_not_advance_measured_work(monkeypatch):
    monkeypatch.setenv('TERM', 'xterm')
    reporter = Reporter(Terminal())
    reporter.context = {'scope': 'batch', 'item': 1, 'selected': 4}
    reporter.configure_console()
    reporter.emit(status='progress', stage='transcription', stage_status='running', chunk=1, chunks=5)
    counts = reporter.console.state.counts.copy()
    reporter.last -= 40
    reporter.emit(status='heartbeat')
    assert reporter.console.state.counts == counts
    with reporter.console.console.capture() as capture:
        reporter.console.console.print(reporter.console.render())
    assert 'completion is unconfirmed' in capture.get()
    reporter.close()


def test_reporter_nested_configuration_keeps_parent_view():
    reporter = Reporter(io.StringIO())
    reporter.context = {'scope': 'batch'}
    reporter.configure_console(mode='plain')
    view = reporter.console
    reporter.context = {'scope': 'interview', 'item': 5}
    reporter.configure_console(mode='auto', quiet=True)
    token = CURRENT.set(reporter)
    try:
        emit_progress('transcription', 'running', chunk=1, chunks=2)
    finally:
        CURRENT.reset(token)
        reporter.close()
    assert reporter.console is view and not reporter.quiet
    assert view.state.active[5]['chunk'] == 1
