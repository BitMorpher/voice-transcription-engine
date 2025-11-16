import unittest
from unittest.mock import patch, MagicMock
import sys
import os
from src.cli import main

class TestCLI(unittest.TestCase):

    @patch('src.cli.Transcriber')
    @patch('argparse.ArgumentParser.parse_args')
    def test_main_with_valid_arguments(self, mock_parse_args, mock_transcriber):
        mock_parse_args.return_value = MagicMock(input_folder='input', output_folder='output', enhance_for_reading=True)
        mock_transcriber.return_value.transcribe.return_value = "Transcription result"

        with patch('sys.stdout', new_callable=MagicMock()) as mock_stdout:
            main()
            mock_transcriber.assert_called_once_with('input', 'output', True)
            mock_transcriber.return_value.transcribe.assert_called_once()
            mock_stdout.write.assert_called_once_with("Transcription result\n")

    @patch('src.cli.Transcriber')
    @patch('argparse.ArgumentParser.parse_args')
    def test_main_with_missing_input_folder(self, mock_parse_args, mock_transcriber):
        mock_parse_args.return_value = MagicMock(input_folder=None, output_folder='output', enhance_for_reading=False)

        with self.assertRaises(SystemExit):
            main()

    @patch('src.cli.Transcriber')
    @patch('argparse.ArgumentParser.parse_args')
    def test_main_with_invalid_output_folder(self, mock_parse_args, mock_transcriber):
        mock_parse_args.return_value = MagicMock(input_folder='input', output_folder=None, enhance_for_reading=False)

        with self.assertRaises(SystemExit):
            main()

if __name__ == '__main__':
    unittest.main()