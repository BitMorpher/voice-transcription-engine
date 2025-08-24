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
        transcription = "this is a test transcript without punctuation"
        enhanced_transcription = self.transcriber.enhance_transcription(transcription)
        self.assertIsInstance(enhanced_transcription, str)
        self.assertNotEqual(transcription, enhanced_transcription)

    def test_enhance_as_interview(self):
        transcription = "hello can you tell me about your experience yes I started in 1990"
        interview_text = self.transcriber.enhance_as_interview(transcription)
        self.assertIsInstance(interview_text, str)
        self.assertIn("Interviewer:", interview_text)
        self.assertIn("Interviewee:", interview_text)

if __name__ == '__main__':
    unittest.main()