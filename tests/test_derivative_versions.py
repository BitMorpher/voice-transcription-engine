"""Synthetic configuration changes retain exact source-bound text artifacts."""

import json
import hashlib
import pytest

from src.author_review import ReviewOptions
from src.author_workflow import AuthorOptions
from src.derivative_versions import enhancement_path
from src.interview_attribution import AttributedInterview, InterviewOptions, input_record
from src.model_config import EditingOptions
from src.model_config import _fingerprint
from src.private_output import digest, write_private
from src.pipeline import Pipeline, PipelineError
from src.transcriber import Transcriber
from test_ordered_interview import interview_provider as _ordered_provider, manifest, workflow
from test_interview_attribution import interview_provider as _attributed_provider


@pytest.fixture
def interview_provider(provider):
    return _ordered_provider.__wrapped__(provider)


@pytest.fixture
def attributed_provider():
    return _attributed_provider.__wrapped__()


def test_original_review_change_reuses_exact_polish(synthetic_media, tmp_path, interview_provider):
    source = manifest(tmp_path, [synthetic_media('synthetic.wav')])
    initial = workflow(source, tmp_path / 'output', enhance=True, review=True)
    initial.process(transcriber=Transcriber(client=interview_provider))
    raw, provenance = initial._combine()
    configuration = Pipeline(initial.output)._enhancement_fingerprint(provenance['raw_sha256'])
    # Exercise the read-only reuse verifier directly, before the process wrapper.
    assert initial._reusable_polish(raw, provenance, configuration) is None
    before = (initial.job / 'derivative_readability.txt').read_bytes()
    editing_calls = sum(call.kwargs['response_format']['json_schema']['name'] == 'faithful_transcript_edit'
                        for call in interview_provider.chat.completions.create.call_args_list)
    review = ReviewOptions(model='gpt-6.1-sol', reasoning_effort='medium')
    revised = workflow(source, initial.output, enhance=True, review=True, resume=True,
                       review_options=review)
    assert revised.raw_identity == initial.raw_identity and revised.job != initial.job
    assert revised._reusable_polish(raw, provenance, configuration)[0].encode() == before
    _, summary = revised.process(transcriber=Transcriber(client=interview_provider))
    assert summary['enhancement'] == 'skipped'
    assert (revised.job / 'derivative_readability.txt').read_bytes() == before
    assert (initial.job / 'derivative_readability.txt').read_bytes() == before
    assert interview_provider.audio.transcriptions.create.call_count == 1
    assert sum(call.kwargs['response_format']['json_schema']['name'] == 'faithful_transcript_edit'
               for call in interview_provider.chat.completions.create.call_args_list) == editing_calls


def test_direct_editing_versions_coexist_and_reselect(synthetic_media, tmp_path, provider):
    source, output = synthetic_media('synthetic.wav'), tmp_path / 'output'
    first_editor, second_editor = EditingOptions(), EditingOptions(model='gpt-6.1-sol', reasoning_effort='low')
    first = Transcriber(client=provider, editing_options=first_editor)
    identity, _ = Pipeline(output).process(source, transcriber=first, enhance=True)
    job = output / identity
    before = (job / 'derivative_readability.txt').read_bytes()
    second = Transcriber(client=provider, editing_options=second_editor)
    _, summary = Pipeline(output, resume=True, editing_options=second_editor).process(
        source, transcriber=second, enhance=True)
    state = json.loads((job / 'manifest.json').read_bytes())
    second_path = enhancement_path(job, state['stages']['enhancement'])
    assert second_path != job / 'derivative_readability.txt'
    assert second_path.is_file() and summary['enhancement'] == 'complete'
    assert len(state['derivative_versions']['enhancement']) == 2
    assert (job / 'derivative_readability.txt').read_bytes() == before
    _, summary = Pipeline(output, resume=True).process(source, transcriber=first, enhance=True)
    assert summary['enhancement'] == 'skipped'
    assert provider.audio.transcriptions.create.call_count == 1
    assert provider.chat.completions.create.call_count == 2


def test_changed_editing_refuses_corrupt_previous_output_before_requests(synthetic_media, tmp_path, provider):
    source, output = synthetic_media('synthetic.wav'), tmp_path / 'output'
    identity, _ = Pipeline(output).process(source, transcriber=Transcriber(client=provider), enhance=True)
    job = output / identity
    (job / 'derivative_readability.txt').write_bytes(b'changed')
    editor = EditingOptions(model='gpt-6.1-sol', reasoning_effort='low')
    with pytest.raises(PipelineError):
        Pipeline(output, resume=True, editing_options=editor).process(
            source, transcriber=Transcriber(client=provider, editing_options=editor), enhance=True)
    assert provider.audio.transcriptions.create.call_count == 1
    assert provider.chat.completions.create.call_count == 1


def test_attributed_text_versions_reuse_raw_and_reselect(synthetic_media, tmp_path, attributed_provider):
    source, output = synthetic_media('synthetic.wav'), tmp_path / 'output'
    first = Transcriber(client=attributed_provider)
    identity, _ = Pipeline(output).process(source, transcriber=first)
    job = output / identity
    original = json.loads((job / 'manifest.json').read_bytes())
    inputs = [input_record(1, job, original)]
    speaker = InterviewOptions('Synthetic Host', 'Synthetic Guest')
    options = AuthorOptions(review_options=ReviewOptions())
    family = AttributedInterview(output, inputs, speaker, resume=False, enhance=True, author_options=options)
    family.process(first)
    raw = (family.job / 'transcription.txt').read_bytes()
    old_polish = (family.job / 'derivative_readability.txt').read_bytes()
    second_editor = EditingOptions(model='gpt-6.1-sol', reasoning_effort='low')
    second = Transcriber(client=attributed_provider, editing_options=second_editor)
    second_options = AuthorOptions(review_options=ReviewOptions(model='gpt-6.1-sol', reasoning_effort='medium'))
    revised = AttributedInterview(output, inputs, speaker, resume=True, enhance=True, author_options=second_options)
    summary = revised.process(second)
    assert revised.job == family.job
    assert summary['attribution'] == 'skipped'
    state = json.loads((family.job / 'manifest.json').read_bytes())
    assert len(state['derivative_versions']['enhancement']) == 2
    assert len(state['derivative_versions']['author_review']) == 2
    assert enhancement_path(family.job, state['stages']['enhancement']) != family.job / 'derivative_readability.txt'
    assert (family.job / 'transcription.txt').read_bytes() == raw
    assert (family.job / 'derivative_readability.txt').read_bytes() == old_polish
    calls = attributed_provider.chat.completions.create.call_count
    family.resume = True
    summary = family.process(first)
    assert summary['enhancement'] == summary['author_review'] == 'skipped'
    assert attributed_provider.chat.completions.create.call_count == calls
    assert attributed_provider.audio.transcriptions.create.call_count == 2


@pytest.mark.parametrize('change_model', [False, True])
def test_head_legacy_multipart_polish_preserved_and_safely_selected(
        synthetic_media, tmp_path, attributed_provider, change_model):
    output = tmp_path / 'output'
    transcriber = Transcriber(client=attributed_provider)
    inputs = []
    for index in (1, 2):
        identity, _ = Pipeline(output).process(synthetic_media(f'synthetic-{index}.wav'), transcriber=transcriber)
        original_job = output / identity
        inputs.append(input_record(index, original_job, json.loads((original_job / 'manifest.json').read_bytes())))
    speaker = InterviewOptions('Synthetic Host', 'Synthetic Guest')
    family = AttributedInterview(output, inputs, speaker, resume=False, enhance=False, author_options=None)
    family.process(transcriber)
    raw = (family.job / 'transcription.txt').read_bytes().decode('utf-8')
    provenance = json.loads((family.job / 'provenance.json').read_bytes())
    from src.interview_attribution import NOTICE
    # Reproduce the actual pre-grouping writer, including its omitted part gap.
    legacy = NOTICE + '\n' + ''.join(raw[turn['start']:turn['speech_start']]
        + raw[turn['speech_start']:turn['speech_end']] + '\n\n'
        for turn in provenance['attribution']['turns'])
    old_path = family.job / 'derivative_readability.txt'
    write_private(old_path, legacy)
    state = json.loads((family.job / 'manifest.json').read_bytes())
    state['stages']['enhancement'] = {'status': 'complete', 'sha256': digest(old_path),
        'configuration_sha256': _fingerprint({'contract': 1, 'provenance': provenance,
                                              'editing': transcriber.editing_options.fingerprint})}
    (family.job / 'manifest.json').write_text(json.dumps(state))
    calls = attributed_provider.chat.completions.create.call_count
    if change_model:
        transcriber = Transcriber(client=attributed_provider,
            editing_options=EditingOptions(model='gpt-6.1-sol', reasoning_effort='low'))
    family.resume, family.enhance = True, True
    family.process(transcriber)
    state = json.loads((family.job / 'manifest.json').read_bytes())
    new_path = enhancement_path(family.job, state['stages']['enhancement'])
    assert new_path != old_path and old_path.read_bytes() == legacy.encode('utf-8')
    AttributedInterview._validate_polish(new_path.read_bytes().decode('utf-8'), raw, provenance)
    assert attributed_provider.chat.completions.create.call_count == calls + (1 if change_model else 0)
    assert len(state['derivative_versions']['enhancement']) == 2


@pytest.mark.parametrize('unknown_prefix', [False, True])
def test_matching_checksum_cannot_hide_readability_fidelity_change(
        synthetic_media, tmp_path, provider, unknown_prefix):
    source, output = synthetic_media('synthetic.wav'), tmp_path / 'output'
    transcriber = Transcriber(client=provider)
    identity, _ = Pipeline(output).process(source, transcriber=transcriber, enhance=True)
    job = output / identity
    target = job / 'derivative_readability.txt'
    payload = target.read_text().replace('Synthetic transcript.', 'Invented words.')
    if unknown_prefix:
        payload = 'Unknown derivative prefix.\n' + payload
    target.write_text(payload)
    state = json.loads((job / 'manifest.json').read_bytes())
    record = state['stages']['enhancement']
    record['sha256'] = digest(target)
    state['derivative_versions']['enhancement'][record['configuration_sha256']]['sha256'] = digest(target)
    (job / 'manifest.json').write_text(json.dumps(state))
    with pytest.raises(PipelineError):
        Pipeline(output, resume=True).process(source, transcriber=transcriber, enhance=True)
    assert provider.audio.transcriptions.create.call_count == provider.chat.completions.create.call_count == 1


@pytest.mark.parametrize('ordered', [False, True])
def test_true_head_original_polish_is_not_reused_after_prompt_change(
        synthetic_media, tmp_path, provider, interview_provider, monkeypatch, ordered):
    from src import transcriber as engine
    source, output = synthetic_media('synthetic.wav'), tmp_path / 'output'
    client = interview_provider if ordered else provider
    transcriber = Transcriber(client=client)
    if ordered:
        path = manifest(tmp_path, [source])
        first = workflow(path, output, enhance=True)
        first.process(transcriber=transcriber)
        job = first.job
    else:
        identity, _ = Pipeline(output).process(source, transcriber=transcriber, enhance=True)
        job = output / identity
    state = json.loads((job / 'manifest.json').read_bytes())
    # Keep only the fields shipped by the pre-optimization enhancement writer.
    state.pop('derivative_versions', None)
    state.pop('text_configuration', None)
    state.pop('raw_identity_sha256', None)
    state['stages']['enhancement'] = {key: state['stages']['enhancement'][key]
        for key in ('status', 'sha256', 'configuration_sha256', 'transcription_sha256')}
    (job / 'manifest.json').write_text(json.dumps(state))
    before = (job / 'derivative_readability.txt').read_bytes()
    fingerprint = hashlib.sha256(before).hexdigest()
    calls = client.chat.completions.create.call_count
    monkeypatch.setattr(engine, 'FAITHFUL_INSTRUCTION', engine.FAITHFUL_INSTRUCTION + ' Synthetic revision.')
    if ordered:
        revised = workflow(path, output, enhance=True, resume=True)
        revised.process(transcriber=transcriber)
    else:
        Pipeline(output, resume=True).process(source, transcriber=transcriber, enhance=True)
    assert client.chat.completions.create.call_count == calls + 1
    assert digest(job / 'derivative_readability.txt') == fingerprint
    assert client.audio.transcriptions.create.call_count == 1


def test_literal_next_turn_header_inside_speech_retains_all_source_words():
    from src.interview_attribution import NOTICE
    first_header, next_header = '[1:1:A | Synthetic A]\n', '[1:1:B | Synthetic B]\n'
    first_speech = 'I quoted the next label:\n\n' + next_header + 'Then continued.'
    next_speech = 'Answer.'
    first = first_header + first_speech + '\n\n'
    raw = first + next_header + next_speech + '\n\n'
    turns = [
        {'start': 0, 'speech_start': len(first_header), 'speech_end': len(first_header + first_speech)},
        {'start': len(first), 'speech_start': len(first + next_header),
         'speech_end': len(first + next_header + next_speech)},
    ]
    provenance = {'attribution': {'turns': turns}}
    payload = NOTICE + '\n' + raw
    AttributedInterview._validate_polish(payload, raw, provenance)
    assert AttributedInterview._restore_legacy_polish(payload, raw, provenance) == payload
