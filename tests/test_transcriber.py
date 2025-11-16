"""Unit tests for transcriber.py module."""
import os
import pytest
from unittest.mock import MagicMock, patch, mock_open, Mock
import io
from src.transcriber import Transcriber


class TestTranscriberInit:
    """Tests for Transcriber initialization."""
    
    def test_init_with_api_key(self, mock_env_openai_key):
        """Test initialization with API key set."""
        transcriber = Transcriber()
        assert transcriber.model_name == "whisper-1"
        assert transcriber.max_bytes == 20 * 1024 * 1024
    
    def test_init_without_api_key(self, monkeypatch):
        """Test initialization without API key raises error."""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(EnvironmentError, match="OPENAI_API_KEY"):
            Transcriber()
    
    def test_init_with_custom_model(self, mock_env_openai_key):
        """Test initialization with custom model name."""
        transcriber = Transcriber(model_name="whisper-2")
        assert transcriber.model_name == "whisper-2"


class TestTranscriberTranscribe:
    """Tests for Transcriber.transcribe method."""
    
    @patch('src.transcriber.openai.OpenAI')
    def test_transcribe_small_file(self, mock_openai_class, mock_env_openai_key, mock_audio_file):
        """Test transcribing a small audio file."""
        # Setup mock
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "Test transcription result"
        mock_client.audio.transcriptions.create.return_value = mock_response
        mock_openai_class.return_value = mock_client
        
        transcriber = Transcriber()
        result = transcriber.transcribe(mock_audio_file)
        
        assert result == "Test transcription result"
        mock_client.audio.transcriptions.create.assert_called_once()
    
    @patch('src.transcriber.openai.OpenAI')
    def test_transcribe_nonexistent_file(self, mock_openai_class, mock_env_openai_key):
        """Test transcribing a file that doesn't exist."""
        mock_openai_class.return_value = MagicMock()
        transcriber = Transcriber()
        
        with pytest.raises(FileNotFoundError, match="Could not access file"):
            transcriber.transcribe("/nonexistent/file.wav")
    
    @patch('src.transcriber.openai.OpenAI')
    @patch('src.transcriber.Transcriber._split_audio_into_chunks')
    def test_transcribe_large_file_with_chunks(self, mock_split, mock_openai_class, 
                                                mock_env_openai_key, mock_large_audio_file):
        """Test transcribing a large file that needs splitting."""
        # Setup mocks
        mock_client = MagicMock()
        mock_response1 = MagicMock()
        mock_response1.text = "Part 1"
        mock_response2 = MagicMock()
        mock_response2.text = "Part 2"
        mock_client.audio.transcriptions.create.side_effect = [mock_response1, mock_response2]
        mock_openai_class.return_value = mock_client
        
        # Mock chunk splitting
        chunk1 = io.BytesIO(b"chunk1")
        chunk1.name = "part_0.wav"
        chunk2 = io.BytesIO(b"chunk2")
        chunk2.name = "part_1.wav"
        mock_split.return_value = [chunk1, chunk2]
        
        transcriber = Transcriber()
        result = transcriber.transcribe(mock_large_audio_file)
        
        assert result == "Part 1\n\nPart 2"
        assert mock_client.audio.transcriptions.create.call_count == 2
    
    @patch('src.transcriber.openai.OpenAI')
    def test_transcribe_api_error(self, mock_openai_class, mock_env_openai_key, mock_audio_file):
        """Test handling API errors during transcription."""
        mock_client = MagicMock()
        mock_client.audio.transcriptions.create.side_effect = Exception("API Error")
        mock_openai_class.return_value = mock_client
        
        transcriber = Transcriber()
        with pytest.raises(Exception, match="API Error"):
            transcriber.transcribe(mock_audio_file)


class TestSplitAudioIntoChunks:
    """Tests for _split_audio_into_chunks method."""
    
    def test_split_small_file_raises_error(self, mock_env_openai_key, mock_audio_file):
        """Test that splitting a small file raises ValueError."""
        transcriber = Transcriber()
        with pytest.raises(ValueError, match="File does not need splitting"):
            transcriber._split_audio_into_chunks(mock_audio_file)
    
    @patch('src.transcriber.AudioSegment.from_file')
    def test_split_zero_duration_audio(self, mock_audio_segment, mock_env_openai_key, mock_large_audio_file):
        """Test splitting audio with zero duration raises error."""
        mock_audio = MagicMock()
        mock_audio.__len__.return_value = 0
        mock_audio_segment.return_value = mock_audio
        
        transcriber = Transcriber()
        with pytest.raises(ValueError, match="Audio duration is zero"):
            transcriber._split_audio_into_chunks(mock_large_audio_file)
    
    @patch('src.transcriber.AudioSegment.from_file')
    def test_split_audio_creates_chunks(self, mock_audio_segment, mock_env_openai_key, mock_large_audio_file):
        """Test that audio is split into multiple chunks."""
        # Mock audio segment
        mock_audio = MagicMock()
        mock_audio.__len__.return_value = 600000  # 10 minutes in ms
        mock_audio.__getitem__.return_value = MagicMock()
        mock_audio_segment.return_value = mock_audio
        
        # Mock export
        mock_chunk = MagicMock()
        mock_chunk.export = MagicMock()
        mock_audio.__getitem__.return_value = mock_chunk
        
        transcriber = Transcriber()
        chunks = transcriber._split_audio_into_chunks(mock_large_audio_file)
        
        assert isinstance(chunks, list)
        assert len(chunks) > 0


class TestSplitAudioByDuration:
    """Tests for _split_audio_by_duration method."""
    
    @patch('src.transcriber.AudioSegment.from_file')
    @patch('src.transcriber.tempfile.NamedTemporaryFile')
    def test_split_by_duration_creates_temp_files(self, mock_tempfile, mock_audio_segment, 
                                                    mock_env_openai_key, tmp_path):
        """Test that splitting by duration creates temporary files."""
        # Mock audio
        mock_audio = MagicMock()
        mock_audio.__len__.return_value = 600000  # 10 minutes
        mock_chunk = MagicMock()
        mock_chunk.export = MagicMock()
        mock_audio.__getitem__.return_value = mock_chunk
        mock_audio_segment.return_value = mock_audio
        
        # Mock temp file
        mock_temp = MagicMock()
        mock_temp.name = str(tmp_path / "temp_0.mp4")
        mock_temp.close = MagicMock()
        mock_tempfile.return_value = mock_temp
        
        transcriber = Transcriber()
        chunk_ms = 300000  # 5 minutes
        result = transcriber._split_audio_by_duration("/fake/path.m4a", chunk_ms)
        
        assert isinstance(result, list)
        assert len(result) > 0
    
    @patch('src.transcriber.AudioSegment.from_file')
    def test_split_zero_duration_raises_error(self, mock_audio_segment, mock_env_openai_key):
        """Test that zero duration audio raises error."""
        mock_audio = MagicMock()
        mock_audio.__len__.return_value = 0
        mock_audio_segment.return_value = mock_audio
        
        transcriber = Transcriber()
        with pytest.raises(ValueError, match="Audio duration is zero"):
            transcriber._split_audio_by_duration("/fake/path.m4a", 300000)


class TestEnhanceTranscription:
    """Tests for enhance_transcription method."""
    
    @patch('src.transcriber.openai.OpenAI')
    def test_enhance_transcription(self, mock_openai_class, mock_env_openai_key, sample_transcription):
        """Test enhancing transcription for readability."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "This is a test transcription with proper punctuation."
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_class.return_value = mock_client
        
        transcriber = Transcriber()
        result = transcriber.enhance_transcription(sample_transcription)
        
        assert result == "This is a test transcription with proper punctuation."
        mock_client.chat.completions.create.assert_called_once()
        
        # Verify the prompt contains the transcription
        call_args = mock_client.chat.completions.create.call_args
        assert sample_transcription in call_args.kwargs['messages'][0]['content']
    
    @patch('src.transcriber.openai.OpenAI')
    def test_enhance_empty_transcription(self, mock_openai_class, mock_env_openai_key):
        """Test enhancing empty transcription."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = ""
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_class.return_value = mock_client
        
        transcriber = Transcriber()
        result = transcriber.enhance_transcription("")
        
        assert result == ""


class TestEnhanceAsInterview:
    """Tests for enhance_as_interview method."""
    
    @patch('src.transcriber.openai.OpenAI')
    def test_enhance_as_interview(self, mock_openai_class, mock_env_openai_key, sample_transcription):
        """Test formatting transcription as interview."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Interviewer: Question\nInterviewee: Answer"
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_class.return_value = mock_client
        
        transcriber = Transcriber()
        result = transcriber.enhance_as_interview(sample_transcription)
        
        assert "Interviewer:" in result
        assert "Interviewee:" in result
        mock_client.chat.completions.create.assert_called_once()
    
    @patch('src.transcriber.openai.OpenAI')
    def test_enhance_as_interview_preserves_content(self, mock_openai_class, mock_env_openai_key):
        """Test that interview formatting preserves original content."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Formatted interview text"
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_class.return_value = mock_client
        
        transcriber = Transcriber()
        original = "Original transcript content"
        result = transcriber.enhance_as_interview(original)
        
        # Verify the original content was sent to the API
        call_args = mock_client.chat.completions.create.call_args
        assert original in call_args.kwargs['messages'][0]['content']
