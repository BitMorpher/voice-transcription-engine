"""Small source-bound author stages, run under the existing pipeline job lock."""

import hashlib
import json
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

if __package__:
    from .provider_control import ProviderStopped
    from .derivative_versions import select_version, remember_version
    from .author_review import ReviewError, ReviewOptions, review_transcript, validate_review_report
    from .chapters import ChapterError, ChapterOptions, draft_chapters, render_chapter
    from .private_output import digest, write_private
    from .review_export import export_review
    from .source_provenance import author_binding, bind_report, bind_chapters, validate_binding
else:
    from provider_control import ProviderStopped
    from derivative_versions import select_version, remember_version
    from author_review import ReviewError, ReviewOptions, review_transcript, validate_review_report
    from chapters import ChapterError, ChapterOptions, draft_chapters, render_chapter
    from private_output import digest, write_private
    from review_export import export_review
    from source_provenance import author_binding, bind_report, bind_chapters, validate_binding


class AuthorWorkflowError(RuntimeError):
    """Fixed, privacy-safe author workflow failure."""

    def __init__(self, message, *, admission_stopped=False):
        super().__init__(message)
        self.admission_stopped = admission_stopped


@dataclass(frozen=True)
class AuthorOptions:
    review: bool = True
    review_options: ReviewOptions = ReviewOptions()
    chapter_options: ChapterOptions | None = None
    allow_unresolved_high: bool = False

    def __post_init__(self):
        if type(self.review) is not bool or type(self.allow_unresolved_high) is not bool:
            raise AuthorWorkflowError('Author stage selection and draft override must be explicit booleans.')
        if self.chapter_options is not None and not self.review:
            raise AuthorWorkflowError('Chapter drafts require author review first.')
        if self.allow_unresolved_high and self.chapter_options is None:
            raise AuthorWorkflowError('The unresolved-high override requires chapter drafts.')

    def fingerprint(self, stage, raw_sha256, review_sha256=None):
        value = {'contract': 1, 'stage': stage, 'raw_sha256': raw_sha256}
        if stage == 'author_review':
            value['options'] = self.review_options.fingerprint
            value['export_contract'] = 1
        else:
            value.update(options=self.chapter_options.fingerprint,
                         review_sha256=review_sha256,
                         allow_unresolved_high=self.allow_unresolved_high)
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _raw_snapshot(job, expected):
    target = job / 'transcription.txt'
    try:
        if target.is_symlink():
            raise ValueError()
        data = target.read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError()
        return data.decode('utf-8')
    except (OSError, ValueError):
        raise AuthorWorkflowError('Raw transcript changed or is unreadable; use a stable source and a new output folder.') from None


def _verified_bundle(job, record, fingerprint, raw_sha256, expected_names):
    if (not isinstance(record, dict) or record.get('status') != 'complete'
            or record.get('configuration_sha256') != fingerprint
            or record.get('transcription_sha256') != raw_sha256
            or not isinstance(record.get('artifacts'), dict) or not record['artifacts']
            or any(not isinstance(name, str) for name in record['artifacts'])
            or {Path(name).name for name in record['artifacts']} != expected_names
            or len(record['artifacts']) != len(expected_names)):
        return False
    if len({Path(name).parts[0] for name in record['artifacts']}) != 1:
        return False
    for filename, expected in record['artifacts'].items():
        if not isinstance(filename, str) or not isinstance(expected, str):
            return False
        path = Path(filename)
        if (path.is_absolute() or len(path.parts) != 2 or '..' in path.parts
                or (job / path.parts[0]).is_symlink()):
            return False
        target = job / path
        try:
            if target.is_symlink() or not target.is_file() or digest(target) != expected:
                return False
        except OSError:
            return False
    return True


def _publish_bundle(job, stage, writer):
    """Atomic directory publication; interrupted attempts cannot look complete."""
    temporary = Path(tempfile.mkdtemp(prefix='.author-', dir=job))
    final = job / f'{stage}_{uuid.uuid4().hex}'
    try:
        writer(temporary)
        artifacts = {str(Path(final.name) / path.name): digest(path)
                     for path in temporary.iterdir() if path.is_file()}
        if os.path.lexists(final):
            raise AuthorWorkflowError('Author bundle already exists; no artifact was overwritten.')
        os.rename(temporary, final)
        return artifacts
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def _artifact(job, record, name):
    matches = [job / filename for filename in record['artifacts']
               if isinstance(filename, str) and Path(filename).name == name]
    if len(matches) != 1:
        raise AuthorWorkflowError('Author stage artifact references are invalid; use a new output folder.')
    return matches[0]


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def _load_bound_report(job, record, raw, options, *, provenance=None):
    """Parse the same bytes whose checksum is bound in the manifest."""
    try:
        path = _artifact(job, record, 'review_report.json')
        data = path.read_bytes()
        expected = record['artifacts'][str(path.relative_to(job))]
        checksum = hashlib.sha256(data).hexdigest()
        if path.is_symlink() or checksum != expected:
            raise ValueError()
        report = json.loads(data.decode('utf-8'), object_pairs_hook=_unique_object)
        if not isinstance(report, dict):
            raise ValueError()
        validate_review_report(raw, report, options)
        if provenance is not None:
            validate_binding(report, provenance)
        return report, checksum
    except (OSError, ValueError, KeyError, TypeError, ReviewError):
        raise AuthorWorkflowError('Saved author review is invalid or changed; use a new output folder.') from None


def _check_report_unchanged(job, record, expected):
    path = _artifact(job, record, 'review_report.json')
    try:
        if path.is_symlink() or digest(path) != expected:
            raise OSError()
    except OSError:
        raise AuthorWorkflowError('Author review changed during drafting; no accepted draft was saved.') from None


def run_author_stages(job, state, transcriber, options, *, resume, save, summary, progress=None, provenance=None):
    """Retain unsuccessful review attempts; only complete bound reports are reusable."""
    progress = progress or (lambda stage, status: None)
    raw_hash = state['stages']['transcription']['sha256']
    report = None
    stages = ['author_review'] if options.review else []
    if options.chapter_options is not None:
        stages.append('chapters')
    summary.update({stage: 'pending' for stage in stages})
    for stage in stages:
        progress(stage, 'running')
        try:
            raw = _raw_snapshot(job, raw_hash)
        except AuthorWorkflowError:
            summary[stage] = 'failed'
            raise
        expected_names = ({'review_report.json', 'review_report.xlsx'} if stage == 'author_review'
                          else {'chapter_drafts.json', *(f'chapter_{style}.txt'
                               for style in options.chapter_options.styles)})
        review_hash = None
        if stage == 'chapters':
            report, review_hash = _load_bound_report(job, state['stages']['author_review'], raw, options.review_options, provenance=provenance)
        fingerprint = options.fingerprint(stage, raw_hash, review_hash)
        if provenance is not None:
            fingerprint = author_binding(fingerprint, provenance)
        try:
            record = select_version(job, state, stage, fingerprint, raw_hash)
        except (ValueError, OSError, TypeError, AttributeError):
            summary[stage] = 'failed'
            raise AuthorWorkflowError('Author artifact changed or settings changed; retain outputs and use a new output folder.') from None
        save()
        if resume and _verified_bundle(job, record, fingerprint, raw_hash, expected_names):
            if stage == 'author_review':
                report, _ = _load_bound_report(job, record, raw, options.review_options, provenance=provenance)
            _raw_snapshot(job, raw_hash)
            if stage == 'chapters':
                _check_report_unchanged(job, state['stages']['author_review'], review_hash)
            summary[stage] = 'skipped'
            progress(stage, 'skipped')
            continue
        # A complete bundle with changed configuration or integrity is a conflict,
        # never silently replaced. Failed/incomplete attempts remain inspectable.
        if isinstance(record, dict) and record.get('status') == 'complete':
            summary[stage] = 'failed'
            raise AuthorWorkflowError('Author artifact changed or settings changed; use a new output folder. No artifact was overwritten.')
        try:
            if stage == 'author_review':
                report = review_transcript(raw, transcriber.client, options.review_options,
                                           checkpoint_root=job / 'text-chunks')
                if report.get('status') == 'complete':
                    validate_review_report(raw, report, options.review_options)
                _raw_snapshot(job, raw_hash)
                if provenance is not None:
                    bind_report(report, provenance)

                def writer(directory):
                    write_private(directory / 'review_report.json', json.dumps(report, indent=2, ensure_ascii=False) + '\n')
                    export_review(report, directory / 'review_report.xlsx')
            else:
                chapters = draft_chapters(raw, report, transcriber.client, options.chapter_options,
                                          allow_unresolved_high=options.allow_unresolved_high,
                                          checkpoint_root=job / 'text-chunks')
                _raw_snapshot(job, raw_hash)
                _check_report_unchanged(job, state['stages']['author_review'], review_hash)
                if provenance is not None:
                    bind_chapters(chapters, provenance)

                def writer(directory):
                    write_private(directory / 'chapter_drafts.json', json.dumps(chapters, indent=2, ensure_ascii=False) + '\n')
                    for style in options.chapter_options.styles:
                        write_private(directory / f'chapter_{style}.txt', render_chapter(chapters, style))
            artifacts = _publish_bundle(job, stage, writer)
            _raw_snapshot(job, raw_hash)
            if stage == 'chapters':
                _check_report_unchanged(job, state['stages']['author_review'], review_hash)
            status = report['status'] if stage == 'author_review' else 'complete'
            state['stages'][stage] = {
                'status': status, 'configuration_sha256': fingerprint,
                'transcription_sha256': raw_hash, 'artifacts': artifacts,
                'model': report['model'] if stage == 'author_review' else chapters['model'],
                'prompt_version': report['prompt_version'] if stage == 'author_review' else chapters['prompt_version'],
                'schema_version': report['schema_version'] if stage == 'author_review' else chapters['schema_version'],
                'reasoning_effort': (options.review_options.reasoning_effort if stage == 'author_review'
                                     else options.chapter_options.reasoning_effort),
            }
            if stage == 'author_review':
                state['stages'][stage]['prompt_sha256'] = report['prompt_sha256']
            else:
                state['stages'][stage]['prompt_sha256'] = chapters['prompt_sha256']
            if review_hash:
                state['stages'][stage]['review_sha256'] = review_hash
            remember_version(state, stage)
            save()
            summary[stage] = status
            progress(stage, status)
            if status != 'complete':
                unreviewed = [c for c in report['coverage']['chunks'] if c['status'] != 'complete']
                raise AuthorWorkflowError('Author review failed or is incomplete; inspect the saved report. Chapter drafting is blocked.',
                    admission_stopped=bool(unreviewed) and all(
                        c.get('error_category') == 'not_attempted' for c in unreviewed))
        except AuthorWorkflowError:
            if summary[stage] == 'pending':
                summary[stage] = 'failed'
            raise
        except Exception as error:
            stopped = isinstance(error, ProviderStopped)
            state['stages'][stage] = {'status': 'incomplete' if stopped else 'failed', 'configuration_sha256': fingerprint,
                                      'transcription_sha256': raw_hash}
            remember_version(state, stage)
            save()
            summary[stage] = 'incomplete' if stopped else 'failed'
            message = str(error) if type(error) is ChapterError else 'Author stage failed; inspect review findings and retain the raw transcript.'
            raise AuthorWorkflowError(message, admission_stopped=stopped) from None
