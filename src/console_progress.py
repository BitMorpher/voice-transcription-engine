"""Human progress views built exclusively from Reporter's sanitized events."""

from collections import Counter

from rich.console import Console, Group
from rich.live import Live
from rich.progress_bar import ProgressBar
from rich.table import Table
from rich.text import Text


STAGE_LABELS = {
    'preflight': ('Checking inputs', 'Checked inputs'),
    'conversion': ('Preparing audio', 'Prepared audio'),
    'transcription': ('Transcribing audio', 'Transcribed audio'),
    'part_transcription': ('Transcribing recording', 'Transcribed recording'),
    'enhancement': ('Polishing transcript', 'Polished transcript'),
    'author_review': ('Reviewing transcript', 'Reviewed transcript'),
    'chapters': ('Drafting chapters', 'Drafted chapters'),
    'staging': ('Copying recordings', 'Copied recordings'),
    'verification': ('Verifying recordings', 'Verified recordings'),
    'combined_raw': ('Combining recordings', 'Combined recordings'),
    'diarization': ('Separating speakers', 'Separated speakers'),
    'attribution': ('Building speaker transcript', 'Built speaker transcript'),
}
SUCCESS = {'complete', 'staged', 'verified'}
TERMINAL = SUCCESS | {'failed', 'blocked', 'interrupted'}


def duration(seconds):
    seconds = int(seconds)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f'{hours}:{minutes:02d}:{seconds:02d}' if hours else f'{minutes}:{seconds:02d}'


def stage_label(row):
    stage = row.get('stage', 'preflight')
    stage = stage.removeprefix('attributed_')
    ongoing, finished = STAGE_LABELS.get(stage, ('Processing', 'Processed'))
    status = row.get('stage_status', 'running')
    if status == 'skipped':
        return f'{finished} (cache reused)'
    if status == 'complete':
        return finished
    if status in {'failed', 'blocked', 'incomplete'}:
        return f'{ongoing} — {status}'
    return ongoing


def item_detail(row):
    details = []
    if row.get('family') == 'attributed' or row.get('stage', '').startswith('attributed_'):
        details.append('attributed')
    for unit, total in (('part', 'parts'), ('chunk', 'chunks')):
        if unit in row:
            count = str(row[unit])
            if total in row:
                count += f'/{row[total]}'
            details.append(f'{unit} {count}')
    return ' · '.join(details)


class ProgressState:
    """Count item outcomes separately from failed operations and nested summaries."""

    def __init__(self, scope):
        self.scope = scope
        self.selected = None
        self.active = {}
        self.outcomes = {}
        self.errors = set()
        self.elapsed = 0
        self.idle = 0
        self.phase = None
        self.finished = False
        self.seen = False

    @property
    def counts(self):
        outcomes = Counter(self.outcomes.values())
        succeeded = sum(outcomes[status] for status in SUCCESS)
        resolved = succeeded + outcomes['failed'] + outcomes['blocked']
        return {
            'succeeded': succeeded, 'failed': outcomes['failed'],
            'blocked': outcomes['blocked'], 'interrupted': outcomes['interrupted'],
            'resolved': resolved, 'active': len(self.active), 'errors': len(self.errors),
            'queued': max(0, (self.selected or 0) - len(self.outcomes) - len(self.active)),
        }

    def update(self, event):
        self.seen = True
        status = event.get('status')
        # An interview nested in a batch has its own complete/summary events.
        # Only its outer coordinator can finish the selected batch item.
        nested = self.scope == 'batch' and event.get('scope') == 'interview'
        self.elapsed = event['elapsed_seconds']
        self.phase = event.get('phase', self.phase)
        if not nested and 'selected' in event:
            self.selected = event['selected']
        if status == 'heartbeat':
            self.idle = event.get('idle_seconds', 0)
            return True
        self.idle = 0
        if nested and status in SUCCESS | {'summary'}:
            return False
        item = event.get('item')
        stage_status = event.get('stage_status')
        if status == 'interrupted':
            for active_item in self.active:
                self.outcomes[active_item] = 'interrupted'
            self.active.clear()
            self.finished = True
        elif not nested and status in TERMINAL and item is not None:
            self.outcomes[item] = status
            self.active.pop(item, None)
        elif status in {'running', 'progress'} and item is not None:
            row = self.active.setdefault(item, {})
            # Counters from a previous chunk/stage/recording cannot carry forward.
            changed = any(key in event and event[key] != row.get(key)
                          for key in ('stage', 'part', 'family'))
            if changed:
                row.pop('chunk', None)
                row.pop('chunks', None)
            if (event.get('stage') in {'enhancement', 'author_review', 'chapters', 'combined_raw',
                                       'attribution', 'attributed_enhancement',
                                       'attributed_author_review', 'attributed_chapters'}
                    and 'part' not in event):
                row.pop('part', None)
                row.pop('parts', None)
            if event.get('family') != row.get('family') and 'stage' in event:
                row.pop('family', None)
            row.update({key: event[key] for key in
                        ('stage', 'stage_status', 'part', 'parts', 'chunk', 'chunks', 'family')
                        if key in event})
            if event.get('stage') in {'diarization', 'attribution'}:
                row['family'] = 'attributed'
            if 'stage' not in event:
                row['stage_status'] = 'running'
        if stage_status == 'failed':
            row = self.active.get(item, event)
            stage = event.get('stage', '').removeprefix('attributed_')
            family = row.get('family')
            part = row.get('part')
            key = (item, family, stage, part, event.get('chunk'))
            # Aggregate stage/item failures must not recount failed chunks.
            # Infer the current recording for chunk events that omit its number.
            if event.get('chunk') is not None:
                self.errors.discard((item, family, stage, part, None))
                self.errors.add(key)
            elif not any(existing[:3] == key[:3] and (part is None or existing[3] == part)
                         for existing in self.errors):
                self.errors.add(key)
        elif status == 'failed' and not any(key[0] == item for key in self.errors):
            self.errors.add((item, None, None, None, None))
        if not nested and status == 'summary':
            self.finished = True
            self.active.clear()
        if not nested and status == 'failed' and item is None:
            self.active.clear()
            self.finished = True
        return True

    def summary(self):
        counts = self.counts
        total = '?' if self.selected is None else str(self.selected)
        result = (f'{counts["resolved"]}/{total} items finished · '
                f'{counts["succeeded"]} succeeded · {counts["failed"]} failed · '
                f'{counts["errors"]} errors · {counts["blocked"]} blocked · '
                f'{counts["active"]} active · {counts["queued"]} queued')
        if counts['interrupted']:
            result += f' · {counts["interrupted"]} interrupted'
        return result


class HumanProgress:
    """Transient terminal dashboard, or append-only plain text for redirected logs."""

    def __init__(self, stream, *, live, no_color, scope):
        self.state = ProgressState(scope)
        self.console = Console(file=stream, color_system=None if no_color or not live else 'auto',
                               no_color=no_color, force_terminal=False if not live else None,
                               highlight=False, markup=False)
        self.live = Live(console=self.console, transient=True, auto_refresh=False,
                         redirect_stdout=False, redirect_stderr=False) if live else None
        self.started = False
        self.last_line = None
        self.guidance_seen = set()

    def render(self):
        state = self.state
        counts = state.counts
        title = 'Batch' if state.scope == 'batch' else 'Transcription'
        if state.phase:
            title += f' · {state.phase}'
        header = Text(title, style='bold')
        header.append(f'  elapsed {duration(state.elapsed)}', style='dim')
        summary = Text()
        for label, style in (('succeeded', 'green'), ('failed', 'red'), ('errors', 'red'),
                             ('blocked', 'yellow'), ('active', 'cyan'), ('queued', 'dim')):
            if summary:
                summary.append(' · ')
            summary.append(f'{counts[label]} {label}', style=style)
        rows = [header]
        if state.selected is not None:
            total = Text(f'{counts["resolved"]}/{state.selected} items finished')
            meter = Table.grid(expand=True, padding=(0, 1))
            meter.add_column(ratio=1)
            meter.add_column()
            meter.add_row(ProgressBar(total=state.selected, completed=counts['resolved'],
                                     complete_style='cyan',
                                     finished_style='yellow' if counts['failed'] or counts['blocked'] else 'green'), total)
            rows.append(meter)
        rows.append(summary)
        active = Table.grid(padding=(0, 2), expand=True)
        active.add_column(style='cyan', no_wrap=True)
        active.add_column(ratio=1)
        active.add_column(style='dim')
        for item, row in sorted(state.active.items()):
            active.add_row(Text(f'Item {item}'), Text(stage_label(row)), Text(item_detail(row)))
        if state.active:
            rows.append(active)
        elif not state.finished:
            if state.selected is not None and counts['resolved'] == state.selected:
                message = 'Finalizing execution…'
            elif counts['resolved']:
                message = 'Waiting for next item…'
            else:
                message = 'Preparing execution…'
            rows.append(Text(message, style='dim'))
        if state.idle:
            rows.append(Text(f'No progress update for {duration(state.idle)}; '
                             'coordinator is alive, completion is unconfirmed.', style='yellow'))
        return Group(*rows)

    def consume(self, event):
        if not self.state.update(event):
            return
        if self.live and self.started:
            # Printing a permanent line also redraws the live region. Give it
            # the current frame so a just-completed item cannot look active.
            self.live.update(self.render())
        status = event.get('status')
        item = event.get('item')
        stage_status = event.get('stage_status')
        prefix = f'Item {item}: ' if item is not None else ''
        style = None
        if status == 'summary':
            # Historical batch status reports have aggregate counts, not item events.
            if self.state.phase == 'status' or not self.state.outcomes and 'processed' in event:
                failed, blocked = event.get('failed', 0), event.get('blocked', 0)
                succeeded = max(0, event.get('processed', 0) - failed - blocked - event.get('interrupted', 0))
                phase = event.get('phase')
                heading = f'Summary ({phase})' if phase else 'Summary'
                line = (f'{heading}: {event.get("processed", 0)} processed · {succeeded} succeeded · '
                        f'{failed} failed · {blocked} blocked · {event.get("interrupted", 0)} interrupted')
            else:
                line = 'Summary: ' + self.state.summary()
        elif status in TERMINAL:
            label = {'complete': 'Completed', 'failed': 'Failed', 'blocked': 'Blocked',
                     'staged': 'Staged', 'verified': 'Verified', 'interrupted': 'Interrupted'}[status]
            line = prefix + label
            if status in SUCCESS:
                line = ('✓ ' if self.console.encoding.lower().replace('-', '') == 'utf8' else 'OK ') + line
            if (status == 'interrupted' or event.get('scope') == self.state.scope):
                line += ' · ' + self.state.summary()
            style = 'green' if status in SUCCESS else 'red' if status == 'failed' else 'yellow'
        elif status == 'progress':
            row = self.state.active.get(item, event)
            line = prefix + stage_label(row)
            details = item_detail(row)
            if details:
                line += ' · ' + details
            style = 'red' if stage_status == 'failed' else 'green' if stage_status == 'complete' else None
        elif status == 'heartbeat':
            line = f'Waiting: no progress update for {duration(self.state.idle)}; coordinator is alive.'
        elif status == 'running':
            line = prefix + 'Started'
        elif status == 'started':
            line = 'Started execution'
        else:
            return
        if 'error_category' in event:
            line += ' · ' + event['error_category']
            if 'http_status' in event:
                line += f' (HTTP {event["http_status"]})'
        # Keep completed outcomes and safe failure guidance above the transient view.
        persistent = (status in TERMINAL | {'summary'} or stage_status in {'failed', 'blocked', 'skipped'})
        if not self.live or persistent:
            if line != self.last_line or status in {'heartbeat', 'summary'}:
                self.console.print(Text(f'{line}  ({duration(self.state.elapsed)})', style=style))
                self.last_line = line
                guidance_key = (item, event.get('guidance'))
                if 'guidance' in event and guidance_key not in self.guidance_seen:
                    self.console.print(Text('  ' + event['guidance']))
                    self.guidance_seen.add(guidance_key)
        if self.live and not self.state.finished:
            if not self.started:
                self.live.update(self.render())
                self.live.start(refresh=True)
                self.started = True
            else:
                self.live.update(self.render(), refresh=True)
        elif self.live and self.started:
            self.live.stop()
            self.started = False

    def close(self):
        if self.live and self.started:
            self.live.stop()
            self.started = False
        if self.state.seen and not self.state.finished:
            self.console.print(Text('Stopped: ' + self.state.summary() +
                                    f' · elapsed {duration(self.state.elapsed)}'))
