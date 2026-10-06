"""Source-bound author review with validated coverage and privacy-safe failures.

Coverage records successful model acknowledgement of every core chunk, not a
guarantee that a model detected every issue. Human review remains necessary.
"""

import hashlib
import json
from dataclasses import asdict, dataclass
from importlib.resources import files

if __package__:
    from .provider_errors import classify, SYSTEMIC
    from .progress import emit_progress
    from . import prompts
    from .model_config import EDITING_MODELS
    from .text_editing import split_text
    from .transcriber import _suppress_provider_logging
else:
    from provider_errors import classify, SYSTEMIC
    from progress import emit_progress
    import prompts
    from model_config import EDITING_MODELS
    from text_editing import split_text
    from transcriber import _suppress_provider_logging


PROMPT_VERSION = 'author-review-v1'
REVIEW_CONTRACT = 1
CATEGORIES = ('criticism', 'allegation', 'sensitive_personal_information', 'disputed_fact',
              'uncertain_attribution', 'wording_ambiguity')
# Local prose prevents a model's explanatory text from inventing names or evidence.
REASONS = {
    'personal_opinion': ('criticism', 'low',
                        'Subjective opinion may benefit from clarity and fair context.',
                        'Clarify that this is a personal perspective and preserve relevant context.',
                        'Does this accurately express the speaker’s own perspective?'),
    'ambiguity_wording': ('wording_ambiguity', 'low',
                         'The wording or scope may be ambiguous.',
                         'Ask the speaker to clarify the intended wording or scope.',
                         'What did the speaker mean here, and should uncertainty remain explicit?'),
    'contextual_criticism': ('criticism', 'medium',
                            'Interpersonal or institutional criticism may require context.',
                            'Review context and attribution with the author without presuming the criticism false.',
                            'Is the personal experience and surrounding context represented fairly?'),
    'unverified_attribution': ('uncertain_attribution', 'medium',
                              'Responsibility, identity, or reported speech may be unverified.',
                              'Confirm attribution or retain a clear uncertainty marker.',
                              'Who is being referred to, and what supports this attribution?'),
    'disputed_fact': ('disputed_fact', 'medium',
                      'A factual statement may be uncertain, disputed, or require confirmation.',
                      'Seek source confirmation and preserve disputed or uncertain status.',
                      'What can the author verify, and which uncertainty must remain visible?'),
    'serious_allegation': ('allegation', 'high',
                          'A concrete serious allegation requires careful human review; this flag is not proof.',
                          'Review the source, attribution, context, and available corroboration before publication.',
                          'How should this allegation and its evidentiary status be represented responsibly?'),
    'sensitive_disclosure': ('sensitive_personal_information', 'high',
                             'A potentially sensitive personal disclosure needs deliberate human review.',
                             'Review consent, relevance, attribution, and the source with the author.',
                             'Is including this personal information appropriate and supported by informed consent?'),
    'strong_reputational_risk': ('allegation', 'high',
                                 'Concrete attribution may carry strong reputational risk; this flag is not proof.',
                                 'Review exact wording, attribution, context, and available corroboration.',
                                 'What context and verification does the author need to assess this passage fairly?'),
}


class ReviewError(RuntimeError):
    """Safe review error that never contains source text or provider payloads."""


def _hash(value):
    try:
        return hashlib.sha256(value.encode('utf-8')).hexdigest()
    except UnicodeError:
        raise ReviewError('Author-review text must be valid UTF-8.') from None


def _prompt():
    try:
        return files(prompts).joinpath('author_review_v1.txt').read_text(encoding='utf-8')
    except (OSError, UnicodeError):
        raise ReviewError('The packaged author-review prompt is unavailable.') from None


def review_schema(index):
    """Provider schema plus independent local validation; all properties required."""
    finding = {
        'type': 'object', 'additionalProperties': False,
        'properties': {
            'category': {'type': 'string', 'enum': list(CATEGORIES)},
            'severity': {'type': 'string', 'enum': ['low', 'medium', 'high']},
            'reason_code': {'type': 'string', 'enum': list(REASONS)},
            'excerpt': {'type': 'string'},
            'start': {'type': 'integer'}, 'end': {'type': 'integer'},
        },
        'required': ['category', 'severity', 'reason_code', 'excerpt', 'start', 'end'],
    }
    return {
        'type': 'json_schema',
        'json_schema': {
            'name': 'source_grounded_author_review', 'strict': True,
            'schema': {
                'type': 'object', 'additionalProperties': False,
                'properties': {
                    'chunk_index': {'type': 'integer', 'enum': [index]},
                    'fully_reviewed': {'type': 'boolean'},
                    'reviewed_start': {'type': 'integer'},
                    'reviewed_end': {'type': 'integer'},
                    'findings': {'type': 'array', 'items': finding},
                },
                'required': ['chunk_index', 'fully_reviewed', 'reviewed_start',
                             'reviewed_end', 'findings'],
            },
        },
    }


@dataclass(frozen=True)
class ReviewOptions:
    model: str = 'gpt-6-astra'
    chunk_bytes: int = 6000
    reasoning_effort: str = 'high'

    def __post_init__(self):
        if self.model not in EDITING_MODELS:
            raise ReviewError('Unsupported author-review model; use a documented editing model.')
        if type(self.chunk_bytes) is not int or not 64 <= self.chunk_bytes <= 6000:
            raise ReviewError('Author-review chunks must be between 64 and 6000 UTF-8 bytes.')
        if self.reasoning_effort not in ('low', 'medium', 'high'):
            raise ReviewError('Author-review reasoning effort must be low, medium, or high.')

    @property
    def fingerprint(self):
        return _hash(json.dumps({
            'contract': REVIEW_CONTRACT, 'prompt_version': PROMPT_VERSION,
            'prompt_sha256': _hash(_prompt()), 'schema': review_schema(1),
            'reasons': REASONS, 'segmentation': 'lines-v1', 'context': 'one-core-each-side-v1',
            **asdict(self),
        }, sort_keys=True, ensure_ascii=False))


def source_segments(raw):
    """Return stable line segments, preserving every source character exactly."""
    if not isinstance(raw, str) or not raw.strip():
        raise ReviewError('Author review requires nonempty transcript text.')
    segments, offset = [], 0
    for index, line in enumerate(raw.splitlines(keepends=True), start=1):
        segments.append({'segment_id': f'seg-{index:06d}', 'start': offset,
                         'end': offset + len(line), 'text': line})
        offset += len(line)
    return segments


def _chunks(raw, limit):
    """Core partitions cover all text once; neighbor cores provide boundary context.

    Each request has at most three byte-bounded cores. No character is discarded,
    including Unicode join controls, whitespace, and a final partial core.
    """
    cores, offset = [], 0
    for text in split_text(raw, limit):
        cores.append((offset, offset + len(text)))
        offset += len(text)
    for index, (start, end) in enumerate(cores):
        yield {'chunk_index': index + 1, 'start': start, 'end': end,
               'context_start': cores[max(0, index - 1)][0],
               'context_end': cores[min(len(cores) - 1, index + 1)][1]}


def _validate(content, chunk, raw):
    """Reject invented excerpts, non-exact coverage, schema drift and truncation."""
    def unique_object(pairs):
        value = {}
        for name, item in pairs:
            if name in value:
                raise ValueError()
            value[name] = item
        return value

    try:
        result = json.loads(content, object_pairs_hook=unique_object)
        expected = {'chunk_index', 'fully_reviewed', 'reviewed_start', 'reviewed_end', 'findings'}
        context_start, context_end = chunk['context_start'], chunk['context_end']
        core_start, core_end = chunk['start'] - context_start, chunk['end'] - context_start
        if (not isinstance(result, dict) or set(result) != expected
                or type(result['chunk_index']) is not int
                or result['chunk_index'] != chunk['chunk_index']
                or type(result['fully_reviewed']) is not bool or not result['fully_reviewed']
                or type(result['reviewed_start']) is not int
                or type(result['reviewed_end']) is not int
                or result['reviewed_start'] != core_start or result['reviewed_end'] != core_end
                or not isinstance(result['findings'], list)):
            raise ValueError()
        validated = []
        for finding in result['findings']:
            if (not isinstance(finding, dict)
                    or set(finding) != {'category', 'severity', 'reason_code', 'excerpt', 'start', 'end'}
                    or not isinstance(finding['reason_code'], str)
                    or finding['reason_code'] not in REASONS
                    or type(finding['start']) is not int or type(finding['end']) is not int
                    or not isinstance(finding['excerpt'], str)):
                raise ValueError()
            category, severity, *_ = REASONS[finding['reason_code']]
            start, end = finding['start'], finding['end']
            if (finding['category'] != category or finding['severity'] != severity
                    or not 0 <= start < end <= context_end - context_start
                    or start >= core_end or end <= core_start
                    or not finding['excerpt'].strip()
                    or raw[context_start + start:context_start + end] != finding['excerpt']):
                raise ValueError()
            validated.append({**finding, 'start': context_start + start,
                              'end': context_start + end})
        return validated
    except (ValueError, TypeError, KeyError):
        raise ReviewError('Author-review response failed schema, coverage, or exact-source validation.') from None


def validate_review_report(raw, report, options=None):
    """Validate a complete cached report independently of mutable manifest hashes.

    Explicit options additionally bind exact chunk boundaries and settings.
    Reviewer/disposition values are records for human review, never evidence.
    """
    segments = source_segments(raw)
    source_hash = _hash(raw)
    try:
        if (not isinstance(report, dict) or report['status'] != 'complete'
                or report['raw_sha256'] != source_hash or report['segments'] != segments
                or type(report['schema_version']) is not int
                or report['schema_version'] != REVIEW_CONTRACT
                or report['human_review_required'] is not True
                or report['flags_are_proof'] is not False
                or report['prompt_version'] != PROMPT_VERSION
                or report['prompt_sha256'] != _hash(_prompt())
                or report['model'] not in EDITING_MODELS
                or not isinstance(report['findings'], list)):
            raise ValueError()
        coverage = report['coverage']
        if (coverage['complete'] is not True
                or type(coverage['total_characters']) is not int
                or type(coverage['reviewed_characters']) is not int
                or coverage['total_characters'] != len(raw)
                or coverage['reviewed_characters'] != len(raw)
                or not isinstance(coverage['chunks'], list) or not coverage['chunks']):
            raise ValueError()
        cursor = 0
        for index, chunk in enumerate(coverage['chunks'], start=1):
            if (chunk['status'] != 'complete' or type(chunk['chunk_index']) is not int
                    or chunk['chunk_index'] != index
                    or any(type(chunk[field]) is not int for field in
                           ('start', 'end', 'context_start', 'context_end'))
                    or chunk['start'] != cursor
                    or not 0 <= chunk['context_start'] <= cursor < chunk['end']
                    <= chunk['context_end'] <= len(raw)):
                raise ValueError()
            cursor = chunk['end']
        if cursor != len(raw):
            raise ValueError()
        if options is not None:
            if (report['model'] != options.model
                    or report['settings_fingerprint'] != options.fingerprint
                    or coverage['chunks'] != [{**chunk, 'status': 'complete'}
                                               for chunk in _chunks(raw, options.chunk_bytes)]):
                raise ValueError()
        seen = set()
        for finding in report['findings']:
            start, end = finding['start'], finding['end']
            reason = finding['reason_code']
            if (type(start) is not int or type(end) is not int
                    or not 0 <= start < end <= len(raw)
                    or not isinstance(finding['excerpt'], str)
                    or not finding['excerpt'].strip() or raw[start:end] != finding['excerpt']
                    or not isinstance(reason, str) or reason not in REASONS):
                raise ValueError()
            category, severity, rationale, action, question = REASONS[reason]
            key = (start, end, category)
            if (key in seen or finding['category'] != category or finding['severity'] != severity
                    or finding['finding_id'] != 'finding-' + _hash(json.dumps([source_hash, *key]))[:24]
                    or finding['rationale'] != rationale or finding['author_action'] != action
                    or finding['author_question'] != question
                    or finding['segment_ids'] != [s['segment_id'] for s in segments
                                                  if s['start'] < end and s['end'] > start]
                    or not isinstance(finding['disposition'], str)
                    or not isinstance(finding['reviewer'], str)):
                raise ValueError()
            seen.add(key)
    except (KeyError, ValueError, TypeError, AttributeError):
        raise ReviewError('Cached author review failed complete-source or configuration validation.') from None


def review_transcript(raw, client, options=None):
    """Review original text, retaining valid findings when later chunks fail.

    No raw or provider data is logged. A complete empty report means every core
    was acknowledged and validated, never that publication is safe or book ready.
    """
    options = options or ReviewOptions()
    segments = source_segments(raw)
    prompt = _prompt()
    raw_hash = _hash(raw)
    chunks, findings = [], {}
    requests = list(_chunks(raw, options.chunk_bytes))
    for chunk in requests:
        emit_progress('author_review', 'running', chunk=chunk['chunk_index'], chunks=len(requests))
        text = raw[chunk['context_start']:chunk['context_end']]
        payload = {'chunk_index': chunk['chunk_index'], 'text': text,
                   'core_start': chunk['start'] - chunk['context_start'],
                   'core_end': chunk['end'] - chunk['context_start']}
        try:
            _suppress_provider_logging()
            response = client.chat.completions.create(
                model=options.model,
                messages=[{'role': 'system', 'content': prompt},
                          {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}],
                response_format=review_schema(chunk['chunk_index']),
                extra_body={'reasoning_effort': options.reasoning_effort,
                            'max_completion_tokens': 32768, 'store': False},
            )
            if len(response.choices) != 1:
                raise ReviewError('Author-review returned an invalid completion.')
            choice = response.choices[0]
            if choice.finish_reason != 'stop' or getattr(choice.message, 'refusal', None):
                raise ReviewError('Author-review output was refused or incomplete.')
            validated = _validate(choice.message.content, chunk, raw)
            for finding in validated:
                key = (finding['start'], finding['end'], finding['category'])
                # Repeated overlap findings retain the higher priority without
                # treating different spans as interchangeable evidence.
                previous = findings.get(key)
                ranks = {'low': 0, 'medium': 1, 'high': 2}
                if previous is None or ranks[finding['severity']] > ranks[previous['severity']]:
                    findings[key] = finding
            chunks.append({**chunk, 'status': 'complete'})
        except ReviewError as error:
            category = 'completion' if str(error) in {
                'Author-review returned an invalid completion.',
                'Author-review output was refused or incomplete.'} else 'validation'
            # Never echo an arbitrary ReviewError raised by an injected client.
            chunks.append({**chunk, 'status': 'failed',
                           'error': 'Author-review output failed completion, schema, coverage, or exact-source validation.',
                           'error_category': category})
        except Exception as error:
            failure = classify(error)
            chunks.append({**chunk, 'status': 'failed',
                           'error': 'Author-review provider request failed; check access and retry.',
                           **failure})
        if chunks[-1].get('error_category') == 'not_attempted':
            chunks[-1]['attempted'] = False
        failure = {key: chunks[-1][key] for key in ('error_category', 'http_status') if key in chunks[-1]}
        emit_progress('author_review', chunks[-1]['status'], chunk=chunk['chunk_index'], chunks=len(requests), **failure)
        if failure.get('error_category') in SYSTEMIC | {'not_attempted'}:
            # Preserve full attempted/unattempted coverage without charging more
            # chunks for a definite global configuration/account failure.
            for remaining in requests[len(chunks):]:
                chunks.append({**remaining, 'status': 'failed', 'attempted': False,
                               'error_category': 'not_attempted',
                               'error': 'Not requested after a systemic provider failure.'})
                emit_progress('author_review', 'blocked', chunk=remaining['chunk_index'],
                              chunks=len(requests), error_category='not_attempted')
            break
    completed = sum(chunk['status'] == 'complete' for chunk in chunks)
    status = 'complete' if completed == len(chunks) else ('incomplete' if completed else 'failed')
    output_findings = []
    for key in sorted(findings):
        finding = findings[key]
        _, _, rationale, action, question = REASONS[finding['reason_code']]
        output_findings.append({
            'finding_id': 'finding-' + _hash(json.dumps([raw_hash, *key]))[:24],
            **finding,
            'segment_ids': [segment['segment_id'] for segment in segments
                            if segment['start'] < finding['end'] and segment['end'] > finding['start']],
            'rationale': rationale, 'author_action': action, 'author_question': question,
            'disposition': 'Unreviewed', 'reviewer': '',
        })
    return {
        'status': status, 'raw_sha256': raw_hash, 'model': options.model,
        'prompt_version': PROMPT_VERSION, 'prompt_sha256': _hash(prompt),
        'schema_version': REVIEW_CONTRACT, 'settings_fingerprint': options.fingerprint,
        'offset_unit': 'Unicode characters; zero-based; end exclusive',
        'human_review_required': True, 'flags_are_proof': False,
        'segments': segments, 'findings': output_findings,
        'coverage': {'complete': status == 'complete', 'total_characters': len(raw),
                     'reviewed_characters': sum(chunk['end'] - chunk['start'] for chunk in chunks
                                                if chunk['status'] == 'complete'),
                     'chunks': chunks},
    }
