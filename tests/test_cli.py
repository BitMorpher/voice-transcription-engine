"""Unit tests for cli.py module."""
import os
import pytest
from unittest.mock import MagicMock, patch, call
from src.cli import main


class TestCLIMain:
    """Tests for main CLI function."""
    
    @patch('src.cli.Transcriber')
    @patch('src.cli.os.listdir')
    def test_main_basic_transcription(self, mock_listdir, mock_transcriber_class, 
                                       temp_input_folder, temp_output_folder, mock_env_openai_key):
        """Test basic transcription without enhancements."""
        # Setup mocks
        mock_listdir.return_value = ['test.wav', 'test.mp3']
        mock_transcriber = MagicMock()
        mock_transcriber.transcribe.return_value = "Test transcription"
        mock_transcriber_class.return_value = mock_transcriber
        
        with patch('sys.argv', ['cli.py', '--input_folder', temp_input_folder, 
                                 '--output_folder', temp_output_folder]):
            main()
        
        # Verify transcriber was created
        mock_transcriber_class.assert_called_once()
        
        # Verify transcribe was called for each audio file
        assert mock_transcriber.transcribe.call_count == 2
    
    @patch('src.cli.Transcriber')
    def test_main_nonexistent_input_folder(self, mock_transcriber_class, temp_output_folder, 
                                            mock_env_openai_key):
        """Test with nonexistent input folder."""
        with patch('sys.argv', ['cli.py', '--input_folder', '/nonexistent/folder', 
                                 '--output_folder', temp_output_folder]):
            main()
        
        # Should not create transcriber if input folder doesn't exist
        mock_transcriber_class.assert_not_called()
    
    @patch('src.cli.Transcriber')
    @patch('src.cli.os.listdir')
    def test_main_creates_output_folder(self, mock_listdir, mock_transcriber_class, 
                                         temp_input_folder, tmp_path, mock_env_openai_key):
        """Test that output folder is created if it doesn't exist."""
        mock_listdir.return_value = ['test.wav']
        mock_transcriber = MagicMock()
        mock_transcriber.transcribe.return_value = "Test transcription"
        mock_transcriber_class.return_value = mock_transcriber
        
        output_folder = str(tmp_path / "new_output")
        assert not os.path.exists(output_folder)
        
        with patch('sys.argv', ['cli.py', '--input_folder', temp_input_folder, 
                                 '--output_folder', output_folder]):
            main()
        
        assert os.path.exists(output_folder)
    
    @patch('src.cli.Transcriber')
    @patch('src.cli.os.listdir')
    @patch('builtins.open', new_callable=MagicMock)
    def test_main_saves_verbatim_transcription(self, mock_open, mock_listdir, 
                                                mock_transcriber_class, temp_input_folder, 
                                                temp_output_folder, mock_env_openai_key):
        """Test that verbatim transcription is saved."""
        mock_listdir.return_value = ['audio.wav']
        mock_transcriber = MagicMock()
        mock_transcriber.transcribe.return_value = "Verbatim text"
        mock_transcriber_class.return_value = mock_transcriber
        
        with patch('sys.argv', ['cli.py', '--input_folder', temp_input_folder, 
                                 '--output_folder', temp_output_folder]):
            main()
        
        # Check that file was opened for writing
        mock_open.assert_called()
        # Check that write was called with transcription
        file_handle = mock_open.return_value.__enter__.return_value
        file_handle.write.assert_called_with("Verbatim text")
    
    @patch('src.cli.Transcriber')
    @patch('src.cli.os.listdir')
    def test_main_with_enhance_for_reading(self, mock_listdir, mock_transcriber_class, 
                                            temp_input_folder, temp_output_folder, mock_env_openai_key):
        """Test transcription with readability enhancement."""
        mock_listdir.return_value = ['test.wav']
        mock_transcriber = MagicMock()
        mock_transcriber.transcribe.return_value = "raw text"
        mock_transcriber.enhance_transcription.return_value = "Enhanced text"
        mock_transcriber_class.return_value = mock_transcriber
        
        with patch('sys.argv', ['cli.py', '--input_folder', temp_input_folder, 
                                 '--output_folder', temp_output_folder, '--enhance_for_reading']):
            main()
        
        # Verify enhancement was called
        mock_transcriber.enhance_transcription.assert_called_once_with("raw text")
    
    @patch('src.cli.Transcriber')
    @patch('src.cli.os.listdir')
    def test_main_with_format_as_interview(self, mock_listdir, mock_transcriber_class, 
                                            temp_input_folder, temp_output_folder, mock_env_openai_key):
        """Test transcription with interview formatting."""
        mock_listdir.return_value = ['test.wav']
        mock_transcriber = MagicMock()
        mock_transcriber.transcribe.return_value = "raw text"
        mock_transcriber.enhance_as_interview.return_value = "Interview format"
        mock_transcriber_class.return_value = mock_transcriber
        
        with patch('sys.argv', ['cli.py', '--input_folder', temp_input_folder, 
                                 '--output_folder', temp_output_folder, '--format_as_interview']):
            main()
        
        # Verify interview formatting was called
        mock_transcriber.enhance_as_interview.assert_called_once_with("raw text")
    
    @patch('src.cli.Transcriber')
    @patch('src.cli.os.listdir')
    def test_main_filters_non_audio_files(self, mock_listdir, mock_transcriber_class, 
                                           temp_input_folder, temp_output_folder, mock_env_openai_key):
        """Test that non-audio files are filtered out."""
        mock_listdir.return_value = ['test.wav', 'test.txt', 'test.mp3', 'test.jpg']
        mock_transcriber = MagicMock()
        mock_transcriber.transcribe.return_value = "Test"
        mock_transcriber_class.return_value = mock_transcriber
        
        with patch('sys.argv', ['cli.py', '--input_folder', temp_input_folder, 
                                 '--output_folder', temp_output_folder]):
            main()
        
        # Should only transcribe .wav and .mp3 files (2 calls)
        assert mock_transcriber.transcribe.call_count == 2
    
    @patch('src.cli.Transcriber')
    @patch('src.cli.os.listdir')
    def test_main_handles_transcription_error(self, mock_listdir, mock_transcriber_class, 
                                               temp_input_folder, temp_output_folder, mock_env_openai_key):
        """Test that transcription errors are handled gracefully."""
        mock_listdir.return_value = ['test1.wav', 'test2.wav']
        mock_transcriber = MagicMock()
        mock_transcriber.transcribe.side_effect = [Exception("API Error"), "Success"]
        mock_transcriber_class.return_value = mock_transcriber
        
        with patch('sys.argv', ['cli.py', '--input_folder', temp_input_folder, 
                                 '--output_folder', temp_output_folder]):
            main()  # Should not raise exception
        
        # Both files should be attempted
        assert mock_transcriber.transcribe.call_count == 2
    
    @patch('src.cli.Transcriber')
    @patch('src.cli.os.listdir')
    def test_main_processes_m4a_files(self, mock_listdir, mock_transcriber_class, 
                                       temp_input_folder, temp_output_folder, mock_env_openai_key):
        """Test that .m4a files are processed."""
        mock_listdir.return_value = ['test.m4a']
        mock_transcriber = MagicMock()
        mock_transcriber.transcribe.return_value = "M4A transcription"
        mock_transcriber_class.return_value = mock_transcriber
        
        with patch('sys.argv', ['cli.py', '--input_folder', temp_input_folder, 
                                 '--output_folder', temp_output_folder]):
            main()
        
        mock_transcriber.transcribe.assert_called_once()
    
    @patch('src.cli.Transcriber')
    @patch('src.cli.os.listdir')
    def test_main_output_filename_conventions(self, mock_listdir, mock_transcriber_class, 
                                               temp_input_folder, temp_output_folder, mock_env_openai_key):
        """Test that output files follow naming conventions."""
        mock_listdir.return_value = ['meeting.wav']
        mock_transcriber = MagicMock()
        mock_transcriber.transcribe.return_value = "Transcription"
        mock_transcriber.enhance_transcription.return_value = "Enhanced"
        mock_transcriber.enhance_as_interview.return_value = "Interview"
        mock_transcriber_class.return_value = mock_transcriber
        
        with patch('sys.argv', ['cli.py', '--input_folder', temp_input_folder, 
                                 '--output_folder', temp_output_folder, 
                                 '--enhance_for_reading', '--format_as_interview']):
            with patch('builtins.open', MagicMock()) as mock_open:
                main()
                
                # Check that three files were created with correct names
                calls = mock_open.call_args_list
                filenames = [call[0][0] for call in calls]
                
                assert any('meeting_transcription.txt' in f for f in filenames)
                assert any('meeting_enhanced.txt' in f for f in filenames)
                assert any('meeting_enhanced_interview.txt' in f for f in filenames)


class TestCLIArgumentParsing:
    """Tests for CLI argument parsing."""
    
    def test_required_arguments_missing(self):
        """Test that missing required arguments causes exit."""
        with patch('sys.argv', ['cli.py']):
            with pytest.raises(SystemExit):
                main()
    
    def test_input_folder_required(self):
        """Test that input_folder is required."""
        with patch('sys.argv', ['cli.py', '--output_folder', '/tmp/output']):
            with pytest.raises(SystemExit):
                main()
    
    def test_output_folder_required(self):
        """Test that output_folder is required."""
        with patch('sys.argv', ['cli.py', '--input_folder', '/tmp/input']):
            with pytest.raises(SystemExit):
                main()
