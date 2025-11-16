"""Integration tests for end-to-end transcription workflows."""
import os
import pytest
from unittest.mock import MagicMock, patch
from src.transcriber import Transcriber
from src.cli import main


@pytest.mark.integration
class TestEndToEndTranscription:
    """Integration tests for complete transcription workflow."""
    
    @patch('src.transcriber.openai.OpenAI')
    def test_full_transcription_pipeline(self, mock_openai_class, mock_env_openai_key, 
                                          mock_audio_file, tmp_path):
        """Test complete transcription pipeline from file to output."""
        # Setup mock
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "Complete transcription text"
        mock_client.audio.transcriptions.create.return_value = mock_response
        mock_openai_class.return_value = mock_client
        
        # Create transcriber and transcribe
        transcriber = Transcriber()
        result = transcriber.transcribe(mock_audio_file)
        
        # Verify result
        assert result == "Complete transcription text"
        assert mock_client.audio.transcriptions.create.called
    
    @patch('src.transcriber.openai.OpenAI')
    def test_transcription_with_enhancement_pipeline(self, mock_openai_class, 
                                                      mock_env_openai_key, mock_audio_file):
        """Test transcription followed by enhancement."""
        # Setup mocks
        mock_client = MagicMock()
        
        # Mock transcription
        mock_transcribe_response = MagicMock()
        mock_transcribe_response.text = "raw transcription"
        
        # Mock enhancement
        mock_enhance_response = MagicMock()
        mock_enhance_response.choices = [MagicMock()]
        mock_enhance_response.choices[0].message.content = "Enhanced transcription."
        
        mock_client.audio.transcriptions.create.return_value = mock_transcribe_response
        mock_client.chat.completions.create.return_value = mock_enhance_response
        mock_openai_class.return_value = mock_client
        
        # Execute pipeline
        transcriber = Transcriber()
        raw = transcriber.transcribe(mock_audio_file)
        enhanced = transcriber.enhance_transcription(raw)
        
        # Verify
        assert raw == "raw transcription"
        assert enhanced == "Enhanced transcription."
    
    @patch('src.transcriber.openai.OpenAI')
    def test_transcription_with_interview_format_pipeline(self, mock_openai_class, 
                                                           mock_env_openai_key, mock_audio_file):
        """Test transcription followed by interview formatting."""
        # Setup mocks
        mock_client = MagicMock()
        
        mock_transcribe_response = MagicMock()
        mock_transcribe_response.text = "conversation transcript"
        
        mock_interview_response = MagicMock()
        mock_interview_response.choices = [MagicMock()]
        mock_interview_response.choices[0].message.content = "Interviewer: Question\nInterviewee: Answer"
        
        mock_client.audio.transcriptions.create.return_value = mock_transcribe_response
        mock_client.chat.completions.create.return_value = mock_interview_response
        mock_openai_class.return_value = mock_client
        
        # Execute pipeline
        transcriber = Transcriber()
        raw = transcriber.transcribe(mock_audio_file)
        interview = transcriber.enhance_as_interview(raw)
        
        # Verify
        assert raw == "conversation transcript"
        assert "Interviewer:" in interview
        assert "Interviewee:" in interview


@pytest.mark.integration
class TestCLIIntegration:
    """Integration tests for CLI workflow."""
    
    @patch('src.cli.Transcriber')
    @patch('src.cli.os.listdir')
    def test_cli_processes_multiple_files(self, mock_listdir, mock_transcriber_class, 
                                           temp_input_folder, temp_output_folder, mock_env_openai_key):
        """Test CLI processes multiple audio files in a folder."""
        # Setup
        mock_listdir.return_value = ['audio1.wav', 'audio2.mp3', 'audio3.m4a', 'readme.txt']
        mock_transcriber = MagicMock()
        mock_transcriber.transcribe.return_value = "Test transcription"
        mock_transcriber_class.return_value = mock_transcriber
        
        # Execute
        with patch('sys.argv', ['cli.py', '--input_folder', temp_input_folder, 
                                 '--output_folder', temp_output_folder]):
            main()
        
        # Verify only audio files were processed (not readme.txt)
        assert mock_transcriber.transcribe.call_count == 3
    
    @patch('src.cli.Transcriber')
    @patch('src.cli.os.listdir')
    @patch('builtins.open', new_callable=MagicMock)
    def test_cli_creates_all_output_formats(self, mock_open, mock_listdir, 
                                             mock_transcriber_class, temp_input_folder, 
                                             temp_output_folder, mock_env_openai_key):
        """Test CLI creates verbatim, enhanced, and interview outputs."""
        # Setup
        mock_listdir.return_value = ['test.wav']
        mock_transcriber = MagicMock()
        mock_transcriber.transcribe.return_value = "Raw text"
        mock_transcriber.enhance_transcription.return_value = "Enhanced text"
        mock_transcriber.enhance_as_interview.return_value = "Interview text"
        mock_transcriber_class.return_value = mock_transcriber
        
        # Execute with all flags
        with patch('sys.argv', ['cli.py', '--input_folder', temp_input_folder, 
                                 '--output_folder', temp_output_folder,
                                 '--enhance_for_reading', '--format_as_interview']):
            main()
        
        # Verify all three enhancement methods were called
        mock_transcriber.transcribe.assert_called_once()
        mock_transcriber.enhance_transcription.assert_called_once_with("Raw text")
        mock_transcriber.enhance_as_interview.assert_called_once_with("Raw text")
        
        # Verify files were written (3 files per audio file)
        assert mock_open.call_count >= 3
    
    @patch('src.cli.Transcriber')
    @patch('src.cli.os.listdir')
    def test_cli_resilient_to_individual_file_failures(self, mock_listdir, 
                                                        mock_transcriber_class, 
                                                        temp_input_folder, temp_output_folder,
                                                        mock_env_openai_key):
        """Test CLI continues processing after individual file failures."""
        # Setup - one file fails, others succeed
        mock_listdir.return_value = ['fail.wav', 'success1.wav', 'success2.wav']
        mock_transcriber = MagicMock()
        mock_transcriber.transcribe.side_effect = [
            Exception("File corrupted"),
            "Success 1",
            "Success 2"
        ]
        mock_transcriber_class.return_value = mock_transcriber
        
        # Execute - should not raise exception
        with patch('sys.argv', ['cli.py', '--input_folder', temp_input_folder, 
                                 '--output_folder', temp_output_folder]):
            main()  # Should complete without raising
        
        # Verify all files were attempted
        assert mock_transcriber.transcribe.call_count == 3


@pytest.mark.integration
class TestUtilsIntegration:
    """Integration tests for utils functions in workflows."""
    
    def test_validate_and_format_workflow(self, tmp_path):
        """Test validation and formatting workflow."""
        from src.utils import validate_input_path, validate_output_path, format_transcription
        
        # Create test directories
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        output_dir = tmp_path / "output"
        
        # Test validation workflow
        validate_input_path(str(input_dir))
        validate_output_path(str(output_dir))
        
        # Output directory should now exist
        assert output_dir.exists()
        
        # Test formatting workflow
        raw_text = "  line one\nline two  \n\n  line three  "
        formatted = format_transcription(raw_text)
        
        # The function does strip() -> replace('\n', ' ') -> replace('  ', ' ') (one pass)
        assert formatted == "line one line two   line three"
        assert '\n' not in formatted
        assert formatted.strip() == formatted
