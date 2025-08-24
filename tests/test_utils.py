import os
import unittest
from voice_transcription_engine.utils import validate_input_path, format_transcription

class TestUtils(unittest.TestCase):

    def test_validate_input_path_valid(self):
        valid_path = "path/to/valid/audio/file.wav"
        self.assertTrue(validate_input_path(valid_path))

    def test_validate_input_path_invalid(self):
        invalid_path = "path/to/invalid/audio/file.wav"
        self.assertFalse(validate_input_path(invalid_path))

    def test_format_transcription(self):
        transcription = "This is a test transcription."
        formatted = format_transcription(transcription)
        self.assertEqual(formatted, "This is a test transcription.")  # Assuming no formatting changes

if __name__ == '__main__':
    unittest.main()