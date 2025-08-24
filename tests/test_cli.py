import unittest
from unittest.mock import patch, MagicMock
import sys
import os
from src.cli import main

class TestCLI(unittest.TestCase):

    @patch('src.cli.Transcriber')
    @patch('argparse.ArgumentParser.parse_args')
    @patch('os.listdir')
    @patch('os.path.exists')
    @patch('builtins.open')
    def test_main_with_all_flags(self, mock_open, mock_exists, mock_listdir, mock_parse_args, mock_transcriber):
        # Setup mocks
        mock_parse_args.return_value = MagicMock(
            input_folder='input', output_folder='output', enhance_for_reading=True, format_as_interview=True
        )
        mock_exists.side_effect = lambda path: True
        mock_listdir.return_value = ['audio1.wav']
        mock_transcriber.return_value.transcribe.return_value = "Verbatim transcript"
        mock_transcriber.return_value.enhance_transcription.return_value = "Enhanced transcript"
        mock_transcriber.return_value.enhance_as_interview.return_value = "Interview transcript"
        mock_file = MagicMock()
        mock_open.return_value.__enter__.return_value = mock_file

        with patch('sys.stdout', new_callable=MagicMock()) as mock_stdout:
            main()
            # Check calls for all output files
            mock_transcriber.return_value.transcribe.assert_called_once_with(os.path.join('input', 'audio1.wav'))
            mock_transcriber.return_value.enhance_transcription.assert_called_once_with("Verbatim transcript")
            mock_transcriber.return_value.enhance_as_interview.assert_called_once_with("Verbatim transcript")
            self.assertEqual(mock_open.call_count, 3)  # 3 output files
            # Check print statements for each output
            mock_stdout.write.assert_any_call("Verbatim transcription saved to 'output/audio1_transcription.txt'.\n")
            mock_stdout.write.assert_any_call("Enhanced (readability) transcription saved to 'output/audio1_enhanced.txt'.\n")
            mock_stdout.write.assert_any_call("Interview-formatted transcription saved to 'output/audio1_enhanced_interview.txt'.\n")

    @patch('src.cli.Transcriber')
    @patch('argparse.ArgumentParser.parse_args')
    @patch('os.path.exists')
    def test_main_with_missing_input_folder(self, mock_exists, mock_parse_args, mock_transcriber):
        mock_parse_args.return_value = MagicMock(input_folder='missing', output_folder='output', enhance_for_reading=False, format_as_interview=False)
        mock_exists.side_effect = lambda path: False if path == 'missing' else True
        with patch('sys.stdout', new_callable=MagicMock()) as mock_stdout:
            main()
            mock_stdout.write.assert_any_call("Error: The input folder 'missing' does not exist.\n")

    @patch('src.cli.Transcriber')
    @patch('argparse.ArgumentParser.parse_args')
    @patch('os.path.exists')
    @patch('os.makedirs')
    def test_main_creates_output_folder(self, mock_makedirs, mock_exists, mock_parse_args, mock_transcriber):
        mock_parse_args.return_value = MagicMock(input_folder='input', output_folder='output', enhance_for_reading=False, format_as_interview=False)
        # input exists, output does not
        mock_exists.side_effect = lambda path: path == 'input'
        with patch('os.listdir', return_value=['audio1.wav']), patch('builtins.open', MagicMock()):
            main()
            mock_makedirs.assert_called_once_with('output', exist_ok=True)

if __name__ == '__main__':
    unittest.main()