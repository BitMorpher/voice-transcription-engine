"""Human status is privacy-safe and counts completed work, never started work."""

import io
import json
import os
import threading
import time

import pytest

from src.progress import LogError, Reporter


class Terminal(io.StringIO):
    def isatty(self):
        return True


@pytest.fixture
def terminal(monkeypatch):
    monkeypatch.setenv('TERM', 'xterm-256color')
    monkeypatch.setenv('COLUMNS', '110')
    monkeypatch.setenv('LINES', '24')
    return Terminal()


def test_default_json_and_auto_redirects_remain_json(monkeypatch):
    monkeypatch.setenv('TERM', 'xterm')
    for mode in ('json', 'auto'):
        stream = io.StringIO()
        reporter = Reporter(stream, output=mode)
        reporter.emit(status='progress', stage='transcription', stage_status='running')
        reporter.close()
        assert json.loads(stream.getvalue())['stage'] == 'transcription'
        assert '\x1b' not in stream.getvalue()


@pytest.mark.parametrize('term', ['dumb', ''])
def test_auto_requires_terminal_capabilities(monkeypatch, term):
    monkeypatch.setenv('TERM', term)
    stream = Terminal()
    reporter = Reporter(stream, output='auto')
    reporter.emit(status='started')
    reporter.close()
    assert json.loads(stream.getvalue())['status'] == 'started'
    assert '\x1b' not in stream.getvalue()


def test_plain_words_reuse_failures_and_private_json_logs(tmp_path):
    stream = io.StringIO()
    reporter = Reporter(stream, output='plain')
    reporter.start(tmp_path / 'logs')
    reporter.context = {'scope': 'interview', 'item': 1}
    reporter.emit(status='progress', stage='transcription', stage_status='running',
                  chunk=1, chunks=2, path='/private/SECRET', message='SECRET')
    reporter.emit(status='progress', stage='transcription', stage_status='skipped', chunk=1, chunks=2)
    reporter.emit(status='progress', stage='transcription', stage_status='failed', chunk=2, chunks=2,
                  error_category='authentication', credential='SECRET')
    reporter.emit(status='summary', processed=1, failed=1)
    reporter.close()
    output = stream.getvalue()
    assert 'Transcribing audio' in output and 'reused saved result' in output
    words = ' '.join(output.split())
    assert '0/2 sections complete' in words and '1/2 sections complete' in words
    assert 'failed' in output and 'Next:' in output
    assert 'SECRET' not in output and '\x1b' not in output
    rows = [json.loads(line) for line in next((tmp_path / 'logs').glob('*.jsonl')).read_text().splitlines()]
    assert rows[2]['cache_reused'] and rows[3]['stage_status'] == 'failed'
    assert 'SECRET' not in json.dumps(rows)
    assert 'Transcribing audio' not in json.dumps(rows)


def test_known_chunks_are_scoped_by_item_family_stage_and_part(terminal):
    reporter = Reporter(terminal, output='auto')
    for item, family, part in [(7, 'original', 1), (7, 'attributed', 1), (11, 'original', 1),
                               (7, 'original', 2)]:
        reporter.context = {'scope': 'interview', 'item': item, 'family': family, 'part': part}
        reporter.emit(status='progress', stage='transcription', stage_status='running', chunk=1, chunks=3)
        assert '0/3 sections complete' in terminal.getvalue().split('\x1b[2K')[-1]
        reporter.emit(status='progress', stage='transcription', stage_status='complete', chunk=1, chunks=3)
        reporter.emit(status='progress', stage='transcription', stage_status='skipped', chunk=1, chunks=3)
        key = (item, family, 'transcription', part)
        assert reporter._renderer.sections[key]['done'] == {1}
    reporter.close()
    assert 'Speaker transcript' in terminal.getvalue()
    assert terminal.getvalue().endswith('\x1b[?25h')


def test_running_and_failed_requests_never_count_as_finished(terminal):
    reporter = Reporter(terminal, output='auto')
    reporter.context = {'scope': 'batch', 'selected': 2, 'item': 9, 'batch_position': 1}
    reporter.emit(status='progress', stage='transcription', stage_status='running', chunk=2, chunks=3)
    reporter.emit(status='progress', stage='transcription', stage_status='failed', chunk=2, chunks=3)
    assert not reporter._renderer.sections[(9, 'original', 'transcription', None)]['done']
    assert reporter._renderer.finished == 0
    reporter.emit(status='failed', finished=1, failed=1)
    assert reporter._renderer.finished == 1
    assert 'Interview 1 of 2 (plan item 9)' in terminal.getvalue()
    reporter.close()


def test_sparse_selection_and_inner_summary_dont_advance_batch(terminal):
    reporter = Reporter(terminal, output='auto')
    reporter.context = {'scope': 'batch', 'selected': 3, 'item': 14}
    reporter.emit(status='progress', stage='preflight', stage_status='running')
    reporter.context = {'scope': 'interview', 'selected': 3, 'item': 14}
    reporter.emit(status='summary', processed=1, failed=0)
    assert reporter._renderer.finished == 0
    reporter.context = {'scope': 'batch', 'selected': 3}
    reporter.emit(status='complete', item=14, processed=1)
    reporter.emit(status='blocked', item=42, processed=2)
    reporter.emit(status='not_attempted', item=81, processed=2)
    assert reporter._renderer.finished == 3
    reporter.emit(status='summary', selected=3, processed=2, completed=1, blocked=1, not_attempted=1, finished=3)
    reporter.close()
    assert '3/3 interviews finished' in terminal.getvalue()
    assert '1 complete, 1 blocked, 1 not started' in terminal.getvalue()


def test_direct_input_rows_advance_but_nested_interviews_do_not(terminal):
    reporter = Reporter(terminal, output='auto')
    reporter.context = {'scope': 'interview', 'selected': 3, 'item': 2}
    reporter.emit(status='complete', finished=1)
    assert reporter._renderer.finished == 1
    assert '1/3 inputs finished' in terminal.getvalue()
    reporter.context = {'scope': 'interview', 'selected': 3, 'item': 9, 'batch_position': 1}
    reporter.emit(status='summary', processed=1, finished=2)
    assert reporter._renderer.finished == 1
    reporter.close()


def test_finished_folder_input_leaves_the_newest_activity_visible(terminal):
    reporter = Reporter(terminal, output='auto')
    reporter.context = {'scope': 'interview', 'selected': 10, 'item': 1}
    reporter.emit(status='progress', stage='transcription', stage_status='running', chunk=1, chunks=2)
    reporter.emit(status='complete', finished=1)
    assert not reporter._renderer.activities
    reporter.context = {'scope': 'interview', 'selected': 10, 'item': 2}
    reporter.emit(status='progress', stage='transcription', stage_status='running', chunk=1, chunks=3)
    assert set(reporter._renderer.activities) == {(2, 'original')}
    panel = '\n'.join(reporter._renderer._panel(109, 8))
    assert 'Recording 2' in panel and 'Recording 1' not in panel
    assert '0/3 sections complete' in panel
    reporter.close()


@pytest.mark.parametrize('status', ['complete', 'failed', 'not_attempted', 'blocked'])
def test_nested_batch_family_results_need_coordinator_markers(terminal, status):
    reporter = Reporter(terminal, output='auto')
    reporter.context = {'scope': 'batch', 'selected': 2, 'item': 14, 'batch_position': 1}
    reporter.emit(status='progress', stage='transcription', stage_status='running', chunk=1, chunks=2)
    reporter.emit(status=status, stages={'transcription': status})
    assert reporter._renderer.finished == 0
    assert (14, 'original') in reporter._renderer.activities
    reporter.emit(status='progress', stage='diarization', family='attributed', stage_status='running')
    assert (14, 'attributed') in reporter._renderer.activities
    reporter.emit(status=status, finished=1, processed=1)
    assert reporter._renderer.finished == 1 and not reporter._renderer.activities
    reporter.close()


def test_unknown_total_is_honest_and_plain_has_no_terminal_controls():
    stream = io.StringIO()
    reporter = Reporter(stream, output='plain')
    reporter.emit(status='progress', stage='chapters', stage_status='running', chunk=4)
    reporter.emit(status='progress', stage='chapters', stage_status='complete', chunk=4)
    reporter.close()
    assert 'Section 4; 0 complete (total not known)' in stream.getvalue()
    assert 'Section 4; 1 complete (total not known)' in stream.getvalue()
    assert '%' not in stream.getvalue() and '\x1b' not in stream.getvalue()


def test_parts_need_explicit_completion(terminal):
    reporter = Reporter(terminal, output='auto')
    reporter.emit(status='progress', stage='part_transcription', stage_status='running', part=2, parts=4)
    assert reporter._renderer.parts[(None, 'original', 'part_transcription')]['done'] == set()
    reporter.emit(status='progress', stage='part_transcription', stage_status='complete', part=2, parts=4)
    assert reporter._renderer.parts[(None, 'original', 'part_transcription')]['done'] == {2}
    reporter.close()


def test_heartbeat_after_completion_does_not_claim_completed_step_is_running():
    stream = io.StringIO()
    reporter = Reporter(stream, output='plain')
    reporter.emit(status='progress', stage='conversion', stage_status='complete')
    reporter.emit(status='heartbeat', stage='conversion', stage_status='complete')
    reporter.close()
    assert 'last step complete; no new update' in ' '.join(stream.getvalue().split())
    assert 'still working' not in stream.getvalue()


def test_provider_counter_update_keeps_active_chunk_context():
    reporter = Reporter(io.StringIO())
    reporter.emit(status='progress', stage='transcription', stage_status='running',
                  part=2, parts=3, chunk=1, chunks=4)
    reporter.emit(status='progress', provider_requests=2)
    assert reporter.active['chunk'] == 1 and reporter.active['chunks'] == 4
    assert reporter.active['part'] == 2
    reporter.close()


def test_direct_heartbeat_keeps_scope_and_family_then_resets_for_original(tmp_path):
    stream = io.StringIO()
    reporter = Reporter(stream, heartbeat=0.01)
    reporter.context = {'scope': 'interview', 'phase': 'raw', 'family': 'attributed',
                        'selected': 2, 'batch_position': 1, 'item': 9}
    reporter.start(tmp_path / 'logs')
    def wait_for(stage):
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            rows = [json.loads(line) for line in stream.getvalue().splitlines()]
            found = [row for row in rows if row.get('status') == 'heartbeat'
                     and row.get('stage') == stage]
            if found:
                return found[-1]
            time.sleep(0.01)
        raise AssertionError('The reporter did not emit its idle heartbeat.')
    try:
        reporter.emit(status='progress', stage='diarization', stage_status='running', chunk=1, chunks=3)
        heartbeat = wait_for('diarization')
        assert heartbeat['family'] == 'attributed' and heartbeat['scope'] == 'interview'
        assert heartbeat['selected'] == 2 and heartbeat['batch_position'] == 1
        assert heartbeat['phase'] == 'raw' and heartbeat['item'] == 9
        reporter.context = {'scope': 'interview', 'phase': 'review', 'item': 9, 'selected': 2}
        reporter.emit(status='progress', stage='author_review', stage_status='running')
        heartbeat = wait_for('author_review')
        assert heartbeat['family'] == 'original' and heartbeat['phase'] == 'review'
        assert 'chunk' not in heartbeat and 'chunks' not in heartbeat
    finally:
        reporter.close()


def test_parallel_workers_keep_separate_activity_rows(terminal):
    reporter = Reporter(terminal, output='auto')
    gate = threading.Barrier(3)
    def process(item):
        reporter.context = {'scope': 'interview', 'item': item, 'selected': 2}
        reporter.begin_session(item)
        reporter.emit(status='progress', stage='transcription', stage_status='running', chunk=1, chunks=2)
        gate.wait()
    workers = [threading.Thread(target=process, args=(item,)) for item in (4, 13)]
    for worker in workers:
        worker.start()
    gate.wait()
    for worker in workers:
        worker.join()
    assert set(reporter._renderer.activities) == {(4, 'original'), (13, 'original')}
    assert 'Recording 4' in terminal.getvalue() and 'Recording 13' in terminal.getvalue()
    reporter.close()


def test_history_is_labeled_as_recorded_not_current_progress():
    stream = io.StringIO()
    reporter = Reporter(stream, output='plain')
    run = 'a' * 32
    reporter.context = {'scope': 'batch', 'selected': 2}
    reporter.emit(status='summary', historical_run=run, phase='raw', processed=1, completed=1)
    reporter.emit(status='latest', latest_run=run, item=8, family='attributed',
                  recorded_status='incomplete', stages={'diarization': 'complete', 'author_review': 'running'})
    reporter.close()
    output = stream.getvalue()
    assert 'Recorded run ' + run in output
    assert 'Last recorded: Interview 8 | Speaker transcript - incomplete' in output
    assert 'Checking text for author review: working' in output
    assert reporter._renderer.finished == 0 and not reporter._renderer.activities


def test_resize_bounds_panel_and_restores_cursor(terminal, monkeypatch):
    reporter = Reporter(terminal, output='auto')
    reporter.context = {'scope': 'batch', 'selected': 10}
    for item in range(8):
        reporter.emit(status='progress', stage='attributed_author_review', stage_status='running', item=item)
    monkeypatch.setenv('COLUMNS', '40')
    monkeypatch.setenv('LINES', '6')
    reporter.emit(status='heartbeat', stage='author_review', stage_status='running', item=0, idle_seconds=30)
    assert reporter._renderer.lines <= 4
    assert all(len(line) <= 39 for line in reporter._renderer._panel(39, 4))
    monkeypatch.setenv('COLUMNS', '15')
    reporter.emit(status='heartbeat', stage='author_review', stage_status='running', item=0)
    assert not reporter._renderer.hidden and reporter._renderer.lines == 0
    reporter.close()
    assert '\x1b[?25h' in terminal.getvalue()


def test_switching_modes_cleans_up_live_panel(terminal):
    reporter = Reporter(terminal, output='auto')
    reporter.emit(status='started')
    reporter.set_output('json')
    offset = len(terminal.getvalue())
    reporter.emit(status='complete')
    reporter.close()
    assert '\x1b[?25h' in terminal.getvalue()
    assert json.loads(terminal.getvalue()[offset:])['status'] == 'complete'
    with pytest.raises(ValueError, match='auto, plain or json'):
        reporter.set_output('SYNTHETIC_SECRET')


def test_console_failure_keeps_private_log_and_attempts_cursor_restore(tmp_path, terminal):
    class Broken(Terminal):
        broken = False
        attempts = []
        def write(self, line):
            self.attempts.append(line)
            if self.broken:
                raise BrokenPipeError('SYNTHETIC_SECRET')
            return super().write(line)
    stream = Broken()
    reporter = Reporter(stream, output='auto')
    reporter.start(tmp_path / 'logs')
    stream.broken = True
    reporter.emit(status='complete')
    reporter.emit(status='summary', processed=1)
    reporter.close()
    assert '\x1b[?25h' in stream.attempts
    rows = [json.loads(line) for line in next((tmp_path / 'logs').glob('*.jsonl')).read_text().splitlines()]
    assert rows[-1]['status'] == 'summary' and len(rows) == 3


def test_logging_failure_immediately_restores_cursor(terminal):
    class Full:
        def write(self, _):
            raise OSError('SYNTHETIC_SECRET')
        def close(self):
            pass
    reporter = Reporter(terminal, output='auto')
    reporter.emit(status='started')
    reporter.log = Full()
    with pytest.raises(LogError):
        reporter.emit(status='complete')
    assert terminal.getvalue().endswith('\x1b[?25h')
    reporter.emit(status='failed')
    reporter.close()
    assert 'failed' in terminal.getvalue() and 'SYNTHETIC_SECRET' not in terminal.getvalue()


def test_summary_interruption_and_safe_guidance_persist(terminal):
    reporter = Reporter(terminal, output='auto')
    reporter.emit(status='blocked', blocked_reason='human_review_required', family='original', item=1)
    reporter.emit(status='progress', stage='transcription', stage_status='running', item=2)
    reporter.emit(status='interrupted')
    reporter.close()
    output = terminal.getvalue()
    assert 'blocked' in output and 'Next:' in output and 'interrupted' in output


def test_terminal_size_uses_the_output_stream(monkeypatch, terminal):
    monkeypatch.setattr(terminal, 'fileno', lambda: 123)
    observed = []
    def size(fd):
        observed.append(fd)
        return os.terminal_size((52, 10))
    monkeypatch.setattr(os, 'get_terminal_size', size)
    reporter = Reporter(terminal, output='auto')
    reporter.emit(status='started')
    assert observed == [123] and reporter._renderer._size() == (51, 8)
    reporter.close()


@pytest.mark.parametrize('arguments, guidance', [
    (['--prepare-audio', '--polish-text'], '--extract-only cannot request enhancement.'),
    (['--author-workflow', '--steps', 'invalid'], '--stages must be a comma-separated selection'),
    (['--pipeline', '--chapter-style', 'both'], 'Author options require --workflow.'),
])
def test_argument_errors_restore_shared_terminal_before_diagnostics(
        monkeypatch, terminal, tmp_path, arguments, guidance):
    from src.cli import entrypoint

    # stdout and stderr share one terminal cursor in an actual interactive CLI.
    monkeypatch.setattr('sys.stdout', terminal)
    monkeypatch.setattr('sys.stderr', terminal)
    with pytest.raises(SystemExit) as error:
        entrypoint(arguments + ['--input', 'SYNTHETIC_PRIVATE_PATH',
                               '--logs-folder', str(tmp_path / 'logs')])
    assert error.value.code == 2
    output = terminal.getvalue()
    diagnostic = output.index('usage: voice-transcribe')
    assert '\x1b[?25h' in output[:diagnostic]
    assert guidance in output[diagnostic:]
    assert '\x1b' not in output[diagnostic:]
    assert 'SYNTHETIC_PRIVATE_PATH' not in output


def test_parallel_outcome_totals_count_interviews_and_ignore_nested_results(terminal):
    reporter = Reporter(terminal, output='auto', no_color=True)
    reporter.context = {'scope': 'batch', 'selected': 4}
    reporter.emit(status='progress', item=3, stage='preflight', stage_status='running')
    reporter.emit(status='progress', item=7, stage='preflight', stage_status='running')
    reporter.context = {'scope': 'interview', 'item': 3, 'batch_position': 1}
    reporter.emit(status='progress', stage='transcription', stage_status='running', family='original')
    reporter.emit(status='progress', stage='diarization', stage_status='running', family='attributed')
    reporter.emit(status='complete')
    reporter.emit(status='summary', selected=1, processed=1, completed=1, finished=1)
    view = reporter._renderer
    assert view.selected == 4 and view.counts()['succeeded'] == 0
    assert view.counts()['active'] == 2 and view.counts()['queued'] == 2
    reporter.context = {'scope': 'batch', 'selected': 4}
    reporter.emit(status='complete', item=3, finished=1, processed=1)
    reporter.emit(status='blocked', item=7, finished=2, processed=2)
    assert view.counts()['succeeded'] == 1 and view.counts()['blocked'] == 1
    assert view.counts()['active'] == 0 and view.counts()['queued'] == 2
    panel = '\n'.join(view._panel(109, 8))
    assert '1 succeeded' in panel and '0 errors' in panel and '2 queued' in panel
    reporter.close()


def test_distinct_errors_deduplicate_stage_family_and_item_aggregates(terminal):
    reporter = Reporter(terminal, output='auto', no_color=True)
    reporter.context = {'scope': 'batch', 'selected': 1, 'item': 2}
    for chunk in (1, 2):
        reporter.emit(status='progress', stage='author_review', stage_status='failed',
                      chunk=chunk, chunks=3, error_category='validation')
    reporter.emit(status='progress', stage='author_review', stage_status='failed')
    reporter.emit(status='progress', stage='phase_result', stage_status='failed')
    reporter.emit(status='failed')
    assert reporter._renderer.counts()['errors'] == 2
    assert reporter._renderer.counts()['failed'] == 0
    reporter.emit(status='failed', processed=1, finished=1)
    reporter.emit(status='failed', processed=1, finished=1)
    assert reporter._renderer.counts()['errors'] == 2
    assert reporter._renderer.counts()['failed'] == 1
    reporter.close()


def test_errors_are_scoped_to_recording_and_family(terminal):
    reporter = Reporter(terminal, output='auto')
    reporter.context = {'scope': 'interview', 'item': 1}
    for family, part in [('original', 1), ('original', 2), ('attributed', 1)]:
        reporter.emit(status='progress', family=family, part=part, stage='transcription',
                      stage_status='failed', chunk=1, chunks=2)
    assert reporter._renderer.counts()['errors'] == 3
    reporter.close()


def test_final_totals_keep_incomplete_and_unattempted_outcomes_separate(terminal):
    reporter = Reporter(terminal, output='auto')
    reporter.context = {'scope': 'batch', 'selected': 6}
    for item, status in enumerate(('complete', 'failed', 'blocked', 'interrupted',
                                   'incomplete', 'not_attempted'), 1):
        reporter.emit(status=status, item=item, processed=item, finished=item)
    reporter.emit(status='summary', completed=1, failed=1, blocked=1, interrupted=1,
                  incomplete=1, not_attempted=1, finished=6, processed=5)
    assert reporter._renderer.counts() == {
        'succeeded': 1, 'failed': 1, 'blocked': 1, 'interrupted': 1, 'incomplete': 1,
        'not_attempted': 1, 'errors': 1, 'active': 0, 'queued': 0}
    reporter.close()


@pytest.mark.parametrize('mode', ['auto', 'plain', 'json'])
def test_quiet_keeps_private_events(tmp_path, terminal, mode):
    reporter = Reporter(terminal, output=mode, quiet=True)
    reporter.start(tmp_path / 'logs')
    reporter.emit(status='failed', error_category='quota')
    reporter.emit(status='summary', processed=1, failed=1)
    reporter.close()
    assert terminal.getvalue() == ''
    rows = [json.loads(line) for line in next((tmp_path / 'logs').glob('*.jsonl')).read_text().splitlines()]
    assert [row['status'] for row in rows] == ['started', 'failed', 'summary']


@pytest.mark.parametrize('disable', ['flag', 'environment', 'plain'])
def test_colors_can_be_disabled_without_losing_output(monkeypatch, terminal, disable):
    if disable == 'environment':
        monkeypatch.setenv('NO_COLOR', '')
    else:
        monkeypatch.delenv('NO_COLOR', raising=False)
    reporter = Reporter(terminal, output='plain' if disable == 'plain' else 'auto',
                        no_color=disable == 'flag')
    reporter.emit(status='progress', stage='transcription', stage_status='running')
    reporter.emit(status='progress', stage='transcription', stage_status='complete')
    reporter.close()
    output = terminal.getvalue()
    assert 'Transcribed audio' in output
    assert '\x1b[32m' not in output and '\x1b[36m' not in output
    if disable == 'plain':
        assert '\x1b' not in output
    else:
        assert output.endswith('\x1b[?25h')


def test_terminal_colors_mark_success_and_failure(monkeypatch, terminal):
    monkeypatch.delenv('NO_COLOR', raising=False)
    reporter = Reporter(terminal, output='auto')
    reporter.emit(status='progress', stage='transcription', stage_status='complete')
    reporter.emit(status='progress', stage='author_review', stage_status='failed')
    reporter.close()
    assert '\x1b[32m' in terminal.getvalue() and '\x1b[31m' in terminal.getvalue()


def test_heartbeat_does_not_advance_totals(terminal):
    reporter = Reporter(terminal, output='auto')
    reporter.context = {'scope': 'batch', 'selected': 3, 'item': 1}
    reporter.emit(status='progress', stage='transcription', stage_status='running', chunk=2, chunks=4)
    counts = reporter._renderer.counts().copy()
    reporter.emit(status='heartbeat', **reporter.active)
    assert reporter._renderer.counts() == counts
    reporter.close()


def test_switching_to_quiet_clears_live_display(terminal):
    reporter = Reporter(terminal, output='auto')
    reporter.emit(status='started')
    reporter.set_output('auto', quiet=True)
    previous = terminal.getvalue()
    reporter.emit(status='complete')
    reporter.close()
    assert previous.endswith('\x1b[?25h') and terminal.getvalue() == previous


def test_nested_engine_inherits_quiet_and_no_color(monkeypatch, tmp_path, terminal):
    from src.cli import main
    from src.progress import CURRENT
    reporter = Reporter(terminal, output='auto', quiet=True, no_color=True)
    reporter.context = {'scope': 'batch', 'item': 2, 'batch_position': 1, 'selected': 2}
    token = CURRENT.set(reporter)
    def invalid_hints(**kwargs):
        raise ValueError('Fixed failure.')
    monkeypatch.setattr('src.cli.load_hints', invalid_hints)
    try:
        assert main(['--pipeline', '--input', 'unused', '--output-folder', str(tmp_path / 'output')]) == 1
        assert reporter.quiet and reporter.no_color and terminal.getvalue() == ''
        reporter.set_output('auto', quiet=False)
        assert main(['--pipeline', '--input', 'unused', '--output-folder', str(tmp_path / 'output')]) == 1
        assert reporter.no_color and not reporter._renderer.color
    finally:
        reporter.close()
        CURRENT.reset(token)


def test_ordered_interview_without_item_number_counts_as_one_active_input(terminal):
    reporter = Reporter(terminal, output='auto')
    reporter.context = {'scope': 'interview', 'selected': 1}
    reporter.emit(status='progress', stage='transcription', stage_status='running', part=1, parts=2)
    reporter.emit(status='progress', stage='diarization', stage_status='running', family='attributed')
    assert reporter._renderer.counts()['active'] == 1
    assert reporter._renderer.counts()['queued'] == 0
    reporter.emit(status='summary', selected=1, finished=1, completed=1, failed=0)
    assert reporter._renderer.counts()['active'] == 0
    assert reporter._renderer.counts()['succeeded'] == 1
    reporter.close()


def test_independent_family_failures_count_once_each_before_outer_result(terminal):
    reporter = Reporter(terminal, output='auto')
    reporter.context = {'scope': 'batch', 'item': 1, 'selected': 1}
    for family in ('original', 'attributed'):
        reporter.emit(status='failed', family=family)
        reporter.emit(status='progress', family=family, stage='phase_result', stage_status='failed')
    reporter.emit(status='failed', finished=1, processed=1)
    assert reporter._renderer.counts()['failed'] == 1 and reporter._renderer.counts()['errors'] == 2
    reporter.close()
