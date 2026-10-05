"""Additive, source-bound diarization and explicitly confirmed speaker names.

Provider labels are local to an API request. A name is never inferred from a
question, a turn's position, or another request's identically spelled label.
"""

import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import unicodedata
import wave
from dataclasses import dataclass
from pathlib import Path

if __package__:
    from .author_review import source_segments
    from .author_workflow import AuthorWorkflowError, run_author_stages, _verified_bundle, _load_bound_report
    from .model_config import ModelConfigurationError, _fingerprint
    from .private_output import digest, output_directory, write_private
    from .progress import emit_progress, CURRENT
    from .source_provenance import author_binding, validate_chapter_binding
    from .provider_errors import classify
    from .text_editing import words
    from .transcriber import TranscriptionError, _suppress_provider_logging
else:
    from author_review import source_segments
    from author_workflow import AuthorWorkflowError, run_author_stages, _verified_bundle, _load_bound_report
    from model_config import ModelConfigurationError, _fingerprint
    from private_output import digest, output_directory, write_private
    from progress import emit_progress, CURRENT
    from source_provenance import author_binding, validate_chapter_binding
    from provider_errors import classify
    from text_editing import words
    from transcriber import TranscriptionError, _suppress_provider_logging

DIARIZATION_MODEL = 'gpt-4o-transcribe-diarize'
NOTICE = ('ATTRIBUTED DERIVATIVE — HUMAN RECORDING REVIEW REQUIRED.\n'
          'Labels and timestamps are metadata, not spoken words. Diarization is automatic; '
          'names are user-confirmed mappings, never inferred roles. Unmapped voices remain '
          'unidentified. Request labels may reset; overlap and unclear speech need listening.\n')
SAFE_CACHE = 'Attributed cache/output is changed, unverified, or conflicts; use a new output folder. No artifact was overwritten.'


@dataclass(frozen=True)
class InterviewOptions:
    interviewer: str
    interviewee: str
    speaker_map: tuple[str, ...] = ()
    model: str = DIARIZATION_MODEL

    def __post_init__(self):
        for name in (self.interviewer, self.interviewee):
            if (not isinstance(name, str) or not name.strip() or name != name.strip()
                    or len(name.encode('utf-8')) > 256
                    or any(unicodedata.category(c).startswith('C') or c in '[]|\u2028\u2029' for c in name)):
                raise ModelConfigurationError('Interview mode requires two nonempty names, at most 256 UTF-8 bytes, without control or label-delimiter characters.')
        if self.interviewer.casefold() == self.interviewee.casefold():
            raise ModelConfigurationError('Use distinct interviewer and interviewee display names.')
        if self.model != DIARIZATION_MODEL:
            raise ModelConfigurationError('Interview diarization requires gpt-4o-transcribe-diarize and diarized_json; the original ASR model stays separate.')
        self.mappings()  # Validate without exposing supplied values.

    def mappings(self):
        result = {}
        if not isinstance(self.speaker_map, tuple):
            raise ModelConfigurationError('Speaker mappings must be repeated CLI options.')
        for entry in self.speaker_map:
            match = re.fullmatch(r'([1-9][0-9]*):([1-9][0-9]*):([A-Za-z0-9_-]{1,64})=(interviewer|interviewee)', entry) if isinstance(entry, str) else None
            if not match:
                raise ModelConfigurationError('Use --speaker-map PART:REQUEST:LABEL=interviewer or =interviewee after checking the recording; indices start at 1.')
            part, request, label, role = match.groups()
            key = (int(part), int(request), label)
            if key in result:
                raise ModelConfigurationError('Each scoped speaker may be mapped only once.')
            result[key] = role
        return result

    @property
    def fingerprint(self):
        return _fingerprint({'contract': 1, 'interviewer': self.interviewer,
                             'interviewee': self.interviewee, 'model': self.model,
                             'mapping': sorted(self.speaker_map)})


def diarization_configuration(transcriber, model=DIARIZATION_MODEL):
    # Original context/glossary are intentionally not sent to this model: it
    # supports no prompt. Names are local display metadata, not provider hints.
    return {'contract': 1, 'model': model, 'response_format': 'diarized_json',
            'chunking_strategy': 'auto', 'chunk_seconds': float(transcriber.options.chunk_seconds),
            'max_upload_bytes': transcriber.max_bytes,
            'language': transcriber.options.languages[0] if len(transcriber.options.languages) == 1 else None}


def _validated_response(body, duration):
    if (not isinstance(body, dict) or not isinstance(body.get('text'), str)
            or not isinstance(body.get('segments'), list)):
        raise ValueError()
    for segment in body['segments']:
        if (not isinstance(segment, dict) or not isinstance(segment.get('text'), str)
                or not isinstance(segment.get('speaker'), (str, type(None)))):
            raise ValueError()
        for key in ('start', 'end'):
            value = segment.get(key)
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError()
        if not 0 <= segment['start'] <= segment['end'] <= duration + 0.1:
            raise ValueError()
        label = segment.get('speaker')
        if label and not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', label):
            raise ValueError()
    if body['text'].strip() and not body['segments']:
        raise ValueError()  # Never silently turn a nonempty transcript into nothing.
    return body


def _complete_turn_text(body):
    """Accept full text with or without provider speaker prefixes, never omit words."""
    def compact(text):
        return ''.join(text.split()).casefold()
    plain = ''.join(segment['text'] for segment in body['segments'])
    labelled = ''.join(f'{segment.get("speaker") or "unknown"}: {segment["text"]}'
                       for segment in body['segments'])
    plain_words = words(' '.join(segment['text'] for segment in body['segments']))
    labelled_words = words(' '.join(f'{segment.get("speaker") or "unknown"}: {segment["text"]}'
                                  for segment in body['segments']))
    return (compact(body['text']) in {compact(plain), compact(labelled)}
            and words(body['text']) in (plain_words, labelled_words))


def diarize(transcriber, audio, configuration):
    """Keep full provider responses; fail the whole pass if any request fails."""
    requests = []
    try:
        for index, chunk in enumerate(transcriber._wave_chunks(audio), 1):
            emit_progress('diarization', 'running', chunk=index, chunks=chunk.total_chunks)
            try:
                parameters = {k: configuration[k] for k in ('model', 'response_format', 'chunking_strategy')}
                if configuration['language']:
                    parameters['language'] = configuration['language']
                _suppress_provider_logging()
                response = transcriber.client.audio.transcriptions.create(
                    file=('audio.wav', chunk, 'audio/wav'), **parameters)
                body = response.model_dump(mode='json') if hasattr(response, 'model_dump') else response
                _validated_response(body, chunk.duration_seconds)
                requests.append({'request': index, 'offset_seconds': chunk.offset_seconds,
                                 'duration_seconds': chunk.duration_seconds, 'response': body})
                emit_progress('diarization', 'complete', chunk=index, chunks=chunk.total_chunks)
            except Exception as error:
                emit_progress('diarization', 'failed', chunk=index, chunks=chunk.total_chunks,
                              **classify(error))
                raise TranscriptionError('Interview diarization failed; no complete attributed transcript was saved. Check model access, response validity, quota, and connectivity.') from None
            finally:
                chunk.close()
    except (OSError, EOFError):
        raise TranscriptionError('Prepared audio could not be read for diarization.') from None
    return {'version': 1, 'configuration': configuration, 'requests': requests}


def _read_json(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError()
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError()
            result[key] = value
        return result
    return json.loads(path.read_bytes().decode('utf-8'), object_pairs_hook=unique)


def _publish(directory, files):
    parent = output_directory(directory.parent)
    temporary = Path(tempfile.mkdtemp(prefix='.attributed-', dir=parent))
    try:
        for name, text in files.items():
            write_private(temporary / name, text)
        if os.path.lexists(directory):
            raise ValueError()
        os.rename(temporary, directory)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def _json(value):
    return json.dumps(value, ensure_ascii=False, indent=2) + '\n'


class AttributedInterview:
    """Independent family, using already verified original PCM and raw outputs.

    Inputs contain only engine-owned paths; all persistent paths are opaque.
    Diarization caches exclude names/mappings; families bind those separately.
    """
    def __init__(self, output, inputs, options, *, resume, enhance, author_options, progress=None):
        self.output = output_directory(Path(output) / 'attributed')
        self.inputs, self.options = inputs, options
        self.resume, self.enhance, self.author_options = resume, enhance, author_options
        self.progress = progress or (lambda stage, status: None)
        self.cache_snapshots = {}
        self.source_binding = _fingerprint([{**{k: part[k] for k in ('order', 'source_sha256', 'audio_sha256', 'original_raw_sha256')},
                                            'original_job_id': part.get('original_job_id', part['original_raw_sha256'])}
                                           for part in inputs])
        self.job = self.output / self.source_binding / options.fingerprint

    def _sources_unchanged(self):
        for part in self.inputs:
            for key, hash_key in (('audio', 'audio_sha256'), ('original_raw', 'original_raw_sha256')):
                path = Path(part[key])
                if path.is_symlink() or digest(path) != part[hash_key]:
                    raise ValueError()
        for path, expected in self.cache_snapshots.items():
            if path.is_symlink() or digest(path) != expected:
                raise ValueError()

    def _cache(self, part, transcriber, configuration):
        binding = _fingerprint({'source': part['source_sha256'], 'audio': part['audio_sha256'],
                                'configuration': configuration})
        cache = self.output / 'diarization-cache' / binding
        if os.path.lexists(cache):
            if cache.is_symlink():
                raise ValueError()
            state = _read_json(cache / 'manifest.json')
            if (cache / 'provider_responses.json').is_symlink():
                raise ValueError()
            if (state != {'version': 1, 'binding_sha256': binding,
                         'response_sha256': digest(cache / 'provider_responses.json')}):
                raise ValueError()
            payload = _read_json(cache / 'provider_responses.json')
            if payload['configuration'] != configuration or payload['version'] != 1:
                raise ValueError()
            if not isinstance(payload['requests'], list) or not payload['requests']:
                raise ValueError()
            offset = 0
            for index, request in enumerate(payload['requests'], 1):
                if (type(request['request']) is not int or request['request'] != index
                        or type(request['offset_seconds']) not in (int, float)
                        or not math.isclose(request['offset_seconds'], offset, abs_tol=1e-8)
                        or type(request['duration_seconds']) not in (int, float)
                        or not 0 < request['duration_seconds'] <= configuration['chunk_seconds']):
                    raise ValueError()
                _validated_response(request['response'], request['duration_seconds'])
                offset += request['duration_seconds']
            with wave.open(str(part['audio']), 'rb') as audio:
                if not math.isclose(offset, audio.getnframes() / audio.getframerate(), abs_tol=1e-8):
                    raise ValueError()
            self.progress('diarization', 'skipped')
            self.cache_snapshots[cache / 'provider_responses.json'] = state['response_sha256']
            return payload, binding
        self._sources_unchanged()
        payload = diarize(transcriber, part['audio'], configuration)
        self._sources_unchanged()
        text = _json(payload)
        _publish(cache, {'provider_responses.json': text, 'manifest.json': _json({
            'version': 1, 'binding_sha256': binding,
            'response_sha256': hashlib.sha256(text.encode()).hexdigest()})})
        self.cache_snapshots[cache / 'provider_responses.json'] = hashlib.sha256(text.encode()).hexdigest()
        return payload, binding

    def _assemble(self, payloads):
        mapping, used = self.options.mappings(), set()
        part_texts, turns, parts, spans, cursor = [], [], [], [], 0
        provider_text = []
        for part, payload, cache_binding in payloads:
            content, local_cursor = [], 0
            if part_texts:
                spans.append({'kind': 'separator', 'start': cursor, 'end': cursor + 2})
                cursor += 2
            start = cursor
            for request in payload['requests']:
                _validated_response(request['response'], request['duration_seconds'])
                if not _complete_turn_text(request['response']):
                    raise TranscriptionError('Diarization full text and speaker turns disagree; no attributed derivative was saved. Inspect retained provider responses and original raw text.')
                provider_text.append(request['response']['text'])
                segments = request['response']['segments']
                for index, segment in enumerate(segments, 1):
                    label = segment.get('speaker') or 'unknown'
                    key = (part['order'], request['request'], label)
                    role = mapping.get(key) if segment.get('speaker') else None
                    if role:
                        used.add(key)
                    evidence = 'user_confirmed_mapping' if role else 'unidentified'
                    name = getattr(self.options, role) if role else f'Speaker {label}'
                    scoped = f'{part["order"]}:{request["request"]}:{label}'
                    a = request['offset_seconds'] + segment['start']
                    b = request['offset_seconds'] + segment['end']
                    overlap = any(j != index - 1 and other['start'] < segment['end']
                                  and other['end'] > segment['start'] for j, other in enumerate(segments))
                    header = f'[{scoped} | {name} | {evidence} | part audio {a:.3f}:{b:.3f}]\n'
                    turn_start = cursor + local_cursor
                    content.append(header + segment['text'] + '\n\n')
                    local_cursor += len(content[-1])
                    turns.append({'turn_id': f'turn-{len(turns) + 1:06d}', 'part_order': part['order'],
                                  'request': request['request'], 'provider_segment_index': index,
                                  'provider_speaker': segment.get('speaker'), 'speaker_key': scoped,
                                  'display_name': name, 'role': role, 'identity_evidence': evidence,
                                  'diarization_evidence': 'provider_automatic_unverified',
                                  'start': turn_start, 'end': cursor + local_cursor,
                                  'speech_start': turn_start + len(header),
                                  'speech_end': turn_start + len(header) + len(segment['text']),
                                  'speech_sha256': hashlib.sha256(segment['text'].encode()).hexdigest(),
                                  'audio_start': a, 'audio_end': b, 'overlap_detected': overlap,
                                  'cache_binding_sha256': cache_binding})
                    turns[-1]['provider_response_sha256'] = _fingerprint(request['response'])
            raw_part = ''.join(content)
            if not any(request['response']['segments'] and any(s['text'].strip() for s in request['response']['segments'])
                       for request in payload['requests']):
                raise TranscriptionError('Interview diarization returned no usable turns; original output and provider responses are retained.')
            cursor += len(raw_part)
            part_texts.append(raw_part)
            part_id = f'part-{part["order"]:06d}'
            spans.append({'kind': 'recording', 'part_id': part_id, 'order': part['order'],
                          'start': start, 'end': cursor})
            parts.append({'id': part_id, 'order': part['order'], 'path': 'Local recording (opaque identity)',
                          'source_sha256': part['source_sha256'], 'raw_transcript': 'attributed/transcription.txt',
                          'raw_sha256': hashlib.sha256(raw_part.encode()).hexdigest(),
                          'original_raw_sha256': part['original_raw_sha256'],
                          'provider_payload_sha256': _fingerprint(payload),
                          'start': start, 'end': cursor,
                          'segments': [{k: v for k, v in s.items() if k != 'text'} for s in source_segments(raw_part)]})
        if used != set(mapping):
            raise ModelConfigurationError('A speaker mapping does not match a returned part/request/label; inspect provider responses and correct the mapping. Original outputs are retained.')
        raw = '\n\n'.join(part_texts)
        provenance = {'version': 1, 'interview_id': self.source_binding,
                      'manifest_sha256': self.options.fingerprint, 'human_review_required': True,
                      'offset_unit': 'Python Unicode characters; zero based; end exclusive',
                      'recording_time': 'Part-local audio seconds only; no global timeline across recordings.',
                      'separator': '\n\n', 'parts': parts, 'spans': spans,
                      'raw_sha256': hashlib.sha256(raw.encode()).hexdigest(),
                      'attribution': {'contract': 1, 'notice': NOTICE, 'model': self.options.model,
                                      'turns': turns, 'names_are_not_voice_evidence': True}}
        return raw, provenance, '\n\n'.join(provider_text)

    def _preflight_derivatives(self, state, transcriber):
        """Validate every selected completed stage before any provider call."""
        provenance = _read_json(self.job / 'provenance.json')
        raw = (self.job / 'transcription.txt').read_bytes().decode('utf-8')
        raw_hash = state['stages']['transcription']['sha256']
        if self.enhance:
            target = self.job / 'derivative_readability.txt'
            record = state['stages'].get('enhancement', {})
            if os.path.lexists(target) or record.get('status') == 'complete':
                config = _fingerprint({'contract': 1, 'provenance': provenance,
                                       'editing': transcriber.editing_options.fingerprint})
                if (target.is_symlink() or record.get('status') != 'complete'
                        or record.get('sha256') != digest(target)
                        or record.get('configuration_sha256') != config):
                    raise ValueError()
        if self.author_options is None:
            return
        review_hash = None
        for stage in ('author_review', 'chapters'):
            requested = (self.author_options.review if stage == 'author_review'
                         else self.author_options.chapter_options is not None)
            record = state['stages'].get(stage, {})
            if not requested or record.get('status') != 'complete':
                continue
            names = ({'review_report.json', 'review_report.xlsx'} if stage == 'author_review'
                     else {'chapter_drafts.json', *(f'chapter_{style}.txt'
                          for style in self.author_options.chapter_options.styles)})
            config = author_binding(self.author_options.fingerprint(stage, raw_hash, review_hash), provenance)
            if not _verified_bundle(self.job, record, config, raw_hash, names):
                raise ValueError()
            if stage == 'author_review':
                _, review_hash = _load_bound_report(self.job, record, raw,
                    self.author_options.review_options, provenance=provenance)
            else:
                path = next(self.job / name for name in record['artifacts'] if name.endswith('/chapter_drafts.json'))
                validate_chapter_binding(_read_json(path), provenance)

    def process(self, transcriber):
        summary = {}
        lock = self.output / '.attribution.lock'
        try:
            output_directory(self.job.parent)
            output_directory(self.output / 'diarization-cache')
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        except FileExistsError:
            raise TranscriptionError('Attributed output is locked; wait for the running process before retrying.') from None
        reporter = CURRENT.get()
        previous_context = reporter.context if reporter is not None else None
        if reporter is not None:
            reporter.context = {**previous_context, 'family': 'attributed'}
        try:
            self._sources_unchanged()
            configuration = diarization_configuration(transcriber, self.options.model)
            binding = _fingerprint({'source': self.source_binding, 'names': self.options.fingerprint,
                                    'diarization': configuration})
            # Entire family checked before making a diarization request.
            state = None
            if os.path.lexists(self.job):
                if not self.resume or self.job.is_symlink():
                    raise ValueError()
                state = _read_json(self.job / 'manifest.json')
                if state['version'] != 'attributed-interview-v1' or state['binding_sha256'] != binding:
                    raise ValueError()
                for name, expected in state['raw_artifacts'].items():
                    if name not in {'transcription.txt', 'provenance.json', 'diarization_transcription.txt', 'attribution_notice.txt'}:
                        raise ValueError()
                    if (self.job / name).is_symlink() or digest(self.job / name) != expected:
                        raise ValueError()
                if set(state['raw_artifacts']) != {'transcription.txt', 'provenance.json', 'diarization_transcription.txt', 'attribution_notice.txt'}:
                    raise ValueError()
                self._preflight_derivatives(state, transcriber)
            # Validate ALL existing caches before the first paid request.
            cache_root = self.output / 'diarization-cache'
            for part in self.inputs:
                cache_binding = _fingerprint({'source': part['source_sha256'], 'audio': part['audio_sha256'], 'configuration': configuration})
                cache = cache_root / cache_binding
                if state is not None and not os.path.lexists(cache):
                    raise ValueError()
                if os.path.lexists(cache):
                    self._cache(part, transcriber, configuration)
            payloads = [(part, *self._cache(part, transcriber, configuration)) for part in self.inputs]
            raw, provenance, provider_text = self._assemble(payloads)
            self._sources_unchanged()
            if state is None:
                files = {'transcription.txt': raw, 'provenance.json': _json(provenance),
                         'diarization_transcription.txt': provider_text, 'attribution_notice.txt': NOTICE}
                state = {'version': 'attributed-interview-v1', 'binding_sha256': binding,
                         'human_review_required': True, 'raw_artifacts': {
                             name: hashlib.sha256(text.encode()).hexdigest() for name, text in files.items()},
                         'stages': {'transcription': {'status': 'complete', 'sha256': provenance['raw_sha256']}}}
                _publish(self.job, {**files, 'manifest.json': _json(state)})
                summary['attribution'] = 'complete'
            else:
                if (_read_json(self.job / 'provenance.json') != provenance
                        or (self.job / 'transcription.txt').read_bytes() != raw.encode()
                        or (self.job / 'diarization_transcription.txt').read_bytes() != provider_text.encode()
                        or state['stages']['transcription']['sha256'] != provenance['raw_sha256']):
                    raise ValueError()
                summary['attribution'] = 'skipped'
            self.progress('attribution', summary['attribution'])
            def save():
                write_private(self.job / 'manifest.json', _json(state), replace=True)
            if self.enhance:
                config = _fingerprint({'contract': 1, 'provenance': provenance,
                                       'editing': transcriber.editing_options.fingerprint})
                target = self.job / 'derivative_readability.txt'
                record = state['stages'].get('enhancement', {})
                if os.path.lexists(target):
                    if (not self.resume or target.is_symlink() or record.get('sha256') != digest(target)
                            or record.get('configuration_sha256') != config):
                        raise ValueError()
                    summary['enhancement'] = 'skipped'
                else:
                    edited = []
                    # Edit speech alone, then restore exact labels and boundaries.
                    for turn in provenance['attribution']['turns']:
                        speech = raw[turn['speech_start']:turn['speech_end']]
                        result = transcriber.enhance_transcription(speech) if speech.strip() else speech
                        if words(result) != words(speech):
                            raise TranscriptionError('Attributed polishing changed source words; no derivative was saved.')
                        edited.append(raw[turn['start']:turn['speech_start']] + result + '\n\n')
                    self._sources_unchanged()
                    write_private(target, NOTICE + '\n' + ''.join(edited))
                    state['stages']['enhancement'] = {'status': 'complete', 'sha256': digest(target),
                                                     'configuration_sha256': config}
                    save()
                    summary['enhancement'] = 'complete'
                self.progress('enhancement', summary['enhancement'])
            if self.author_options is not None:
                run_author_stages(self.job, state, transcriber, self.author_options,
                                  resume=self.resume, save=save, summary=summary,
                                  progress=self.progress, provenance=provenance)
            self._sources_unchanged()
            return summary
        except ModelConfigurationError as error:
            raise TranscriptionError(str(error)) from None
        except (ValueError, KeyError, TypeError, OSError, AttributeError):
            raise TranscriptionError(SAFE_CACHE) from None
        except AuthorWorkflowError as error:
            raise TranscriptionError(str(error)) from None
        finally:
            lock.unlink()
            if reporter is not None:
                reporter.context = previous_context


def input_record(order, job, state):
    """Bind a prepared source to its verified original ASR output."""
    return {'order': order, 'source_sha256': state['source_sha256'],
            'original_job_id': job.name,
            'audio': job / 'audio.wav', 'audio_sha256': state['stages']['conversion']['sha256'],
            'original_raw': job / 'transcription.txt',
            'original_raw_sha256': state['stages']['transcription']['sha256']}
