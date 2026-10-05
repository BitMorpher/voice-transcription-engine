"""Serial failure isolation and separate review/chapter gates."""

import json
from pathlib import Path

from ..author_review import ReviewOptions
from ..author_workflow import AuthorOptions, _load_bound_report, _verified_bundle
from ..model_config import TranscriptionOptions, load_hints
from ..source_provenance import author_binding, validate_provenance

from ..cli import main as engine_main
from ..private_output import digest
from ..progress import CURRENT
from .plan import BatchError, require
from .storage import target, verify


def gate(root, item, phase, options, author_options):
    """Require completed stages for these sources and exact ASR/review settings."""
    if phase == 'raw':
        return
    directory = target(root, item)
    ledger = json.loads((directory / 'staging.json').read_text())['parts']
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
        review = state['stages'].get('author_review', {})
        fingerprint = author_binding(author_options.fingerprint('author_review', checksum), provenance)
        if not _verified_bundle(job, review, fingerprint, checksum,
                                {'review_report.json', 'review_report.xlsx'}):
            continue
        report, _ = _load_bound_report(job, review, raw, author_options.review_options,
                                      provenance=provenance)
        if not any(finding['severity'] == 'high' for finding in report['findings']):
            return
    raise BatchError('Review needs completed raw; chapters need intact complete review without high findings.')


def run_one(root, item, args):
    directory = verify(root, item)
    context, keywords = load_hints(context_file=args.context_file, glossary_file=args.glossary_file)
    options = TranscriptionOptions(model=args.model, context=context, keywords=keywords,
                                   languages=tuple(args.language), chunk_seconds=args.audio_chunk_seconds)
    author_options = AuthorOptions(review=True, review_options=ReviewOptions(model=args.author_model))
    gate(root, item, args.phase, options, author_options)
    command = ['--workflow', '--interview-manifest', str(directory / 'input/interview.json'),
               '--output-folder', str(directory / 'output'), '--resume', '--stages',
               'raw' if args.phase == 'raw' else 'raw,polish,review',
               '--model', args.model, '--editing-model', args.editing_model,
               '--author-model', args.author_model, '--audio-chunk-seconds', str(args.audio_chunk_seconds),
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
    reporter = CURRENT.get()
    interval = reporter.heartbeat
    context = reporter.context
    reporter.context = {**context, 'scope': 'interview'}
    try:
        code = engine_main(command + ['--heartbeat-seconds', str(interval)])
    finally:
        reporter.context = context
    require(code == 0, 'Pipeline failed; completed caches remain available for resume.')
