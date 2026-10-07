"""Plain-language views of already allowlisted execution events.

This module never receives arguments, paths, provider responses or exception text.
The reporter remains responsible for privacy filtering and durable JSON logging.
"""

import os
import shutil
import textwrap
from collections import Counter


STAGE_LABELS = {
    'prerequisite': 'Checking saved results',
    'phase_result': 'Finishing this step',
    'conversion': 'Preparing audio',
    'transcription': 'Transcribing audio',
    'enhancement': 'Creating readable text',
    'author_review': 'Checking text for author review',
    'chapters': 'Drafting chapters',
    'part_transcription': 'Transcribing recording',
    'staging': 'Copying recordings',
    'preflight': 'Checking inputs',
    'verification': 'Verifying saved recordings',
    'combined_raw': 'Combining transcripts',
    'diarization': 'Separating voices',
    'attribution': 'Adding speaker labels',
}
COMPLETED_LABELS = {
    'prerequisite': 'Checked saved results', 'phase_result': 'Finished this step',
    'conversion': 'Prepared audio', 'transcription': 'Transcribed audio',
    'enhancement': 'Created readable text', 'author_review': 'Checked text for author review',
    'chapters': 'Drafted chapters', 'part_transcription': 'Transcribed recording',
    'staging': 'Copied recordings', 'preflight': 'Checked inputs',
    'verification': 'Verified saved recordings', 'combined_raw': 'Combined transcripts',
    'diarization': 'Separated voices', 'attribution': 'Added speaker labels',
}
STATUS_LABELS = {
    'started': 'started', 'progress': 'working', 'running': 'working',
    'complete': 'complete', 'failed': 'failed', 'skipped': 'reused saved result',
    'interrupted': 'interrupted', 'blocked': 'blocked', 'staged': 'copied',
    'verified': 'verified', 'incomplete': 'incomplete', 'pending': 'waiting',
    'not_attempted': 'not started', 'heartbeat': 'still working',
}
PHASE_LABELS = {
    'raw': 'transcription', 'review': 'author review', 'chapters': 'chapter drafting',
    'inventory': 'listing inputs', 'check': 'checking inputs', 'prepare': 'copying inputs',
    'verify': 'verifying inputs', 'status': 'saved history',
}
TERMINAL = {'complete', 'failed', 'blocked', 'interrupted', 'staged', 'verified',
            'incomplete', 'not_attempted'}


def live_capable(stream):
    """Only use terminal controls when both the stream and TERM support them."""
    try:
        return bool(stream is not None and stream.isatty()
                    and os.environ.get('TERM', '') not in {'', 'dumb'})
    except (AttributeError, OSError, ValueError):
        return False


def _elapsed(seconds):
    seconds = max(0, int(seconds))
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f'{hours}:{minutes:02d}:{seconds:02d}' if hours else f'{minutes}:{seconds:02d}'


def _family_stage(event):
    stage = event.get('stage', '')
    family = event.get('family', 'original')
    if stage.startswith('attributed_'):
        family, stage = 'attributed', stage.removeprefix('attributed_')
    return family, stage


class TerminalProgress:
    """A bounded live panel, or scrolling milestones without control characters."""

    def __init__(self, stream, *, live=False, no_color=False):
        self.stream = stream
        self.live = live
        self.color = live and not no_color and 'NO_COLOR' not in os.environ
        self.lines = 0
        self.hidden = False
        self.activities = {}
        self.sections = {}
        self.parts = {}
        self.finished_items = set()
        self.finished = 0
        self.selected = None
        self.input_label = 'inputs'
        self.elapsed = 0
        self.last_guidance = set()
        self.closed = False
        self.summary = None
        self.outcomes = {}
        self.errors = set()
        self.final_counts = None

    def counts(self):
        outcomes = Counter(self.outcomes.values())
        if self.final_counts is not None:
            for status, count in self.final_counts.items():
                outcomes[status] = count
        # Ordered direct interviews omit an item number; their None identity is
        # still one active input, even when both transcript families have rows.
        active = {item for item, _ in self.activities if item not in self.outcomes}
        return {
            'succeeded': sum(outcomes[status] for status in ('complete', 'staged', 'verified')),
            **{status: outcomes[status] for status in
               ('failed', 'blocked', 'interrupted', 'incomplete', 'not_attempted')},
            'errors': len(self.errors), 'active': len(active),
            'queued': max(0, (self.selected or 0) - sum(outcomes.values()) - len(active)),
        }

    def _stats(self):
        counts = self.counts()
        labels = [('succeeded', 'succeeded'), ('failed', 'failed'), ('errors', 'errors'),
                  ('blocked', 'blocked'), ('active', 'active'), ('queued', 'queued')]
        labels.extend((key, label) for key, label in
                      [('incomplete', 'incomplete'), ('interrupted', 'interrupted'),
                       ('not_attempted', 'not started')] if counts[key])
        return ' | '.join(f'{counts[key]} {label}' for key, label in labels)

    def _count_errors(self, event):
        if event.get('stage_status') == 'failed':
            item = event.get('item')
            family, stage = _family_stage(event)
            if stage == 'phase_result':
                if not any(key[0] == item and key[1] in {family, None} for key in self.errors):
                    self.errors.add((item, family, None, None, None))
                return
            self.errors.discard((item, None, None, None, None))
            self.errors.discard((item, family, None, None, None))
            previous = self.activities.get((item, family), {})
            part = event.get('part', previous.get('part') if _family_stage(previous)[1] == stage else None)
            key = item, family, stage, part, event.get('chunk')
            if 'chunk' in event:
                self.errors.discard((item, family, stage, part, None))
                self.errors.add(key)
            elif not any(existing[:3] == key[:3] and (part is None or existing[3] == part)
                         for existing in self.errors):
                self.errors.add(key)
        elif event.get('status') == 'failed':
            item, family = event.get('item'), event.get('family')
            if not any(key[0] == item and (family is None or key[1] in {family, None})
                       for key in self.errors):
                self.errors.add((item, family, None, None, None))

    def _styled(self, text, status=None):
        if not self.color:
            return text
        code = ('32' if status in {'complete', 'skipped', 'staged', 'verified'} else
                '31' if status == 'failed' else
                '33' if status in {'blocked', 'incomplete', 'interrupted', 'not_attempted'} else '36')
        return f'\x1b[{code}m{text}\x1b[0m'

    def _size(self):
        try:
            size = os.get_terminal_size(self.stream.fileno())
        except (AttributeError, OSError, ValueError):
            size = shutil.get_terminal_size(fallback=(80, 24))
        return max(1, min(size.columns - 1, 120)), max(1, min(size.lines - 2, 8))

    def _write(self, text):
        self.stream.write(text)
        self.stream.flush()

    def _clear(self):
        if self.lines:
            # Each panel row ends in a newline: the cursor starts below the panel.
            # Resize/reflow behavior differs between terminals. Clear only rows
            # we drew; guessing wrapped rows could erase durable guidance above.
            self._write('\r' + '\x1b[1A\x1b[2K\r' * self.lines)
            self.lines = 0

    def suspend(self):
        """Restore the terminal even when logging or the command raises an error."""
        try:
            self._clear()
        finally:
            if self.hidden:
                self.hidden = False
                self._write('\x1b[?25h')

    def close(self):
        if not self.closed:
            self.closed = True
            self.suspend()

    def _identity(self, event, *, family=True):
        labels = []
        if 'item' in event:
            unit = 'Interview' if event.get('scope') == 'batch' or 'batch_position' in event else 'Recording'
            if 'batch_position' in event and self.selected:
                labels.append(f'{unit} {event["batch_position"]} of {self.selected}'
                              f' (plan item {event["item"]})')
            else:
                labels.append(f'{unit} {event["item"]}')
        elif 'part' in event:
            labels.append(f'Recording {event["part"]}')
        if family and ('family' in event or event.get('stage', '').startswith('attributed_')):
            labels.append('Speaker transcript' if _family_stage(event)[0] == 'attributed'
                          else 'Source transcript')
        if 'item' in event and 'part' in event:
            labels.append(f'recording part {event["part"]}'
                          + (f' of {event["parts"]}' if event.get('parts') else ''))
        return ' | '.join(labels)

    def _section(self, event):
        family, stage = _family_stage(event)
        return event.get('item'), family, stage, event.get('part')

    def _count_sections(self, event):
        if 'chunk' not in event or not event.get('stage'):
            return
        key = self._section(event)
        state = self.sections.setdefault(key, {'done': set(), 'total': None})
        if event.get('chunks', 0) > 0:
            state['total'] = event['chunks']
        if event.get('stage_status') in {'complete', 'skipped'}:
            state['done'].add(event['chunk'])

    def _count_parts(self, event):
        if 'part' not in event or not event.get('stage') or 'chunk' in event:
            return
        family, stage = _family_stage(event)
        key = event.get('item'), family, stage
        state = self.parts.setdefault(key, {'done': set(), 'total': None})
        if event.get('parts', 0) > 0:
            state['total'] = event['parts']
        if event.get('stage_status') in {'complete', 'skipped'}:
            state['done'].add(event['part'])

    @staticmethod
    def _bar(done, total, width=14):
        done = min(done, total)
        filled = int(width * done / total) if total else 0
        return '[' + '#' * filled + '-' * (width - filled) + ']'

    def _work_count(self, event, width):
        if 'chunk' in event:
            state = self.sections.get(self._section(event), {'done': set(), 'total': None})
            done, total = len(state['done']), state['total']
            if total:
                return (f'{self._bar(done, total, min(14, max(4, width // 6)))} '
                        f'{done}/{total} sections complete; section {event["chunk"]}')
            return f'Section {event["chunk"]}; {done} complete (total not known)'
        if 'part' in event and event.get('stage'):
            family, stage = _family_stage(event)
            state = self.parts.get((event.get('item'), family, stage),
                                   {'done': set(), 'total': None})
            done, total = len(state['done']), state['total']
            if total:
                return f'{self._bar(done, total)} {done}/{total} recordings complete'
        return ''

    def _activity(self, event, width, *, panel=False):
        _, stage = _family_stage(event)
        label = STAGE_LABELS.get(stage, 'Processing')
        if event.get('stage_status') in {'complete', 'skipped'}:
            label = COMPLETED_LABELS.get(stage, 'Processed')
        state = STATUS_LABELS.get(event.get('stage_status', event.get('status')), 'working')
        if event.get('status') == 'heartbeat':
            state = (('last step ' + state if event.get('stage_status') in TERMINAL | {'skipped'}
                      else 'still working')
                     + f'; no new update for {_elapsed(event.get("idle_seconds", 0))}')
        prefix = self._identity(event)
        line = (f'{label} - {state}' + (' | ' + prefix if prefix else '') if panel
                else f'{prefix}: {label} - {state}' if prefix else f'{label} - {state}')
        count = self._work_count(event, width)
        return line + (' | ' + count if count and not panel else '')

    def _summary(self, event):
        labels = [('completed', 'complete'), ('staged', 'copied'), ('verified', 'verified'),
                  ('failed', 'failed'), ('blocked', 'blocked'), ('interrupted', 'interrupted'),
                  ('incomplete', 'incomplete'), ('not_attempted', 'not started')]
        counts = [f'{event[key]} {label}' for key, label in labels if event.get(key)]
        if not counts and 'processed' in event:
            counts.append(f'{event["processed"]} processed')
        return ', '.join(counts) or 'no recordings processed'

    def _historical(self, event):
        if 'historical_run' in event:
            phase = PHASE_LABELS.get(event.get('phase'), 'processing')
            return [f'Recorded run {event["historical_run"]} | {phase} | '
                    f'{event.get("started_at", "")}', '  ' + self._summary(event)]
        if 'latest_run' in event:
            heading = (f'Last recorded: {self._identity(event)} - '
                       f'{STATUS_LABELS.get(event.get("recorded_status"), "incomplete")}'
                       f' | run {event["latest_run"]}')
            stages = []
            for stage, status in event.get('stages', {}).items():
                stages.append(f'{STAGE_LABELS.get(stage, "Processing")}: '
                              f'{STATUS_LABELS.get(status, status)}')
            return [heading] + ['  ' + '; '.join(stages)] if stages else [heading]
        return None

    def _milestones(self, event):
        historical = self._historical(event)
        if historical is not None:
            return historical
        status, stage_status = event.get('status'), event.get('stage_status')
        if status == 'started':
            return ['Starting voice processing.']
        if status == 'configuration':
            return ['Settings checked.']
        if status == 'summary':
            prefix = self._identity(event)
            lines = [(prefix + ': ' if prefix else 'Finished: ') + self._summary(event)
                     + f' | {_elapsed(event.get("elapsed_seconds", 0))} elapsed']
            if 'item' not in event:
                lines.append(self._stats())
            return lines
        if stage_status in TERMINAL | {'skipped'}:
            return [self._activity(event, 120)]
        if status in TERMINAL:
            prefix = self._identity(event)
            label = STATUS_LABELS[status]
            if ((event.get('scope') == 'batch' or 'batch_position' in event)
                    and 'finished' not in event and 'processed' not in event):
                label = 'transcript step ' + label
            return [(prefix + ': ' if prefix else 'Voice processing: ') + label]
        # Plain output needs the start of each step and honest heartbeat updates.
        if not self.live and (stage_status in {'running', 'pending'} or status == 'heartbeat'):
            return [self._activity(event, 120)]
        if not self.live and status == 'not_attempted':
            return ['Voice processing: not started']
        return []

    def _panel(self, width, height):
        lines = [f'Voice processing | {_elapsed(self.elapsed)} elapsed']
        if self.selected is not None:
            lines.append(f'{self._bar(self.finished, self.selected)} '
                         f'{self.finished}/{self.selected} {self.input_label} finished')
        # Wrap totals at narrow widths so errors and active work cannot be truncated.
        stats = textwrap.wrap(self._stats(), width=max(1, width))
        lines.extend(stats[:max(0, height - len(lines))])
        available = height - len(lines)
        values = list(self.activities.values())
        for index, event in enumerate(values):
            group = [self._activity(event, width, panel=True)]
            count = self._work_count(event, width)
            if count:
                group.append('  ' + count)
            # Keep the count together with its current step, and leave room for a
            # truthful overflow indication instead of silently hiding other workers.
            needed = len(group) + int(index < len(values) - 1)
            if needed > available:
                if available > 0:
                    lines.append(f'{len(values) - index} more active transcript steps')
                break
            lines.extend(group)
            available -= len(group)
        return [line[:width] for line in lines[:height]]

    def emit(self, event):
        if self.closed:
            return
        historical = self._historical(event) is not None
        status = event.get('status')
        self.elapsed = event.get('elapsed_seconds', self.elapsed)
        if not historical:
            nested = (event.get('scope') == 'interview'
                      and ('batch_position' in event or self.input_label == 'interviews'))
            if 'selected' in event and not nested:
                self.selected = event['selected']
                if event.get('scope') == 'batch' or 'batch_position' in event:
                    self.input_label = 'interviews'
            self._count_sections(event)
            self._count_parts(event)
            self._count_errors(event)
            # Inner interview summaries can say processed=1 while the batch is still
            # working. Only coordinator rows and item-free summaries finish inputs.
            outer = (event.get('scope') == 'batch' or (status == 'summary' and 'item' not in event)
                     or event.get('scope') == 'interview' and 'finished' in event
                     and 'batch_position' not in event)
            terminal_item = (event.get('scope') == 'batch' and 'item' in event
                             and status in TERMINAL and 'stage' not in event
                             and ('finished' in event or 'processed' in event))
            direct_item = (event.get('scope') == 'interview' and 'item' in event
                           and 'batch_position' not in event and 'finished' in event
                           and status in TERMINAL and 'stage' not in event)
            if outer and 'finished' in event:
                self.finished = event['finished']
            elif terminal_item:
                self.finished_items.add(event['item'])
                self.finished = len(self.finished_items)
            if terminal_item or direct_item:
                self.outcomes[event['item']] = status
                self.activities = {key: value for key, value in self.activities.items()
                                   if key[0] != event['item']}
            elif event.get('stage') and event.get('stage_status'):
                self.activities[event.get('item'), _family_stage(event)[0]] = dict(event)
            if status == 'summary' and 'item' not in event:
                self.activities.clear()
                self.summary = self._summary(event)
                self.final_counts = {status: event[field] for status, field in
                    [('complete', 'completed'), ('staged', 'staged'), ('verified', 'verified'),
                     ('failed', 'failed'), ('blocked', 'blocked'), ('interrupted', 'interrupted'),
                     ('incomplete', 'incomplete'), ('not_attempted', 'not_attempted')] if field in event}
            if status == 'interrupted' and 'item' not in event:
                for item, _ in self.activities:
                    if item not in self.outcomes:
                        self.outcomes[item] = 'interrupted'
                self.activities.clear()
        messages = self._milestones(event)
        guidance = event.get('guidance')
        if guidance and (event.get('item'), guidance) not in self.last_guidance:
            messages.append('Next: ' + guidance)
            self.last_guidance.add((event.get('item'), guidance))
        width, height = self._size()
        if self.live:
            self._clear()
        for message in messages:
            for line in textwrap.wrap(message, width=max(1, width), break_long_words=True):
                self._write(self._styled(line, event.get('stage_status', status)) + '\n')
        if self.live and not historical and width >= 32 and height >= 3:
            if not self.hidden:
                self._write('\x1b[?25l')
                self.hidden = True
            panel = self._panel(width, height)
            self._write('\n'.join(self._styled(line) for line in panel) + '\n')
            self.lines = len(panel)
        elif self.hidden:
            self.hidden = False
            self._write('\x1b[?25h')
