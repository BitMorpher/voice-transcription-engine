"""Pytest configuration and shared fixtures."""
import os
import sys
import pytest
from unittest.mock import MagicMock, Mock
import io

# Add src directory to Python path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))


@pytest.fixture
def mock_openai_client():
    """Mock OpenAI client for testing."""
    client = MagicMock()
    
    # Mock transcription response
    transcription_response = MagicMock()
    transcription_response.text = "This is a test transcription."
    client.audio.transcriptions.create.return_value = transcription_response
    
    # Mock chat completion response
    chat_response = MagicMock()
    chat_response.choices = [MagicMock()]
    chat_response.choices[0].message.content = "This is an enhanced transcription."
    client.chat.completions.create.return_value = chat_response
    
    return client


@pytest.fixture
def mock_audio_file(tmp_path):
    """Create a mock audio file for testing."""
    audio_path = tmp_path / "test_audio.wav"
    # Create a minimal WAV file (44 bytes header + some data)
    wav_header = b'RIFF' + (36).to_bytes(4, 'little') + b'WAVE'
    wav_header += b'fmt ' + (16).to_bytes(4, 'little')
    wav_header += (1).to_bytes(2, 'little')  # Audio format (PCM)
    wav_header += (1).to_bytes(2, 'little')  # Number of channels
    wav_header += (44100).to_bytes(4, 'little')  # Sample rate
    wav_header += (88200).to_bytes(4, 'little')  # Byte rate
    wav_header += (2).to_bytes(2, 'little')  # Block align
    wav_header += (16).to_bytes(2, 'little')  # Bits per sample
    wav_header += b'data' + (0).to_bytes(4, 'little')
    
    audio_path.write_bytes(wav_header)
    return str(audio_path)


@pytest.fixture
def mock_large_audio_file(tmp_path):
    """Create a mock large audio file (>20MB) for testing."""
    audio_path = tmp_path / "large_audio.wav"
    # Create a file larger than 20MB
    size = 21 * 1024 * 1024  # 21 MB
    audio_path.write_bytes(b'\x00' * size)
    return str(audio_path)


@pytest.fixture
def temp_input_folder(tmp_path):
    """Create a temporary input folder with test audio files."""
    input_folder = tmp_path / "input"
    input_folder.mkdir()
    
    # Create test audio files
    (input_folder / "test1.wav").write_bytes(b'dummy wav data')
    (input_folder / "test2.mp3").write_bytes(b'dummy mp3 data')
    (input_folder / "test3.m4a").write_bytes(b'dummy m4a data')
    (input_folder / "ignored.txt").write_text("not an audio file")
    
    return str(input_folder)


@pytest.fixture
def temp_output_folder(tmp_path):
    """Create a temporary output folder for test results."""
    output_folder = tmp_path / "output"
    output_folder.mkdir()
    return str(output_folder)


@pytest.fixture
def mock_env_openai_key(monkeypatch):
    """Set mock OPENAI_API_KEY environment variable."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-api-key-12345")


@pytest.fixture
def sample_transcription():
    """Sample transcription text for testing."""
    return "this is a test transcription with no punctuation or capitalization"


@pytest.fixture
def sample_enhanced_transcription():
    """Sample enhanced transcription for testing."""
    return "This is a test transcription with proper punctuation and capitalization."
