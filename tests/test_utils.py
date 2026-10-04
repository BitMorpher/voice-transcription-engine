import pytest

from src.private_output import OutputError, output_directory, write_private
from src.utils import format_transcription, validate_input_path, validate_output_path


def test_validate_input_directory(tmp_path):
    assert validate_input_path(str(tmp_path)) is None
    with pytest.raises(ValueError):
        validate_input_path(str(tmp_path / 'missing'))
    file = tmp_path / 'file'
    file.touch()
    with pytest.raises(ValueError):
        validate_input_path(str(file))


def test_output_creation_and_formatting(tmp_path):
    target = tmp_path / 'output'
    validate_output_path(str(target))
    assert target.is_dir()
    assert format_transcription('  Synthetic  text.\nNext. ') == 'Synthetic text. Next.'


def test_artifacts_disallowed_in_versioned_tree(tmp_path):
    (tmp_path / '.git').mkdir()
    with pytest.raises(OutputError, match='ignored private/'):
        output_directory(tmp_path / 'src' / 'output')
    assert output_directory(tmp_path / 'private' / 'output').is_dir()


def test_output_symlink_refused(tmp_path):
    real = tmp_path / 'real'
    real.mkdir()
    link = tmp_path / 'link'
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(OutputError, match='symlinks'):
        output_directory(link / 'output')


def test_private_writes_atomic_and_exclusive(tmp_path):
    target = tmp_path / 'synthetic.txt'
    write_private(target, 'Synthetic first.')
    assert target.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        write_private(target, 'Synthetic second.')
    assert target.read_text() == 'Synthetic first.'
    write_private(target, 'Synthetic manifest update.', replace=True)
    assert target.read_text() == 'Synthetic manifest update.'
    assert not list(tmp_path.glob('.write-*'))
