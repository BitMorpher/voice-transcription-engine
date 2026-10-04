import json
from unittest.mock import MagicMock

import pytest

from src.cli import main
from src.transcriber import Transcriber


def test_legacy_audio_arguments_and_names(monkeypatch, synthetic_media, tmp_path, provider, capsys):
    source = synthetic_media('synthetic.wav')
    synthetic_media('ignored.mp4')
    transcriber = Transcriber(client=provider)
    monkeypatch.setattr('src.cli.Transcriber', lambda **kwargs: transcriber)
    output = tmp_path / 'output'
    assert main(['--input_folder', str(source.parent), '--output_folder', str(output)]) == 0
    assert (output / 'synthetic_transcription.txt').read_text() == 'Synthetic transcript.'
    assert not (output / 'ignored_transcription.txt').exists()
    provider.chat.completions.create.assert_not_called()
    captured = capsys.readouterr()
    assert source.name not in captured.out
    assert str(tmp_path) not in captured.out
    assert 'Synthetic transcript.' not in captured.out
    assert captured.err == ''


def test_cli_full_pipeline(monkeypatch, synthetic_media, tmp_path, provider, capsys):
    source = synthetic_media()
    monkeypatch.setattr('src.cli.Transcriber', lambda **kwargs: Transcriber(client=provider))
    assert main(['--pipeline', '--input', str(source), '--output_folder', str(tmp_path / 'output')]) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert rows[0]['stages'] == {'conversion': 'complete', 'transcription': 'complete'}
    assert rows[-1]['failed'] == 0


def test_extract_only_never_initializes_provider(monkeypatch, synthetic_media, tmp_path):
    factory = MagicMock(side_effect=AssertionError('No provider allowed.'))
    monkeypatch.setattr('src.cli.Transcriber', factory)
    assert main(['--extract-only', '--input', str(synthetic_media()), '--output_folder', str(tmp_path / 'output')]) == 0
    factory.assert_not_called()


def test_missing_key_is_clear_nonsecret(synthetic_media, tmp_path, capsys):
    source = synthetic_media()
    assert main(['--pipeline', '--input', str(source), '--output_folder', str(tmp_path / 'output')]) == 1
    output = capsys.readouterr().out
    assert 'OPENAI_API_KEY' in output
    assert str(tmp_path) not in output and source.name not in output


def test_batch_continues_and_redacts_raw_errors(monkeypatch, tmp_path, capsys):
    inputs = tmp_path / 'input'
    inputs.mkdir()
    (inputs / 'personal-a.wav').touch()
    (inputs / 'personal-b.wav').touch()
    mock = MagicMock()
    mock.transcribe.side_effect = [RuntimeError('PRIVATE_PROVIDER_ERROR full/path key payload'), 'Synthetic safe text']
    monkeypatch.setattr('src.cli.Transcriber', lambda **kwargs: mock)
    monkeypatch.setattr('src.cli.require_ffmpeg', lambda: None)
    output = tmp_path / 'output'
    assert main(['--input_folder', str(inputs), '--output_folder', str(output)]) == 1
    captured = capsys.readouterr()
    assert 'PRIVATE_PROVIDER_ERROR' not in captured.out
    assert 'personal-' not in captured.out
    assert 'Synthetic safe text' not in captured.out
    assert str(tmp_path) not in captured.out
    assert captured.err == ''
    assert (output / 'personal-b_transcription.txt').exists()
    assert not (output / 'personal-a_transcription.txt').exists()


def test_preflight_os_error_does_not_leak(monkeypatch, tmp_path, capsys):
    source = tmp_path / 'private.wav'
    source.touch()
    def denied(*args):
        raise PermissionError('PRIVATE_PATH_ERROR')
    monkeypatch.setattr('src.cli.output_directory', denied)
    monkeypatch.setattr('src.cli.require_ffmpeg', lambda: None)
    assert main(['--extract-only', '--input', str(source)]) == 1
    assert 'PRIVATE_PATH_ERROR' not in capsys.readouterr().out


def test_legacy_never_overwrites(monkeypatch, tmp_path):
    inputs = tmp_path / 'input'
    inputs.mkdir()
    (inputs / 'same.wav').touch()
    output = tmp_path / 'output'
    output.mkdir()
    target = output / 'same_transcription.txt'
    target.write_text('Synthetic original.')
    transcriber = MagicMock()
    monkeypatch.setattr('src.cli.Transcriber', lambda **kwargs: transcriber)
    monkeypatch.setattr('src.cli.require_ffmpeg', lambda: None)
    assert main(['--input_folder', str(inputs), '--output_folder', str(output)]) == 1
    transcriber.transcribe.assert_not_called()
    assert target.read_text() == 'Synthetic original.'


@pytest.mark.parametrize('arguments', [
    ['--pipeline', '--input', 'unused', '--media-timeout', 'nan'],
    ['--pipeline', '--input', 'unused', '--media-timeout', '0'],
    ['--pipeline', '--input', 'unused', '--format_as_interview'],
    ['--extract-only', '--input', 'unused', '--enhance_for_reading'],
    ['--input', 'unused'],
    ['--input_folder', 'unused', '--resume'],
])
def test_invalid_arguments_fail_without_processing(arguments):
    with pytest.raises(SystemExit) as error:
        main(arguments)
    assert error.value.code == 2


def test_empty_folder_is_failure(tmp_path):
    assert main(['--extract-only', '--input', str(tmp_path)]) == 1


def test_model_and_private_hint_options_reach_api(monkeypatch, synthetic_media, tmp_path, provider, capsys):
    context = tmp_path / 'context.txt'
    glossary = tmp_path / 'glossary.txt'
    context.write_text('SYNTHETIC_CONTEXT_DO_NOT_LOG', encoding='utf-8')
    glossary.write_text('SYNTHETIC_TERM_DO_NOT_LOG\n', encoding='utf-8')
    monkeypatch.setattr('src.cli.Transcriber', lambda **kwargs: Transcriber(client=provider, **kwargs))
    assert main(['--pipeline', '--input', str(synthetic_media()), '--output-folder', str(tmp_path / 'output'),
                 '--model', 'gpt-transcribe', '--context-file', str(context), '--glossary-file', str(glossary),
                 '--language', 'en', '--language', 'fr', '--editing-model', 'gpt-6.1-sol', '--enhance-for-reading']) == 0
    request = provider.audio.transcriptions.create.call_args.kwargs
    assert request['prompt'] == 'SYNTHETIC_CONTEXT_DO_NOT_LOG'
    assert request['extra_body']['keywords'] == ['SYNTHETIC_TERM_DO_NOT_LOG']
    assert provider.chat.completions.create.call_args.kwargs['model'] == 'gpt-6.1-sol'
    captured = capsys.readouterr()
    assert 'DO_NOT_LOG' not in captured.out + captured.err
    assert str(tmp_path) not in captured.out + captured.err


def test_undocumented_model_is_sanitized_before_any_provider_call(tmp_path, capsys):
    assert main(['--pipeline', '--input', str(tmp_path), '--model', 'SYNTHETIC_INVALID_DO_NOT_LOG']) == 1
    assert 'DO_NOT_LOG' not in capsys.readouterr().out


def test_unrecognized_arguments_do_not_echo_private_values(capsys):
    with pytest.raises(SystemExit) as error:
        main(['--pipeline', '--input', 'unused', '--unknown', '/tmp/DO_NOT_LOG_SYNTHETIC_FILE.wav'])
    assert error.value.code == 2
    captured = capsys.readouterr()
    assert 'DO_NOT_LOG_SYNTHETIC_FILE' not in captured.out + captured.err


@pytest.mark.parametrize('option', [
    '--pipeline', '--extract-only', '--resume', '--enhance_for_reading',
    '--enhance-for-reading', '--format_as_interview', '--help',
])
def test_boolean_parser_errors_do_not_echo_private_values(option, capsys):
    value = '/tmp/DO_NOT_LOG_SYNTHETIC_VALUE.wav'
    with pytest.raises(SystemExit) as error:
        main(['--input', 'unused', f'{option}={value}'])
    assert error.value.code == 2
    output = capsys.readouterr()
    assert value not in output.out + output.err
    assert 'DO_NOT_LOG' not in output.out + output.err
    assert 'does not accept a value' in output.err
    assert option in output.err and '--help' in output.err


@pytest.mark.parametrize('arguments', [
    ['--pipeline', '--input', 'unused', '--media-timeout=DO_NOT_LOG_SYNTHETIC_VALUE'],
    ['--pipeline', '--input', 'unused', '--audio-chunk-seconds=DO_NOT_LOG_SYNTHETIC_VALUE'],
    ['--pipeline', '--input', 'DO_NOT_LOG_SYNTHETIC_VALUE', '--input-folder', 'unused'],
    ['--pipeline', '--input'],
    ['--pipeline', '--input', 'unused', '--in=DO_NOT_LOG_SYNTHETIC_VALUE'],
    [],
])
def test_other_parser_errors_keep_private_values_out(arguments, capsys):
    with pytest.raises(SystemExit) as error:
        main(arguments)
    assert error.value.code == 2
    captured = capsys.readouterr()
    assert 'DO_NOT_LOG' not in captured.out + captured.err
    assert '--help' in captured.err


def test_parser_usage_does_not_echo_caller_program_name(monkeypatch, capsys):
    monkeypatch.setattr('sys.argv', ['/tmp/DO_NOT_LOG_SYNTHETIC_PROGRAM.py'])
    with pytest.raises(SystemExit):
        main([])
    captured = capsys.readouterr()
    assert 'DO_NOT_LOG' not in captured.out + captured.err
    assert 'voice-transcribe' in captured.err


def test_command_mode_error_retains_fixed_guidance(capsys):
    with pytest.raises(SystemExit):
        main(['--input', 'DO_NOT_LOG_SYNTHETIC_VALUE'])
    captured = capsys.readouterr()
    assert 'DO_NOT_LOG' not in captured.out + captured.err
    assert '--input requires --pipeline or --extract-only' in captured.err
