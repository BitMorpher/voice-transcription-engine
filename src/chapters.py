"""Conservative chapter drafts with mechanically checked source provenance.

Interview excerpts are deterministic. Narrative arrangement may group adjacent
source units and change punctuation/layout, but may not rewrite source words.
Third-person output frames exact testimony; it does not invent a narrator's story.
"""

import hashlib
import json
from dataclasses import asdict, dataclass
from importlib.resources import files

if __package__:
    from .provider_errors import classify
    from .progress import emit_progress
    from . import prompts
    from .author_review import CATEGORIES, source_segments
    from .model_config import EDITING_MODELS, ModelConfigurationError
    from .text_editing import split_text, words
    from .transcriber import _suppress_provider_logging
else:
    from provider_errors import classify
    from progress import emit_progress
    import prompts
    from author_review import CATEGORIES, source_segments
    from model_config import EDITING_MODELS, ModelConfigurationError
    from text_editing import split_text, words
    from transcriber import _suppress_provider_logging

CHAPTER_SCHEMA_VERSION = 1
PROMPT_VERSION = 'chapter-arrangement-v1'
OMISSION_REASONS = ('repetition', 'fragment', 'author_selection_needed')
RESOLVED_DISPOSITIONS = ('resolved', 'approved', 'accepted', 'addressed', 'dismissed')
QUOTATION_MARKS = frozenset('\"\'“”‘’«»‹›„‟')


class ChapterError(RuntimeError):
    """Safe chapter failure; raw transcript and review report remain available."""


def _prompt(style):
    try:
        return files(prompts).joinpath(f'chapter_{style}_v1.txt').read_text(encoding='utf-8')
    except (OSError, UnicodeError):
        raise ChapterError('Chapter prompt is unavailable; reinstall the package.') from None


def _digest(value):
    try:
        return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    except (TypeError, ValueError, UnicodeError):
        raise ChapterError('Chapter source metadata is not valid UTF-8 JSON.') from None


@dataclass(frozen=True)
class ChapterOptions:
    model: str = 'gpt-6-astra'
    styles: tuple[str, ...] = ('interview', 'narrative')
    person: str = 'first'
    reasoning_effort: str = 'high'
    chunk_bytes: int = 6000

    def __post_init__(self):
        if self.model not in EDITING_MODELS:
            raise ModelConfigurationError('Unsupported chapter model; use a documented editing model.')
        if (not isinstance(self.styles, tuple) or not self.styles
                or any(not isinstance(style, str) or style not in ('interview', 'narrative')
                       for style in self.styles)
                or len(set(self.styles)) != len(self.styles)):
            raise ModelConfigurationError('Chapter styles must select interview, narrative, or both.')
        if self.person not in ('first', 'third'):
            raise ModelConfigurationError('Narrative person must be first or third.')
        if self.reasoning_effort not in ('low', 'medium', 'high'):
            raise ModelConfigurationError('Chapter reasoning effort must be low, medium, or high.')
        if type(self.chunk_bytes) is not int or not 64 <= self.chunk_bytes <= 6000:
            raise ModelConfigurationError('Chapter chunks must be between 64 and 6000 UTF-8 bytes.')

    @property
    def fingerprint(self):
        return _digest({'schema_version': CHAPTER_SCHEMA_VERSION,
                        'prompt_version': PROMPT_VERSION, **asdict(self),
                        'schema': _schema(0, [{'unit_id': 'unit-schema-template'}], self.person),
                        'prompts': {style: _prompt(style) for style in self.styles}})


def _validate_report(raw, report):
    """A report cannot confer approval merely by being syntactically present."""
    try:
        source_hash = hashlib.sha256(raw.encode('utf-8')).hexdigest()
    except UnicodeError:
        raise ChapterError('Chapter input must be valid UTF-8 transcript text.') from None
    segments = source_segments(raw)
    try:
        coverage = report['coverage']
        if (report['status'] != 'complete' or report['raw_sha256'] != source_hash
                or report['segments'] != segments or coverage['complete'] is not True
                or type(coverage['reviewed_characters']) is not int
                or coverage['reviewed_characters'] != len(raw)
                or coverage['total_characters'] != len(raw)
                or not isinstance(report['findings'], list)):
            raise ValueError()
        chunks = coverage['chunks']
        cursor = 0
        if not isinstance(chunks, list) or not chunks:
            raise ValueError()
        for chunk in chunks:
            if (chunk['status'] != 'complete' or type(chunk['start']) is not int
                    or type(chunk['end']) is not int or chunk['start'] != cursor
                    or not cursor < chunk['end'] <= len(raw)):
                raise ValueError()
            cursor = chunk['end']
        if cursor != len(raw):
            raise ValueError()
        ids = set()
        for finding in report['findings']:
            start, end = finding['start'], finding['end']
            if (not isinstance(finding['finding_id'], str) or not finding['finding_id']
                    or finding['finding_id'] in ids or type(start) is not int
                    or type(end) is not int or not 0 <= start < end <= len(raw)
                    or finding['excerpt'] != raw[start:end]
                    or finding['severity'] not in ('low', 'medium', 'high')
                    or finding['category'] not in CATEGORIES
                    or any(not isinstance(finding[field], str) or not finding[field].strip()
                           for field in ('rationale', 'author_action', 'author_question'))
                    or not isinstance(finding['disposition'], str)
                    or not isinstance(finding['reviewer'], str)
                    or finding['segment_ids'] != [s['segment_id'] for s in segments
                                                 if s['start'] < end and s['end'] > start]):
                raise ValueError()
            ids.add(finding['finding_id'])
    except (KeyError, TypeError, ValueError):
        raise ChapterError('Chapters require a complete review report bound to the exact raw transcript.') from None
    return segments


def _unresolved(finding):
    # A label without a reviewer does not establish a human review decision.
    return (finding['disposition'].strip().casefold() not in RESOLVED_DISPOSITIONS
            or not finding['reviewer'].strip())


def _units(raw, segments, max_bytes):
    """Bound long lines as well as long interviews; every character belongs once."""
    index = 0
    for segment in segments:
        cursor = segment['start']
        for text in split_text(segment['text'], max_bytes):
            index += 1
            end = cursor + len(text)
            yield {'unit_id': f'unit-{index:06d}', 'segment_id': segment['segment_id'],
                   'start': cursor, 'end': end, 'text': raw[cursor:end]}
            cursor = end


def _chunks(units, max_bytes):
    current, size = [], 0
    for unit in units:
        width = len(unit['text'].encode('utf-8'))
        if current and size + width > max_bytes:
            yield current
            current, size = [], 0
        current.append(unit)
        size += width
    if current:
        yield current


def _schema(index, units, person):
    unit_array = {'type': 'array', 'items': {'type': 'string',
                  'enum': [unit['unit_id'] for unit in units]}, 'minItems': 1}
    passage = {'type': 'object', 'additionalProperties': False,
               'properties': {'unit_ids': unit_array, 'text': {'type': 'string'},
                              'kind': {'type': 'string', 'enum':
                                       (['verbatim_excerpt'] if person == 'third' else
                                        ['verbatim_excerpt', 'source_preserving'])}},
               'required': ['unit_ids', 'text', 'kind']}
    omission = {'type': 'object', 'additionalProperties': False,
                'properties': {'unit_ids': unit_array,
                               'reason': {'type': 'string', 'enum': list(OMISSION_REASONS)}},
                'required': ['unit_ids', 'reason']}
    return {'type': 'json_schema', 'json_schema': {'name': 'source_bound_chapter', 'strict': True,
            'schema': {'type': 'object', 'additionalProperties': False,
                       'properties': {'chunk_index': {'type': 'integer', 'enum': [index]},
                                      'passages': {'type': 'array', 'items': passage},
                                      'coverage_omissions': {'type': 'array', 'items': omission}},
                       'required': ['chunk_index', 'passages', 'coverage_omissions']}}}


def _refs(units, findings):
    start, end = units[0]['start'], units[-1]['end']
    return {'start': start, 'end': end,
            'segment_ids': list(dict.fromkeys(unit['segment_id'] for unit in units)),
            'finding_ids': [finding['finding_id'] for finding in findings
                            if finding['start'] < end and finding['end'] > start]}


def _quotation_signature(text):
    """Anchor existing quotation marks to source words; never create dialogue."""
    signature, pending = [], []
    for character in text:
        if character in QUOTATION_MARKS:
            signature.extend(words(''.join(pending)))
            pending = []
            signature.append(('quotation_mark', character))
        else:
            pending.append(character)
    signature.extend(words(''.join(pending)))
    return signature


def _quote_span(source, start):
    """Exact excerpt offsets sit inside the full span used for coverage."""
    excerpt = source.strip()
    quote_start = start + len(source) - len(source.lstrip()) if excerpt else start
    return {'quote_start': quote_start, 'quote_end': quote_start + len(excerpt)}


def _validate_response(content, chunk, index, person, findings):
    def unique_object(pairs):
        value = {}
        for name, item in pairs:
            if name in value:
                raise ValueError()
            value[name] = item
        return value

    try:
        response = json.loads(content, object_pairs_hook=unique_object)
        if (not isinstance(response, dict)
                or set(response) != {'chunk_index', 'passages', 'coverage_omissions'}
                or type(response['chunk_index']) is not int or response['chunk_index'] != index
                or not isinstance(response['passages'], list)
                or not isinstance(response['coverage_omissions'], list)):
            raise ValueError()
        by_id = {unit['unit_id']: unit for unit in chunk}
        positions = {unit['unit_id']: position for position, unit in enumerate(chunk)}
        covered, passages, omissions = [], [], []
        for kind, collection in (('passage', response['passages']),
                                 ('omission', response['coverage_omissions'])):
            last_position = -1
            for item in collection:
                expected = {'unit_ids', 'text', 'kind'} if kind == 'passage' else {'unit_ids', 'reason'}
                if not isinstance(item, dict) or set(item) != expected:
                    raise ValueError()
                ids = item['unit_ids']
                if (not isinstance(ids, list) or not ids
                        or any(not isinstance(unit_id, str) or unit_id not in by_id
                               for unit_id in ids)):
                    raise ValueError()
                indexes = [positions[unit_id] for unit_id in ids]
                if (indexes != list(range(indexes[0], indexes[0] + len(indexes)))
                        or indexes[0] <= last_position):
                    raise ValueError()
                last_position = indexes[-1]
                covered.extend(ids)
                cited = [by_id[unit_id] for unit_id in ids]
                reference = _refs(cited, findings)
                if kind == 'passage':
                    source = ''.join(unit['text'] for unit in cited)
                    text = item['text']
                    if not isinstance(text, str) or (source.strip() and not text.strip()):
                        raise ValueError()
                    if item['kind'] == 'verbatim_excerpt':
                        if text != source.strip():
                            raise ValueError()
                        reference.update(_quote_span(source, reference['start']))
                    elif (item['kind'] != 'source_preserving' or person == 'third'
                          or words(text) != words(source)
                          or _quotation_signature(text) != _quotation_signature(source)):
                        raise ValueError()
                    # Whitespace-only units count as coverage; do not invent their content.
                    if not source.strip() and text.strip():
                        raise ValueError()
                    passages.append({'kind': item['kind'], 'text': text, **reference})
                else:
                    if item['reason'] not in OMISSION_REASONS:
                        raise ValueError()
                    omissions.append({'reason': item['reason'], **reference})
        if len(covered) != len(chunk) or set(covered) != set(by_id):
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise ChapterError('Chapter response violated source coverage, provenance, or wording constraints; no draft was saved.') from None
    return passages, omissions


def _narrative(raw, segments, findings, client, options):
    passages, omissions = [], []
    for index, chunk in enumerate(_chunks(_units(raw, segments, options.chunk_bytes),
                                         options.chunk_bytes), start=1):
        emit_progress('chapters', 'running', chunk=index)
        try:
            # Match the existing client path; never expose provider payloads in exceptions.
            _suppress_provider_logging()
            response = client.chat.completions.create(
                model=options.model,
                messages=[{'role': 'system', 'content': _prompt('narrative')},
                          {'role': 'user', 'content': json.dumps(
                              {'chunk_index': index, 'person': options.person,
                               'source_units': chunk}, ensure_ascii=False)}],
                response_format=_schema(index, chunk, options.person),
                extra_body={'reasoning_effort': options.reasoning_effort,
                            'max_completion_tokens': min(32768, max(16384,
                                sum(len(u['text'].encode()) for u in chunk) * 3 + 8192)),
                            'store': False})
        except Exception as error:
            emit_progress('chapters', 'failed', chunk=index, **classify(error))
            raise ChapterError('Chapter generation failed; check API/model access, quota, and connectivity. Raw transcript and review report are retained.') from None
        try:
            invalid_count = not isinstance(response.choices, list) or len(response.choices) != 1
            if not invalid_count:
                choice = response.choices[0]
                incomplete = choice.finish_reason != 'stop' or bool(getattr(choice.message, 'refusal', None))
                content = choice.message.content
        except Exception:
            emit_progress('chapters', 'failed', chunk=index, error_category='completion')
            raise ChapterError('Chapter provider response could not be read; no draft was saved.') from None
        if invalid_count:
            emit_progress('chapters', 'failed', chunk=index, error_category='completion')
            raise ChapterError('Chapter provider response has an invalid completion count; no draft was saved.')
        if incomplete:
            emit_progress('chapters', 'failed', chunk=index, error_category='completion')
            raise ChapterError('Chapter output was incomplete or refused; raw transcript and review report are retained.')
        try:
            parts, missing = _validate_response(content, chunk, index, options.person, findings)
        except ChapterError:
            emit_progress('chapters', 'failed', chunk=index, error_category='validation')
            raise
        emit_progress('chapters', 'complete', chunk=index)
        passages.extend(parts)
        omissions.extend(missing)
    return passages, omissions


def draft_chapters(raw: str, report: dict, client, options=None, *, allow_unresolved_high=False):
    """Draft selected styles only after complete review; never mutate source/report."""
    options = options or ChapterOptions()
    if not isinstance(raw, str) or not raw.strip():
        raise ChapterError('Chapter input must be nonempty raw transcript text.')
    if type(allow_unresolved_high) is not bool:
        raise ChapterError('The unresolved-high override must be explicitly true or false.')
    segments = _validate_report(raw, report)
    findings = report['findings']
    unresolved = [finding['finding_id'] for finding in findings if _unresolved(finding)]
    high = [finding['finding_id'] for finding in findings
            if finding['severity'] == 'high' and _unresolved(finding)]
    if high and not allow_unresolved_high:
        raise ChapterError('Chapter drafting is blocked by unresolved high-priority findings; review them or explicitly enable the draft override.')
    warnings = ['HUMAN REVIEW REQUIRED — automatic source transcription needs a recording check.',
                'These are inspectable editorial drafts, not publication-ready chapters.',
                'Review flags are questions for human judgment, not evidence or proof.']
    if unresolved:
        warnings.append('Unresolved review findings: ' + ', '.join(unresolved))
    if high:
        warnings.append('DRAFT OVERRIDE: unresolved HIGH-priority findings: ' + ', '.join(high))
    chapters = {}
    for style in options.styles:
        if style == 'interview':
            passages = [{'kind': 'verbatim_excerpt', 'text': unit['text'].strip(),
                         **_quote_span(unit['text'], unit['start']),
                         **_refs([unit], findings)}
                        for unit in _units(raw, segments, options.chunk_bytes)]
            omissions = []
        else:
            passages, omissions = _narrative(raw, segments, findings, client, options)
        for index, passage in enumerate(passages, start=1):
            passage['passage_id'] = f'{style}-{index:06d}'
            passage['attribution'] = 'Unassigned source testimony; verify speaker with recording.'
        chapters[style] = {
            'style': style, 'person': options.person if style == 'narrative' else 'source',
            'passages': passages, 'coverage_omissions': omissions,
            'editorial_changes': ([
                'Source units arranged in original order; no interviewer questions generated.'
            ] if style == 'interview' else [
                'Adjacent source units may be grouped; source words and symbols stay in order.',
                'Source-preserving prose may change punctuation, capitalization, and paragraph layout.',
                'Third-person mode frames exact testimony; pronouns and narrator identity are not rewritten.'
            ]),
            'uncertainties': ['Speaker attribution and automatic transcription require recording verification.',
                              'Wording checks cannot establish truth, tone, meaning, or publication suitability.',
                              *[f'Unresolved author-review finding {finding["finding_id"]}: '
                                f'{finding["category"]}; raw characters {finding["start"]}:{finding["end"]}.'
                                for finding in findings if _unresolved(finding)]],
            'coverage': {'complete': not omissions, 'accounted_characters': len(raw),
                         'included_characters': sum(p['end'] - p['start'] for p in passages),
                         'omitted_characters': sum(o['end'] - o['start'] for o in omissions)},
            'warnings': list(warnings),
            'generation': 'deterministic_source_excerpts' if style == 'interview' else 'structured_model_arrangement',
        }
        if omissions:
            chapters[style]['warnings'].append('Explicit coverage omissions require author review; inspect their source spans and flags.')
    return {'schema_version': CHAPTER_SCHEMA_VERSION, 'prompt_version': PROMPT_VERSION,
            'prompt_sha256': {style: hashlib.sha256(_prompt(style).encode('utf-8')).hexdigest()
                              for style in options.styles},
            'status': 'complete', 'raw_sha256': hashlib.sha256(raw.encode()).hexdigest(),
            'review_sha256': _digest(report), 'settings_fingerprint': options.fingerprint,
            'model': options.model, 'human_review_required': True,
            'allow_unresolved_high': allow_unresolved_high,
            'unresolved_finding_ids': unresolved, 'unresolved_high_finding_ids': high,
            'findings': [dict(finding) for finding in findings], 'chapters': chapters}


def render_chapter(document, style):
    """Plain UTF-8 review artifact with every passage's trace and unresolved flags."""
    try:
        chapter = document['chapters'][style]
    except (KeyError, TypeError):
        raise ChapterError('Requested chapter style is unavailable.') from None
    lines = [f'{style.upper()} CHAPTER DRAFT — HUMAN REVIEW REQUIRED',
             f'Raw source SHA-256: {document["raw_sha256"]}',
             f'Review report canonical-JSON SHA-256: {document["review_sha256"]}', '']
    lines.extend(chapter['warnings'])
    lines.append('')
    if style == 'interview':
        attribution = document.get('recording_provenance', {}).get('attribution', {})
        banner = ('Testimony excerpts in source order. Names and roles appear only for user-confirmed mappings; '
                  'unmapped speakers remain unidentified.' if attribution.get('contract') in (2, 3)
                  else 'Testimony excerpts in source order. Speaker roles are unassigned.')
        lines.extend([banner,
                      'No interviewer questions have been generated.', ''])
    else:
        lines.extend(['Conservative narrative arrangement in original source voice.',
                      'This version groups source testimony; it does not rewrite a story.', ''])
    for passage in chapter['passages']:
        lines.append(f'[{passage["passage_id"]}; raw characters {passage["start"]}:{passage["end"]}; '
                     f'segments {", ".join(passage["segment_ids"])}]')
        for reference in passage.get('recording_refs', []):
            if reference['kind'] == 'recording':
                lines.append(f'Recording part {reference["order"]} ({reference["part_id"]}); '
                             f'local text characters {reference["local_start"]}:{reference["local_end"]}; '
                             f'local segments {", ".join(reference["local_segment_ids"])}.')
            else:
                lines.append('Explicit recording separator; no recording time or content inferred.')
            for turn in reference.get('speaker_turns', []):
                lines.append(f'Speaker {turn["speaker_key"]}: {turn["display_name"]}; '
                             f'identity evidence: {turn["identity_evidence"]}; '
                             f'diarization: {turn["diarization_evidence"]}; '
                             f'part audio seconds {turn["audio_start"]}:{turn["audio_end"]}; '
                             f'overlap detected: {turn["overlap_detected"]}.')
        lines.append('Review flags: ' + (', '.join(passage['finding_ids']) or 'None linked; not a publication clearance.'))
        if style == 'narrative' and chapter['person'] == 'third':
            lines.append('The source testimony states (speaker identity requires verification):')
        if passage['kind'] == 'verbatim_excerpt':
            lines.append(f'Exact automatic-source excerpt, raw characters '
                         f'{passage["quote_start"]}:{passage["quote_end"]} '
                         '(verify against the recording):')
        else:
            lines.append('Source-preserving prose; punctuation/layout adapted, not an exact quotation:')
        lines.extend([passage['text'], ''])
    lines.append('COVERAGE OMISSIONS')
    if not chapter['coverage_omissions']:
        lines.append('None. All raw characters accounted for in included source spans.')
    for omission in chapter['coverage_omissions']:
        lines.append(f'Raw characters {omission["start"]}:{omission["end"]}; '
                     f'segments {", ".join(omission["segment_ids"])}; '
                     f'reason {omission["reason"]}; review flags '
                     f'{", ".join(omission["finding_ids"]) or "none linked"}.')
    lines.extend(['', 'UNCERTAINTIES', *chapter['uncertainties'], '',
                  'EDITORIAL CHANGES', *chapter['editorial_changes'], '', 'AUTHOR REVIEW REGISTER'])
    if not document['findings']:
        lines.append('No model findings in the complete report; human review is still required.')
    for finding in document['findings']:
        lines.extend([f'{finding["finding_id"]} [{finding["severity"].upper()}] '
                      f'raw {finding["start"]}:{finding["end"]}; '
                      f'disposition: {finding["disposition"] or "Unreviewed"}',
                      finding['excerpt'], ''])
    return '\n'.join(lines) + '\n'
