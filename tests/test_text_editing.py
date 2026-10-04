import json

import pytest

from src.model_config import EditingOptions
from src.text_editing import split_text, validate_edit, words
from src.transcriber import Transcriber, TranscriptionError


def test_long_unicode_text_partition_is_exact():
    source = ('Synthetic café sentence. 日本語の合成文。\n' * 400) + '尾'
    chunks = list(split_text(source, 128))
    assert ''.join(chunks) == source
    assert all(len(chunk.encode('utf-8')) <= 128 for chunk in chunks)
    assert len(chunks) > 1


def test_long_editing_reassembles_all_chunks_in_order(provider):
    source = ''.join(f'Synthetic segment {index} retains every word.\n' for index in range(1000))
    edited = Transcriber(client=provider).enhance_transcription(source)
    assert edited.split('\n\n', 1)[1] == source
    assert 'Speaker attribution uncertain' in edited
    calls = provider.chat.completions.create.call_args_list
    assert len(calls) > 1
    chunks = [json.loads(call.kwargs['messages'][-1]['content']) for call in calls]
    assert ''.join(chunk['text'] for chunk in chunks) == source
    assert [chunk['chunk_index'] for chunk in chunks] == list(range(1, len(chunks) + 1))
    for call in calls:
        parameters = call.kwargs
        assert parameters['model'] == 'gpt-6-astra'
        assert parameters['extra_body']['reasoning_effort'] == 'high'
        assert parameters['extra_body']['max_completion_tokens'] > 2048
        assert parameters['extra_body']['store'] is False
        assert 'temperature' not in parameters and 'max_tokens' not in parameters
        assert parameters['response_format']['json_schema']['strict'] is True


def test_editor_is_configurable_without_changing_asr(provider):
    transcriber = Transcriber(client=provider, editing_options=EditingOptions(model='gpt-6.1-sol'))
    transcriber.enhance_transcription('Synthetic sentence.')
    assert provider.chat.completions.create.call_args.kwargs['model'] == 'gpt-6.1-sol'
    assert transcriber.model_name == 'gpt-transcribe'


@pytest.mark.parametrize('bad_text', [
    'Synthetic added question sentence.', 'Sentence synthetic.', 'Synthetic.',
    'Interviewer: Synthetic sentence.', 'Synthetic sentence. Interviewee: Extra answer.',
])
def test_unfaithful_words_are_rejected_even_when_finish_reason_is_stop(provider, bad_text):
    provider.chat.completions.create.return_value.choices[0].message.content = json.dumps({
        'chunk_index': 1, 'text': bad_text, 'speaker_uncertain': True,
    })
    with pytest.raises(TranscriptionError, match='invented, omitted, or reordered'):
        Transcriber(client=provider).enhance_transcription('Synthetic sentence.')


def test_only_punctuation_case_and_layout_are_accepted():
    result, uncertain = validate_edit(json.dumps({
        'chunk_index': 1, 'text': 'SYNTHETIC sentence!\nNext word.', 'speaker_uncertain': True,
    }), 'synthetic sentence next word', 1)
    assert words(result) == words('synthetic sentence next word')
    assert uncertain


@pytest.mark.parametrize('reason', ['length', 'content_filter', 'tool_calls'])
def test_incomplete_editing_is_never_returned(provider, reason):
    provider.chat.completions.create.return_value.choices[0].finish_reason = reason
    with pytest.raises(TranscriptionError, match='incomplete'):
        Transcriber(client=provider).enhance_transcription('Synthetic sentence.')


def test_refusal_and_malformed_or_wrong_chunk_outputs_fail(provider):
    message = provider.chat.completions.create.return_value.choices[0].message
    message.refusal = 'Synthetic refusal.'
    with pytest.raises(TranscriptionError, match='refused'):
        Transcriber(client=provider).enhance_transcription('Synthetic sentence.')
    message.refusal = None
    for content in ('not JSON', '{}', '{"chunk_index":2,"text":"Synthetic sentence.","speaker_uncertain":true}'):
        message.content = content
        with pytest.raises(TranscriptionError, match='malformed, or out of order'):
            Transcriber(client=provider).enhance_transcription('Synthetic sentence.')


def test_failed_later_edit_chunk_never_returns_partial(provider):
    default_edit = provider.chat.completions.create.side_effect
    def edit(**kwargs):
        if json.loads(kwargs['messages'][-1]['content'])['chunk_index'] == 2:
            raise RuntimeError('PRIVATE_PROVIDER_PAYLOAD')
        return default_edit(**kwargs)
    provider.chat.completions.create.side_effect = edit
    with pytest.raises(TranscriptionError) as error:
        Transcriber(client=provider, editing_options=EditingOptions(chunk_bytes=64)).enhance_transcription('Synthetic words. ' * 30)
    assert 'PRIVATE_PROVIDER_PAYLOAD' not in str(error.value)
    assert provider.chat.completions.create.call_count == 2


def test_empty_transcript_never_calls_editor(provider):
    assert Transcriber(client=provider).enhance_transcription(' \n') == ' \n'
    provider.chat.completions.create.assert_not_called()
