"""Unit tests for utils.py module."""
import os
import pytest
from src.utils import validate_input_path, validate_output_path, format_transcription


class TestValidateInputPath:
    """Tests for validate_input_path function."""
    
    def test_valid_directory(self, tmp_path):
        """Test with a valid directory path."""
        test_dir = tmp_path / "test_input"
        test_dir.mkdir()
        # Should not raise any exception
        validate_input_path(str(test_dir))
    
    def test_nonexistent_path(self):
        """Test with a path that doesn't exist."""
        with pytest.raises(ValueError, match="does not exist"):
            validate_input_path("/nonexistent/path")
    
    def test_file_instead_of_directory(self, tmp_path):
        """Test with a file path instead of directory."""
        test_file = tmp_path / "test_file.txt"
        test_file.write_text("test")
        with pytest.raises(ValueError, match="is not a directory"):
            validate_input_path(str(test_file))
    
    def test_empty_string(self):
        """Test with empty string."""
        with pytest.raises(ValueError):
            validate_input_path("")
    
    def test_relative_path(self, tmp_path):
        """Test with relative directory path."""
        test_dir = tmp_path / "relative_test"
        test_dir.mkdir()
        original_dir = os.getcwd()
        try:
            os.chdir(str(tmp_path))
            validate_input_path("relative_test")
        finally:
            os.chdir(original_dir)


class TestValidateOutputPath:
    """Tests for validate_output_path function."""
    
    def test_existing_directory(self, tmp_path):
        """Test with existing directory."""
        test_dir = tmp_path / "existing_output"
        test_dir.mkdir()
        validate_output_path(str(test_dir))
        assert test_dir.exists()
    
    def test_nonexistent_directory_creates_it(self, tmp_path):
        """Test that nonexistent directory is created."""
        test_dir = tmp_path / "new_output"
        assert not test_dir.exists()
        validate_output_path(str(test_dir))
        assert test_dir.exists()
        assert test_dir.is_dir()
    
    def test_nested_directory_creation(self, tmp_path):
        """Test creating nested directories."""
        test_dir = tmp_path / "level1" / "level2" / "output"
        assert not test_dir.exists()
        validate_output_path(str(test_dir))
        assert test_dir.exists()
        assert test_dir.is_dir()


class TestFormatTranscription:
    """Tests for format_transcription function."""
    
    def test_basic_formatting(self):
        """Test basic transcription formatting."""
        input_text = "  Hello world  "
        result = format_transcription(input_text)
        assert result == "Hello world"
    
    def test_newline_replacement(self):
        """Test that newlines are replaced with spaces."""
        input_text = "Line one\nLine two\nLine three"
        result = format_transcription(input_text)
        assert result == "Line one Line two Line three"
    
    def test_multiple_spaces_collapsed(self):
        """Test that double spaces are replaced with single space."""
        input_text = "Hello  world  test"
        result = format_transcription(input_text)
        # Note: The function only does one pass of '  ' -> ' ' replacement
        assert result == "Hello world test"
    
    def test_combined_formatting(self):
        """Test combining all formatting operations."""
        input_text = "  First line\n  Second line  \n\nThird  line  "
        result = format_transcription(input_text)
        # The function does: strip() -> replace('\n', ' ') -> replace('  ', ' ')
        # This leaves some double spaces because it's only one pass
        assert result == "First line  Second line  Third line"
    
    def test_empty_string(self):
        """Test with empty string."""
        result = format_transcription("")
        assert result == ""
    
    def test_whitespace_only(self):
        """Test with whitespace only string."""
        result = format_transcription("   \n  \n  ")
        assert result == ""
    
    def test_no_formatting_needed(self):
        """Test with text that needs no formatting."""
        input_text = "Already formatted text"
        result = format_transcription(input_text)
        assert result == "Already formatted text"
