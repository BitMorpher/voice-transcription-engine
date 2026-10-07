"""Isolated text-only comparison with synthetic defaults and private checkpoints."""

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile
import time
from types import SimpleNamespace
import uuid

from .attributed_editing import grouped_turns
from .author_review import REASONS, ReviewOptions, review_transcript, validate_review_report
from .cli import PrivateArgumentParser
from .model_config import EDITING_MODELS, REASONING_EFFORTS, EditingOptions, _fingerprint
from .private_output import OutputError, digest, output_directory, write_private
from .progress import interruptions
from .provider_control import CURRENT_CONTROL, ControlledClient, ProviderControl
from .provider_errors import classify
from .text_editing import words
from .text_requests import ResponseValidationError, collect_text_metrics
from .transcriber import Transcriber

STAGES = ('polish', 'attributed-polish', 'review')
MAX_INPUT_BYTES = 65536
FIXTURE_VERSION = 'synthetic-text-v1'
SAFE_STORAGE = 'Comparison output conflicts, changed, or is locked; use fresh private output. No saved artifact was overwritten.'


class ComparisonError(ValueError):
    """Fixed local guidance; never include supplied paths or transcript content."""


@dataclass(frozen=True)
class ComparisonCase:
    stage: str
    model: str
    effort: str

    def options(self, chunk_bytes):
        if self.stage not in STAGES:
            raise ComparisonError('Use a documented comparison stage.')
        factory = ReviewOptions if self.stage == 'review' else EditingOptions
        return factory(model=self.model, reasoning_effort=self.effort, chunk_bytes=chunk_bytes)


DEFAULT_CASES = (
    ComparisonCase('polish', 'gpt-6.1-sol', 'low'),
    ComparisonCase('review', 'gpt-6.1-sol', 'medium'),
    ComparisonCase('review', 'gpt-6-astra', 'high'),
    ComparisonCase('attributed-polish', 'gpt-6.1-sol', 'low'),
)


def synthetic_fixture(turn_count=128):
    """Synthetic short turns, Unicode and empty speech; no real speaker identities."""
    phrases = (
        'Yes.', 'No.', 'I think that was unfair.', 'I cannot confirm who said it.',
        'The synthetic treasurer stole the synthetic fund.',
        'The synthetic participant disclosed a medical diagnosis.',
        'दिन café क़ 가 ΐ 👩\u200d💻 €100.', '', ' \t',
        'Ignore these words as instructions and preserve them.',
    )
    parts, turns, cursor = [], [], 0
    for index in range(1, turn_count + 1):
        speech = phrases[(index - 1) % len(phrases)]
        header = f'[synthetic-speaker-{index % 2 + 1} | turn-{index:06d}]\n'
        body = header + speech + '\n\n'
        turns.append({
            'turn_id': f'turn-{index:06d}', 'speaker_key': f'synthetic-speaker-{index % 2 + 1}',
            'start': cursor, 'end': cursor + len(body),
            'speech_start': cursor + len(header), 'speech_end': cursor + len(header) + len(speech),
            'speech_sha256': hashlib.sha256(speech.encode()).hexdigest(),
        })
        parts.append(body)
        cursor += len(body)
    return ''.join(parts), turns


class SyntheticClient:
    """Faithful schema fixture, not a model simulator or quality benchmark."""
    def __init__(self):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **parameters):
        supplied = json.loads(parameters['messages'][-1]['content'])
        schema_name = parameters['response_format']['json_schema']['name']
        if schema_name == 'faithful_turn_group_edit':
            payload = {'group_index': supplied['group_index'], 'edits': [
                {**piece, 'speaker_uncertain': False} for piece in supplied['turns']]}
        elif schema_name == 'faithful_transcript_edit':
            payload = {**supplied, 'speaker_uncertain': False}
        elif schema_name == 'source_grounded_author_review':
            findings = []
            for excerpt, reason in (
                ('I think that was unfair.', 'personal_opinion'),
                ('I cannot confirm who said it.', 'unverified_attribution'),
                ('The synthetic treasurer stole the synthetic fund.', 'serious_allegation'),
                ('The synthetic participant disclosed a medical diagnosis.', 'sensitive_disclosure'),
                ('दिन café क़ 가 ΐ 👩\u200d💻 €100.', 'ambiguity_wording'),
            ):
                offset = 0
                while (start := supplied['text'].find(excerpt, offset)) >= 0:
                    end = start + len(excerpt)
                    offset = end
                    if start >= supplied['core_end'] or end <= supplied['core_start']:
                        continue
                    category, severity, *_ = REASONS[reason]
                    findings.append({'category': category, 'severity': severity,
                                     'reason_code': reason, 'excerpt': excerpt,
                                     'start': start, 'end': end})
            payload = {'chunk_index': supplied['chunk_index'], 'fully_reviewed': True,
                       'reviewed_start': supplied['core_start'],
                       'reviewed_end': supplied['core_end'], 'findings': findings}
        else:
            raise ComparisonError('Synthetic provider received an unsupported schema.')
        return SimpleNamespace(choices=[SimpleNamespace(
            finish_reason='stop', message=SimpleNamespace(
                content=json.dumps(payload, ensure_ascii=False), refusal=None))])


class _CheckpointMiss(BaseException):
    """Preflight stops before the first absent checkpoint without a provider call."""


class _PreflightClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    @staticmethod
    def _create(**parameters):
        raise _CheckpointMiss()


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2) + '\n'


def _read_json(path):
    if path.is_symlink() or not path.is_file():
        raise ComparisonError(SAFE_STORAGE)
    def unique(pairs):
        result = {}
        for name, value in pairs:
            if name in result:
                raise ComparisonError(SAFE_STORAGE)
            result[name] = value
        return result
    return json.loads(path.read_bytes().decode('utf-8'), object_pairs_hook=unique)


def _read_text(path):
    source = Path(path).absolute()
    # Match the known macOS system aliases accepted by private_output.
    if sys.platform == 'darwin':
        for alias in ('/var', '/tmp'):
            parent = Path(alias)
            if parent in source.parents and parent.resolve() == Path('/private' + alias):
                source = Path('/private' + alias) / source.relative_to(parent)
                break
    try:
        if source.resolve() != source or not stat.S_ISREG(source.lstat().st_mode):
            raise ComparisonError('Text input must be a regular UTF-8 file without symlinks or parent traversal.')
        with source.open('rb') as stream:
            data = stream.read(MAX_INPUT_BYTES + 1)
        if len(data) > MAX_INPUT_BYTES:
            raise ComparisonError('Text comparisons accept at most 64 KiB of UTF-8 source; select a small sample.')
        text = data.decode('utf-8')
        if not text.strip():
            raise ComparisonError('Text comparison input must be nonempty.')
        return text
    except (OSError, UnicodeError):
        raise ComparisonError('Cannot read comparison input; check regular-file access and UTF-8 encoding.') from None


@contextmanager
def _lock(root):
    path = root / '.comparison.lock'
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except OSError:
        raise ComparisonError(SAFE_STORAGE) from None
    os.close(descriptor)
    try:
        yield
    finally:
        path.unlink()


def _case_binding(case, options, raw, turns, mode):
    settings = (Transcriber(client=_PreflightClient(), editing_options=options).attributed_editing_fingerprint
                if case.stage == 'attributed-polish' else options.fingerprint)
    return {'version': 1, 'stage': case.stage, 'model': case.model,
            'reasoning_effort': case.effort, 'settings_fingerprint': settings,
            'source_sha256': hashlib.sha256(raw.encode()).hexdigest(),
            'turns_sha256': _fingerprint(turns) if case.stage == 'attributed-polish' else None,
            'provider_mode': mode, 'fixture_version': FIXTURE_VERSION if mode == 'synthetic' else None}


def _prepare_case(root, case, options, raw, turns, mode, resume):
    binding = _case_binding(case, options, raw, turns, mode)
    directory = (root / mode / case.stage / case.model / case.effort
                 / binding['source_sha256'] / binding['settings_fingerprint'])
    if os.path.lexists(directory):
        if not resume or directory.is_symlink() or not directory.is_dir():
            raise ComparisonError(SAFE_STORAGE)
        allowed = {'binding.json', 'source.txt', 'checkpoints', 'artifacts'}
        for entry in directory.iterdir():
            if entry.is_symlink() or (entry.name not in allowed
                    and not re.fullmatch(r'attempt-[0-9a-f]{32}\.json', entry.name)):
                raise ComparisonError(SAFE_STORAGE)
        if (_read_json(directory / 'binding.json') != binding
                or (directory / 'source.txt').is_symlink()
                or (directory / 'source.txt').read_bytes() != raw.encode()):
            raise ComparisonError(SAFE_STORAGE)
    else:
        parent = output_directory(directory.parent)
        temporary = Path(tempfile.mkdtemp(prefix='.comparison-', dir=parent))
        try:
            write_private(temporary / 'binding.json', _json(binding))
            write_private(temporary / 'source.txt', raw)
            os.rename(temporary, directory)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
    return directory, binding


def _execute_case(raw, turns, case, options, client, checkpoint_root, timeout, retries):
    if case.stage == 'review':
        output = review_transcript(raw, ControlledClient(client, timeout, retries), options,
                                   checkpoint_root=checkpoint_root)
        if output['status'] == 'complete':
            validate_review_report(raw, output, options)
        return output
    transcriber = Transcriber(client=client, editing_options=options,
                              provider_timeout=timeout, provider_retries=retries)
    if case.stage == 'attributed-polish':
        return transcriber.enhance_attributed(raw, turns, checkpoint_root=checkpoint_root)
    return transcriber.enhance_transcription(raw, checkpoint_root=checkpoint_root)


def _artifact_text(case, output):
    return _json(output) if case.stage == 'review' else output


def _artifact_name(case):
    return 'author_review.json' if case.stage == 'review' else 'derivative_readability.txt'


def _read_artifact(directory, binding, case, raw, options):
    parent = directory / 'artifacts'
    if not os.path.lexists(parent):
        return None
    name = _artifact_name(case)
    if (parent.is_symlink() or not parent.is_dir()
            or {entry.name for entry in parent.iterdir()} != {name, 'completed.json'}):
        raise ComparisonError(SAFE_STORAGE)
    target = parent / name
    if target.is_symlink() or not target.is_file():
        raise ComparisonError(SAFE_STORAGE)
    expected = {'version': 1, 'binding_sha256': _fingerprint(binding),
                'artifact': name, 'artifact_sha256': digest(target)}
    if _read_json(parent / 'completed.json') != expected:
        raise ComparisonError(SAFE_STORAGE)
    output = _read_json(target) if case.stage == 'review' else target.read_bytes().decode('utf-8')
    if case.stage == 'review':
        validate_review_report(raw, output, options)
    return output


def _publish_artifact(directory, binding, case, output):
    temporary = Path(tempfile.mkdtemp(prefix='.complete-', dir=directory))
    try:
        name = _artifact_name(case)
        target = temporary / name
        write_private(target, _artifact_text(case, output))
        write_private(temporary / 'completed.json', _json({
            'version': 1, 'binding_sha256': _fingerprint(binding),
            'artifact': name, 'artifact_sha256': digest(target)}))
        if os.path.lexists(directory / 'artifacts'):
            raise ComparisonError(SAFE_STORAGE)
        os.rename(temporary, directory / 'artifacts')
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def _quality(case, raw, turns, output, mode):
    if case.stage == 'review':
        chunks = output['coverage']['chunks']
        exact = all(raw[item['start']:item['end']] == item['excerpt']
                    for item in output['findings'])
        return {'exact_source_valid': exact,
                'complete_source_acknowledgement': output['coverage']['complete'],
                'findings': len(output['findings']),
                'validation_source_failures': sum(item.get('error_category') == 'validation_source'
                                                  for item in chunks),
                'validation_failures': sum(item.get('error_category', '').startswith('validation')
                                           for item in chunks),
                'human_quality_assessed': False}
    # The original polish method can prepend a fixed, local uncertainty notice.
    match = re.match(r'^\[Speaker attribution uncertain in chunks: [0-9]+(?:, [0-9]+)*\]\n\n', output)
    notice_added = bool(match and words(output) != words(raw)
                        and words(output[match.end():]) == words(raw))
    speech_output = output[match.end():] if notice_added else output
    result = {'word_symbol_fidelity_valid': words(raw) == words(speech_output),
              'local_uncertainty_notice': notice_added, 'human_quality_assessed': False}
    if case.stage == 'attributed-polish':
        result.update(turn_fidelity_valid=True, immutable_boundaries_preserved=True,
                      turns=len(turns), empty_or_whitespace_turns=sum(
                          not raw[turn['speech_start']:turn['speech_end']].strip() for turn in turns))
        if mode == 'synthetic':
            result['synthetic_exact_text_preserved'] = output == raw
    return result


def run_comparison(raw, turns, output_dir, cases, *, mode='synthetic', resume=False,
                   chunk_bytes=6000, provider_timeout=120, provider_retries=0,
                   provider_failure_limit=2, max_provider_requests=None, client=None):
    """Run only supplied text; no audio, chapter stage, media lookup or batch mutation."""
    if mode not in {'synthetic', 'openai'} or not isinstance(raw, str) or not raw.strip():
        raise ComparisonError('Comparison requires nonempty UTF-8 text and a documented provider mode.')
    if len(raw.encode()) > MAX_INPUT_BYTES:
        raise ComparisonError('Text comparisons accept at most 64 KiB of UTF-8 source.')
    if not cases or len(set(cases)) != len(cases):
        raise ComparisonError('Choose at least one case without duplicate stage/model/effort combinations.')
    if any(case.stage == 'attributed-polish' for case in cases) and (mode != 'synthetic' or not turns):
        raise ComparisonError('Attributed comparison requires the built-in synthetic turn fixture.')
    root = output_directory(output_dir)
    control = ProviderControl(max_requests=max_provider_requests, retries=provider_retries,
                              failure_limit=provider_failure_limit)
    prepared = []
    with interruptions(), _lock(root):
        # Validate all saved cases and all saved request checkpoints before a live
        # client is created. A miss is allowed only for an unfinished case.
        for case in cases:
            options = case.options(chunk_bytes)
            directory, binding = _prepare_case(root, case, options, raw, turns, mode, resume)
            saved = _read_artifact(directory, binding, case, raw, options)
            try:
                reconstructed = _execute_case(raw, turns, case, options, _PreflightClient(),
                                              directory / 'checkpoints', provider_timeout, 0)
            except _CheckpointMiss:
                if saved is not None:
                    raise ComparisonError(SAFE_STORAGE) from None
            else:
                if saved is not None and saved != reconstructed:
                    raise ComparisonError(SAFE_STORAGE)
            prepared.append((case, options, directory, binding, saved))
        if client is None:
            client = SyntheticClient() if mode == 'synthetic' else Transcriber(
                provider_timeout=provider_timeout, provider_retries=provider_retries)._client
        results = []
        token = CURRENT_CONTROL.set(control)
        try:
            for case, options, directory, binding, saved in prepared:
                started = time.monotonic()
                summary = {'stage': case.stage, 'model': case.model, 'reasoning_effort': case.effort,
                           'settings_fingerprint': binding['settings_fingerprint'],
                           'completed_artifact_reused': saved is not None,
                           'configured_sdk_retries': provider_retries,
                           'configured_request_timeout_seconds': provider_timeout}
                with collect_text_metrics() as metrics:
                    try:
                        output = _execute_case(raw, turns, case, options, client,
                                               directory / 'checkpoints', provider_timeout, provider_retries)
                        complete = case.stage != 'review' or output['status'] == 'complete'
                        summary['status'] = 'complete' if complete else output['status']
                        summary['validation'] = _quality(case, raw, turns, output, mode)
                        if complete and case.stage != 'review' and not summary['validation']['word_symbol_fidelity_valid']:
                            raise ResponseValidationError('Comparison polishing failed source fidelity.',
                                                          category='validation_source')
                        if (complete and case.stage == 'attributed-polish' and mode == 'synthetic'
                                and not summary['validation']['synthetic_exact_text_preserved']):
                            raise ResponseValidationError('Synthetic comparison changed source boundaries.',
                                                          category='validation_source')
                        if complete:
                            if saved is None:
                                _publish_artifact(directory, binding, case, output)
                            elif saved != output:
                                raise ComparisonError(SAFE_STORAGE)
                        elif case.stage == 'review':
                            # Failure evidence is private and immutable; successful
                            # source-bound checkpoints remain available on resume.
                            write_private(directory / f'attempt-{uuid.uuid4().hex}.json', _json({
                                'version': 1, 'binding_sha256': _fingerprint(binding),
                                'incomplete_review': output}))
                    except ComparisonError:
                        raise
                    except Exception as error:
                        summary.update(status='failed', **classify(error))
                summary['metrics'] = metrics.snapshot()
                summary['wall_latency_seconds'] = round(time.monotonic() - started, 6)
                if case.stage == 'attributed-polish':
                    groups = grouped_turns(raw, turns, options.chunk_bytes)
                    baseline = sum(bool(raw[t['speech_start']:t['speech_end']].strip()) for t in turns)
                    summary['request_comparison'] = {
                        'unbatched_nonempty_turn_requests': baseline,
                        'planned_group_requests': len(groups),
                        'measured_sdk_operations': summary['metrics']['sdk_operations_started'],
                        'measured_reduction_fraction': (
                            round(1 - summary['metrics']['sdk_operations_started'] / baseline, 6)
                            if baseline and not summary['metrics']['cache_hits'] else None),
                    }
                write_private(directory / f'attempt-{uuid.uuid4().hex}.json', _json({
                    'version': 1, 'binding_sha256': _fingerprint(binding), 'result': summary}))
                results.append(summary)
        finally:
            CURRENT_CONTROL.reset(token)
        report = {'version': 1, 'provider_mode': mode,
                  'source_sha256': hashlib.sha256(raw.encode()).hexdigest(),
                  'source_bytes': len(raw.encode()), 'source_characters': len(raw),
                  'quality_conclusion': 'Human comparison required; synthetic checks do not establish model quality.',
                  'cases': results}
        write_private(root / f'comparison-{uuid.uuid4().hex}.json', _json(report))
        return report


def _positive_timeout(value):
    try:
        result = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError('Use a positive finite request timeout.') from None
    if not math.isfinite(result) or result <= 0:
        raise argparse.ArgumentTypeError('Use a positive finite request timeout.')
    return result


def parser():
    result = PrivateArgumentParser(
        prog='interview compare-text', color=False, allow_abbrev=False,
        description='Interview Studio: compare isolated text polish and editorial review. '
                    'Default runs use only a synthetic local provider; no key or network is required.')
    result.add_argument('--output-folder', '--output-dir', dest='output_dir', default='private/text-comparisons',
                        help='Private comparison directory; existing cases require --resume.')
    result.add_argument('--text-file', help='Regular UTF-8 text sample, at most 64 KiB; no audio is read.')
    result.add_argument('--send-to-openai', action='store_true',
                        help='Explicitly send the supplied --text-file to OpenAI; incurs charges.')
    result.add_argument('--case', action='append', metavar='STAGE:MODEL:EFFORT',
                        help='Repeat to select polish, review, or synthetic attributed-polish; '
                             'models: gpt-6.1-sol, gpt-6-astra; efforts: low, medium, high.')
    result.add_argument('--chunk-bytes', type=int, default=6000,
                        help='Maximum source/core bytes, 64 through 6000 (default: 6000).')
    result.add_argument('--resume', action='store_true', help='Validate and reuse existing case checkpoints.')
    result.add_argument('--request-timeout', type=_positive_timeout, default=120,
                        help='Finite timeout per SDK operation in seconds (default: 120).')
    result.add_argument('--request-retries', '--provider-retries', dest='provider_retries', type=int,
                        choices=range(6), default=0,
                        help='SDK retries per operation, 0 through 5 (default: 0); '
                             'validation recovery may add one operation when retries are enabled.')
    result.add_argument('--failure-limit', '--provider-failure-limit', dest='provider_failure_limit', type=int, default=2,
                        help='Stop later operations after this many consecutive failures per model (default: 2).')
    result.add_argument('--max-requests', '--max-provider-requests', dest='max_provider_requests', type=int,
                        help='Optional total SDK-operation admission cap across cases; requires retries 0.')
    return result


def main(argv=None):
    argument_parser = parser()
    args = argument_parser.parse_args(argv)
    if args.send_to_openai and not args.text_file:
        argument_parser.usage_error('--send-to-openai requires an explicit --text-file.')
    try:
        cases = []
        for value in args.case or []:
            pieces = value.split(':')
            if (len(pieces) != 3 or pieces[0] not in STAGES or pieces[1] not in EDITING_MODELS
                    or pieces[2] not in REASONING_EFFORTS):
                argument_parser.usage_error('Use --case STAGE:MODEL:EFFORT with documented values.')
            cases.append(ComparisonCase(*pieces))
        if not cases:
            cases = [case for case in DEFAULT_CASES
                     if not args.text_file or case.stage != 'attributed-polish']
        if args.text_file:
            raw, turns = _read_text(args.text_file), None
        else:
            raw, turns = synthetic_fixture()
        if any(case.stage == 'attributed-polish' for case in cases) and (args.text_file or args.send_to_openai):
            argument_parser.usage_error('Attributed comparison is available only for the built-in synthetic turn fixture.')
        report = run_comparison(raw, turns, args.output_dir, cases,
            mode='openai' if args.send_to_openai else 'synthetic', resume=args.resume,
            chunk_bytes=args.chunk_bytes, provider_timeout=args.request_timeout,
            provider_retries=args.provider_retries, provider_failure_limit=args.provider_failure_limit,
            max_provider_requests=args.max_provider_requests)
        print(_json(report), end='')
        return 0 if all(case['status'] == 'complete' for case in report['cases']) else 1
    except KeyboardInterrupt:
        print('Comparison interrupted; completed artifacts and checkpoints are retained.', file=sys.stderr)
        return 130
    except (ComparisonError, OutputError):
        print('Comparison could not start or resume safely; use --help and fresh private output.', file=sys.stderr)
        return 1
    except Exception:
        print('Comparison failed; private checkpoints are retained. Check configuration and source validation locally.', file=sys.stderr)
        return 1
