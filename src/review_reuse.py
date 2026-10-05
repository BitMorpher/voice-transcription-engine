"""Reuse the exact approved review bytes across ordered chapter generations."""

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path

if __package__:
    from .author_workflow import (AuthorWorkflowError, _load_bound_report, _publish_bundle,
                                 _raw_snapshot, _unique_object, _verified_bundle)
    from .private_output import digest
    from .source_provenance import author_binding
else:
    from author_workflow import (AuthorWorkflowError, _load_bound_report, _publish_bundle,
                                _raw_snapshot, _unique_object, _verified_bundle)
    from private_output import digest
    from source_provenance import author_binding


SAFE_APPROVAL = ('Approved review changed, conflicts, or does not match this generation; '
                 'retain outputs and review the exact bundle again before drafting.')


def _fingerprint(record):
    return hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()


def _read(path):
    if path.is_symlink():
        raise ValueError()
    return json.loads(path.read_bytes().decode('utf-8'), object_pairs_hook=_unique_object)


@dataclass(frozen=True)
class ApprovedReview:
    """Capture identities at the human gate, never just a mutable bundle path."""
    job: Path
    manifest_sha256: str
    raw_sha256: str
    provenance_sha256: str
    record_sha256: str
    report_sha256: str

    @classmethod
    def capture(cls, job, state, report_sha256):
        return cls(job, digest(job / 'manifest.json'), state['stages']['transcription']['sha256'],
                   state['provenance_sha256'], _fingerprint(state['stages']['author_review']),
                   report_sha256)


def validate_approved_review(approval, options, *, attributed=None):
    """Revalidate a captured family approval before any other family requests."""
    try:
        if type(approval) is not ApprovedReview:
            raise ValueError()
        source = approval.job
        if any(path.is_symlink() for path in (source, *source.parents,
                                              source / 'manifest.json', source / 'provenance.json')):
            raise ValueError()
        if digest(source / 'manifest.json') != approval.manifest_sha256:
            raise ValueError()
        state = _read(source / 'manifest.json')
        provenance = _read(source / 'provenance.json')
        if attributed is not None and ('attribution' in provenance) != attributed:
            raise ValueError()
        raw = _raw_snapshot(source, approval.raw_sha256)
        record = state['stages']['author_review']
        configuration = author_binding(options.fingerprint('author_review', approval.raw_sha256), provenance)
        if (state['provenance_sha256'] != approval.provenance_sha256
                or digest(source / 'provenance.json') != approval.provenance_sha256
                or _fingerprint(record) != approval.record_sha256
                or not _verified_bundle(source, record, configuration, approval.raw_sha256,
                                        {'review_report.json', 'review_report.xlsx'})):
            raise ValueError()
        report, checksum = _load_bound_report(source, record, raw, options.review_options,
                                             provenance=provenance)
        if checksum != approval.report_sha256 or any(f['severity'] == 'high' for f in report['findings']):
            raise ValueError()
    except (OSError, ValueError, KeyError, TypeError, AuthorWorkflowError):
        raise AuthorWorkflowError(SAFE_APPROVAL) from None


def reuse_approved_review(approval, job, state, options, provenance, save):
    """Validate approval again under the interview lock, then atomically copy bytes.

    Complete conflicting bundles are never replaced. The report and workbook
    copied here are byte-identical to the bundle accepted by the gate, and the
    normal author-stage verifier will skip review rather than requesting it.
    """
    try:
        if type(approval) is not ApprovedReview or options.chapter_options is None:
            raise ValueError()
        source = approval.job
        if any(path.is_symlink() for path in (source, *source.parents,
                                                source / 'manifest.json', source / 'provenance.json')):
            raise ValueError()
        if digest(source / 'manifest.json') != approval.manifest_sha256:
            raise ValueError()
        original = _read(source / 'manifest.json')
        record = original['stages']['author_review']
        raw_hash = state['stages']['transcription']['sha256']
        raw = _raw_snapshot(job, raw_hash)
        configuration = author_binding(options.fingerprint('author_review', raw_hash), provenance)
        if (raw_hash != approval.raw_sha256
                or _raw_snapshot(source, raw_hash) != raw
                or original['provenance_sha256'] != approval.provenance_sha256
                or digest(source / 'provenance.json') != approval.provenance_sha256
                or _read(source / 'provenance.json') != provenance
                or _fingerprint(record) != approval.record_sha256
                or not _verified_bundle(source, record, configuration, raw_hash,
                                        {'review_report.json', 'review_report.xlsx'})):
            raise ValueError()
        report, checksum = _load_bound_report(source, record, raw, options.review_options,
                                             provenance=provenance)
        if checksum != approval.report_sha256 or any(f['severity'] == 'high' for f in report['findings']):
            raise ValueError()
        payloads = {Path(name).name: (source / name).read_bytes() for name in record['artifacts']}
        if any(hashlib.sha256(payloads[Path(name).name]).hexdigest() != checksum
               for name, checksum in record['artifacts'].items()):
            raise ValueError()
        existing = state['stages'].get('author_review')
        if existing is not None:
            # Even an incomplete/newly regenerated review must not replace what
            # the caller approved. Any conflict needs a separate human decision.
            if (not _verified_bundle(job, existing, configuration, raw_hash, set(payloads))
                    or {Path(name).name: checksum for name, checksum in existing['artifacts'].items()}
                    != {name: hashlib.sha256(data).hexdigest() for name, data in payloads.items()}):
                raise ValueError()
        else:
            def writer(directory):
                for name, data in payloads.items():
                    fd = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(fd, 'wb') as stream:
                        stream.write(data)
                        stream.flush()
                        os.fsync(stream.fileno())
            artifacts = _publish_bundle(job, 'author_review', writer)
            state['stages']['author_review'] = {**record, 'artifacts': artifacts}
        # Catch edits during transfer before accepting the seeded review.
        if (digest(source / 'manifest.json') != approval.manifest_sha256
                or not _verified_bundle(source, record, configuration, raw_hash, set(payloads))
                or digest(source / 'provenance.json') != approval.provenance_sha256):
            raise ValueError()
        _raw_snapshot(source, approval.raw_sha256)
        save()
        return approval.report_sha256
    except (OSError, ValueError, KeyError, TypeError, AuthorWorkflowError):
        raise AuthorWorkflowError(SAFE_APPROVAL) from None
