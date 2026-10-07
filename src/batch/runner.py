"""One ordered session per worker, with separate review/chapter gates."""

from dataclasses import replace
import json
from types import SimpleNamespace
from pathlib import Path

from ..author_review import ReviewOptions
from ..chapters import ChapterOptions
from ..author_workflow import AuthorOptions, AuthorWorkflowError, _load_bound_report, _verified_bundle
from ..model_config import TranscriptionOptions, EditingOptions, load_hints
from ..ordered_interview import OrderedInterview, _read_json
from ..interview_attribution import AttributedInterview, input_record
from ..transcriber import DEFAULT_UPLOAD_BYTES
from ..source_provenance import author_binding, validate_provenance

from ..cli import main as engine_main
from ..private_output import digest
from ..progress import CURRENT
from ..provider_control import CURRENT_CONTROL
from ..review_reuse import ApprovedReview, validate_approved_review
from .plan import require
from .storage import target, verify
from .prerequisites import FamilyBlocked


def _gate_original(root, item, phase, options, author_options):
    """Require completed stages for these sources and exact ASR/review settings."""
    if phase == 'raw':
        return
    directory = target(root, item)
    ordered = OrderedInterview(directory / 'input/interview.json', directory / 'output',
        options=options, editing_options=EditingOptions(), author_options=author_options, resume=True)
    ordered.preflight(original=False, require_raw=True)
    ledger = json.loads((directory / 'staging.json').read_text())['parts']
    matching_raw = False
    for manifest in (directory / 'output/interviews').glob('*/manifest.json'):
        job = manifest.parent
        if job.is_symlink() or manifest.is_symlink():
            continue
        state = json.loads(manifest.read_text())
        raw_path = job / 'transcription.txt'
        provenance_path = job / 'provenance.json'
        require(not raw_path.is_symlink() and not provenance_path.is_symlink(),
                'Combined artifacts are unsafe; use fresh output.')
        raw = raw_path.read_bytes().decode('utf-8')
        provenance = json.loads(provenance_path.read_text())
        validate_provenance(raw, provenance)
        require(digest(provenance_path) == state['provenance_sha256'], 'Provenance changed.')
        records = provenance['parts']
        if (len(records) != len(ledger) or any(
                record['source_sha256'] != staged['sha256']
                for record, staged in zip(records, ledger))):
            continue
        matching = True
        for record in records:
            relative = Path(record['raw_transcript'])
            require(not relative.is_absolute() and '..' not in relative.parts,
                    'Part reference is unsafe.')
            path = directory / 'output' / relative
            require(not any(parent.is_symlink() for parent in (path, *path.parents)),
                    'Part artifact is unsafe.')
            part_state = json.loads((path.parent / 'manifest.json').read_text())
            matching &= part_state.get('transcription_configuration_sha256') == options.fingerprint
            require(digest(path) == record['raw_sha256'], 'Part transcript changed.')
        checksum = digest(raw_path)
        if (not matching or state['stages'].get('transcription', {}).get('sha256') != checksum):
            continue
        if phase == 'review':
            return
        matching_raw = True
        review = state['stages'].get('author_review', {})
        fingerprint = author_binding(author_options.fingerprint('author_review', checksum), provenance)
        if not _verified_bundle(job, review, fingerprint, checksum,
                                {'review_report.json', 'review_report.xlsx'}):
            continue
        report, report_hash = _load_bound_report(job, review, raw, author_options.review_options,
                                      provenance=provenance)
        if any(finding['severity'] == 'high' for finding in report['findings']):
            raise FamilyBlocked('high_findings')
        return ApprovedReview.capture(job, state, report_hash)
    raise FamilyBlocked('review_prerequisite' if matching_raw else 'raw_prerequisite')


def gate(root, item, phase, options, author_options):
    """Fail closed with a fixed prerequisite reason, never provider guidance."""
    try:
        return _gate_original(root, item, phase, options, author_options)
    except FamilyBlocked:
        raise
    except Exception:
        raise FamilyBlocked('raw_prerequisite' if phase == 'review' else 'review_prerequisite') from None


def gate_attribution(root, item, phase, options, author_options, interview_options, args):
    if phase == 'raw' or interview_options is None:
        return None
    reason = 'raw_prerequisite'
    try:
        directory = target(root, item)
        editing = EditingOptions(model=args.editing_model,
                                 reasoning_effort=getattr(args, 'editing_reasoning_effort', 'high'))
        ordered = OrderedInterview(directory / 'input/interview.json', directory / 'output',
            options=options, editing_options=editing, author_options=author_options, resume=True)
        ordered.preflight(original=False, require_raw=True)
        inputs = [input_record(part['order'], folder / identity,
                               _read_json(folder / identity / 'manifest.json'))
                  for part, folder, identity in ordered.parts]
        selected_author = author_options
        if phase == 'chapters':
            styles = ('interview', 'narrative') if args.chapters == 'both' else (args.chapters,)
            selected_author = replace(author_options, chapter_options=ChapterOptions(
                model=args.author_model, styles=styles, person=args.narrative_person,
                reasoning_effort=getattr(args, 'review_reasoning_effort', 'high')))
        family = AttributedInterview(directory / 'output', inputs, interview_options,
            resume=True, enhance=True, author_options=selected_author)
        state, provenance = family.verified_raw(SimpleNamespace(options=options, editing_options=editing,
                                                                max_bytes=DEFAULT_UPLOAD_BYTES))
        if phase == 'review':
            return None
        reason = 'review_prerequisite'
        raw_hash = state['stages']['transcription']['sha256']
        record = state['stages'].get('author_review', {})
        fingerprint = author_binding(author_options.fingerprint('author_review', raw_hash), provenance)
        require(_verified_bundle(family.job, record, fingerprint, raw_hash,
                                 {'review_report.json', 'review_report.xlsx'}), 'Attributed review gate failed.')
        raw = (family.job / 'transcription.txt').read_bytes().decode('utf-8')
        report, checksum = _load_bound_report(family.job, record, raw, author_options.review_options,
                                             provenance=provenance)
        if any(f['severity'] == 'high' for f in report['findings']):
            raise FamilyBlocked('high_findings')
        return ApprovedReview.capture(family.job, state, checksum)
    except FamilyBlocked:
        raise
    except Exception:
        raise FamilyBlocked(reason) from None


def blocked(reporter, family, phase, reason):
    reporter.emit(status='blocked', family=family, stage='prerequisite', stage_status='blocked',
                  blocked_reason=reason, stages={
                      'phase_result': 'blocked',
                      'chapters' if phase == 'chapters' else 'author_review': 'blocked'})


def run_one(root, item, args):
    reporter = CURRENT.get()
    interview_options = getattr(args, 'interview_options_by_id', {}).get(item['id'])
    families = ['original', 'attributed'] if interview_options else ['original']
    try:
        directory = verify(root, item)
    except Exception:
        if args.phase == 'raw':
            raise
        for family in families:
            blocked(reporter, family, args.phase, 'staging_unverified')
        return 'blocked'
    context, keywords = load_hints(context_file=args.context_file, glossary_file=args.glossary_file)
    options = TranscriptionOptions(model=args.model, context=context, keywords=keywords,
                                   languages=tuple(args.language), chunk_seconds=args.audio_chunk_seconds)
    author_options = AuthorOptions(review=True, review_options=ReviewOptions(model=args.author_model,
        reasoning_effort=getattr(args, 'review_reasoning_effort', 'high')))
    command = ['--workflow', '--interview-manifest', str(directory / 'input/interview.json'),
               '--output-folder', str(directory / 'output'), '--resume', '--stages',
               'raw' if args.phase == 'raw' else 'raw,polish,review',
               '--model', args.model, '--editing-model', args.editing_model,
               '--author-model', args.author_model, '--audio-chunk-seconds', str(args.audio_chunk_seconds),
               '--editing-reasoning-effort', getattr(args, 'editing_reasoning_effort', 'high'),
               '--review-reasoning-effort', getattr(args, 'review_reasoning_effort', 'high'),
               '--text-profile', getattr(args, 'text_profile', 'legacy'),
               '--media-timeout', str(args.media_timeout),
               '--provider-timeout', str(args.provider_timeout), '--provider-retries', str(args.provider_retries)]
    for flag, value in (('--context-file', args.context_file), ('--glossary-file', args.glossary_file)):
        if value:
            command += [flag, value]
    for language in args.language:
        command += ['--language', language]
    if args.phase == 'chapters':
        command += ['--chapters', args.chapters, '--narrative-person', args.narrative_person]
    # No child stdout capture or raw SDK/error forwarding. The engine shares the safe reporter.
    interval = reporter.heartbeat
    context = reporter.context
    reporter.context = {**context, 'scope': 'interview'}
    try:
        command += ['--heartbeat-seconds', str(interval)]
        if args.phase == 'raw':
            code = engine_main(command, interview_options_override=interview_options)
            require(code == 0, 'Pipeline failed; completed caches remain available for resume.')
            return 'complete'
        results = []
        for family in families:
            reporter.context = {**context, 'scope': 'interview', 'family': family}
            try:
                approval = (gate(root, item, args.phase, options, author_options) if family == 'original'
                    else gate_attribution(root, item, args.phase, options, author_options, interview_options, args))
                if args.phase == 'chapters':
                    try:
                        validate_approved_review(approval, author_options, attributed=family == 'attributed')
                    except AuthorWorkflowError:
                        raise FamilyBlocked('review_prerequisite') from None
            except FamilyBlocked as error:
                blocked(reporter, family, args.phase, error.reason)
                results.append('blocked')
                continue
            control = CURRENT_CONTROL.get()
            if control and control.reason:
                reporter.emit(status='not_attempted', stage='preflight', stage_status='not_attempted',
                              stages={'phase_result': 'not_attempted'}, stop_reason=control.reason)
                if control.cancelled.is_set():
                    raise KeyboardInterrupt()
                results.append('not_attempted')
                continue
            reporter.emit(status='progress', stage='prerequisite', stage_status='complete')
            code = engine_main(command, approved_review=approval if family == 'original' else None,
                interview_options_override=interview_options if family == 'attributed' else None,
                approved_attributed_review=approval if family == 'attributed' else None,
                attributed_only=family == 'attributed', require_raw=True)
            outcome = 'complete' if code == 0 else 'incomplete' if (
                reporter.families(item['position']).get(family, {}).get('phase_result') == 'incomplete'
                or control and control.reason in {'request_limit', 'start_deadline'}) else 'failed'
            reporter.emit(status='progress', stage='phase_result', stage_status=outcome)
            results.append(outcome)
            if control and control.cancelled.is_set():
                raise KeyboardInterrupt()
        if 'not_attempted' in results:
            results.append('not_attempted' if all(s == 'not_attempted' for s in results) else 'incomplete')
        return next((state for state in ('failed', 'incomplete', 'blocked', 'not_attempted') if state in results), 'complete')
    finally:
        reporter.context = context
