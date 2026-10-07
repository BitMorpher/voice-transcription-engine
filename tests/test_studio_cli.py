"""The unified CLI must preserve dispatch, child gates, privacy, and exit statuses."""

from unittest.mock import MagicMock

import pytest

from src import studio_cli


@pytest.mark.parametrize('command,target', [('transcribe', 'cli'), ('batch', 'batch_cli')])
def test_dispatch_forwards_options_verbatim_and_returns_child_status(command, target, monkeypatch):
    arguments = ['--input_folder', 'SYNTHETIC_PRIVATE_PATH', '--progress=plain', '--resume']
    module = getattr(studio_cli, target)
    function = 'entrypoint' if target == 'cli' else 'main'
    child = MagicMock(return_value=130)
    monkeypatch.setattr(module, function, child)
    assert studio_cli.main([command, *arguments]) == 130
    child.assert_called_once_with(arguments, studio=True)


def test_no_arguments_shows_command_help(capsys):
    assert studio_cli.main([]) == 0
    output = capsys.readouterr()
    assert 'Interview Studio' in output.out and 'interview transcribe --help' in output.out
    assert 'voice-transcribe' in output.out and 'voice-batch' in output.out
    assert output.err == ''


@pytest.mark.parametrize('arguments,command', [
    (['--help'], 'interview'),
    (['transcribe', '--help'], 'interview transcribe'),
    (['batch', '--help'], 'interview batch'),
])
def test_help_reaches_the_right_parser(arguments, command, capsys):
    with pytest.raises(SystemExit) as error:
        studio_cli.main(arguments)
    assert error.value.code == 0
    output = capsys.readouterr()
    assert f'usage: {command} ' in output.out
    assert 'Interview Studio' in output.out
    assert output.err == ''


@pytest.mark.parametrize('arguments,command', [
    (['SYNTHETIC_PRIVATE_COMMAND'], 'interview'),
    (['--SYNTHETIC_PRIVATE_OPTION'], 'interview'),
    (['transcribe', '--input', 'SYNTHETIC_PRIVATE_PATH', '--progress=SYNTHETIC_PRIVATE_VALUE'], 'interview transcribe'),
    (['transcribe', '--pipeline', '--steps', 'raw', '--input', 'SYNTHETIC_PRIVATE_PATH'], 'interview transcribe'),
    (['batch', 'SYNTHETIC_PRIVATE_ACTION'], 'interview batch'),
    (['batch', 'run', '--request-timeout', 'SYNTHETIC_PRIVATE_VALUE'], 'interview batch'),
])
def test_invalid_commands_preserve_privacy_and_mode_gates(arguments, command, monkeypatch, capsys):
    monkeypatch.setattr(studio_cli.sys, 'argv', ['SYNTHETIC_PRIVATE_PROGRAM', *arguments])
    prohibited = MagicMock(side_effect=AssertionError('Must reject before processing.'))
    monkeypatch.setattr(studio_cli.cli, 'Transcriber', prohibited)
    monkeypatch.setattr(studio_cli.batch_cli, 'execute', prohibited)
    with pytest.raises(SystemExit) as error:
        studio_cli.main()
    assert error.value.code == 2
    prohibited.assert_not_called()
    output = capsys.readouterr()
    assert 'SYNTHETIC_PRIVATE_' not in output.out + output.err
    assert f'usage: {command} ' in output.err
    assert '--help' in output.err or 'Author options require --workflow.' in output.err
