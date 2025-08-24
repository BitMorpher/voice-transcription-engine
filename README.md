# voice-transcription-engine

## Overview
The `voice-transcription-engine` is a command-line interface (CLI) tool that utilizes OpenAI's Whisper model to transcribe audio recordings into text. This project is designed to provide an easy-to-use interface for users to process their audio files and obtain accurate transcriptions.

## Features
- Transcribe audio files using OpenAI's Whisper model.
- Support for batch processing of audio files in a specified input folder.
- Option to enhance transcriptions for better readability.
- Option to format transcriptions as an interview between two people using OpenAI (saved as *_enhanced_interview.txt).
- Detailed error handling and validation for input files and directories.

## Installation
To install the necessary dependencies, you can use the following command:

```bash
pip install -r requirements.txt
```

## Usage

To use the CLI, navigate to the project directory and run the following command:

```bash
python src/cli.py --input_folder <path_to_audio_files> --output_folder <path_to_save_transcriptions> [--enhance_for_reading] [--format_as_interview]
```

### Parameters
- `--input_folder`: The path to the folder containing audio files to be transcribed.
- `--output_folder`: The path to the folder where the transcriptions will be saved.
- `--enhance_for_reading`: Optional flag to enhance the readability of the transcriptions (creates `[original_filename]_enhanced.txt`).
- `--format_as_interview`: Optional flag to format the transcription as an interview between two people (creates `[original_filename]_enhanced_interview.txt`).

## Project Structure
```
voice-transcription-engine
├── src
│   ├── cli.py          # Command-line interface implementation
│   ├── transcriber.py  # Core transcription functionality (includes OpenAI Whisper and enhancement logic)
│   ├── utils.py        # Utility functions for file handling and formatting
│   └── types
│       └── __init__.py # Custom types and data structures
├── tests
│   ├── test_cli.py     # Unit tests for CLI functionality
│   ├── test_transcriber.py # Unit tests for Transcriber class
│   └── test_utils.py   # Unit tests for utility functions
├── requirements.txt     # List of dependencies
├── setup.py             # Packaging information
└── .gitignore           # Files and directories to ignore in version control
```

## Contributing

## Output Files
For each audio file processed, the following files may be generated in the output folder:

- `[original_filename]_transcription.txt`: Verbatim transcription of the audio.
- `[original_filename]_enhanced.txt`: Readability-enhanced transcription (if `--enhance_for_reading` is used).
- `[original_filename]_enhanced_interview.txt`: Interview-formatted transcription (if `--format_as_interview` is used).

## API Key
To use OpenAI features, set your `OPENAI_API_KEY` environment variable before running the CLI.

Contributions are welcome! Please feel free to submit a pull request or open an issue for any suggestions or improvements.

## License
This project is licensed under the MIT License. See the LICENSE file for more details.