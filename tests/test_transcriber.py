import unittest
from src.transcriber import Transcriber

class TestTranscriber(unittest.TestCase):

    def setUp(self):
        self.transcriber = Transcriber()

    def test_transcribe_audio(self):
        # Assuming there's a sample audio file for testing
        audio_file = 'tests/sample_audio.wav'
        transcription = self.transcriber.transcribe(audio_file)
        self.assertIsInstance(transcription, str)
        self.assertGreater(len(transcription), 0)

    def test_transcribe_nonexistent_audio(self):
        with self.assertRaises(FileNotFoundError):
            self.transcriber.transcribe('nonexistent_file.wav')

    def test_enhanced_readability(self):
        audio_file = 'tests/sample_audio.wav'
        transcription = self.transcriber.transcribe(audio_file)
        enhanced_transcription = self.transcriber.enhance_for_reading(transcription)
        self.assertIsInstance(enhanced_transcription, str)
        self.assertNotEqual(transcription, enhanced_transcription)

if __name__ == '__main__':
    unittest.main()