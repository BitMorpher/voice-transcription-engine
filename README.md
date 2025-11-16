# voice-transcription-engine

## Overview
The `voice-transcription-engine` is a command-line interface (CLI) tool that leverages OpenAI's Whisper model to transcribe audio recordings into text with high accuracy. This project provides a user-friendly interface for batch processing audio files with optional AI-powered enhancements for improved readability and interview-style formatting.

## Features
- **Whisper-powered Transcription**: Utilizes OpenAI's Whisper model API for accurate audio-to-text conversion
- **Batch Processing**: Process multiple audio files in a directory automatically
- **Large File Support**: Automatically handles files larger than 20MB by intelligently splitting them into chunks
- **Multiple Output Formats**:
  - Verbatim transcription (raw output from Whisper)
  - Enhanced readability version (improved punctuation, capitalization, and paragraphing)
  - Interview-style formatting (structured as Interviewer/Interviewee dialogue)
- **Supported Audio Formats**: WAV, MP3, M4A
- **Robust Error Handling**: Detailed logging with JSON-formatted output for debugging
- **Automatic Directory Creation**: Creates output directories if they don't exist

## Prerequisites

- **Python**: 3.6 or higher (tested with Python 3.12)
- **OpenAI API Key**: Required for accessing Whisper and GPT-4 models
- **FFmpeg**: Required by pydub for audio processing (must be installed separately)

### Installing FFmpeg

**macOS**:
```bash
brew install ffmpeg
```

**Ubuntu/Debian**:
```bash
sudo apt-get install ffmpeg
```

**Windows**:
Download from [ffmpeg.org](https://ffmpeg.org/download.html) and add to PATH

## Installation

1. **Clone the repository**:
```bash
git clone https://github.com/BitMorpher/voice-transcription-engine.git
cd voice-transcription-engine
```

2. **Install Python dependencies**:
```bash
pip install -r requirements.txt
```

3. **Set up OpenAI API key**:
```bash
export OPENAI_API_KEY='your-api-key-here'
```

For permanent setup, add the above line to your `~/.bashrc`, `~/.zshrc`, or equivalent shell configuration file.

## Usage

### Basic Usage

Transcribe all audio files in a folder:

```bash
python src/cli.py --input_folder /path/to/audio/files --output_folder /path/to/output
```

### With Enhanced Readability

Generate both verbatim and enhanced versions:

```bash
python src/cli.py --input_folder /path/to/audio/files --output_folder /path/to/output --enhance_for_reading
```

### With Interview Formatting

Generate interview-style formatted transcriptions:

```bash
python src/cli.py --input_folder /path/to/audio/files --output_folder /path/to/output --format_as_interview
```

### All Options Combined

Generate all three output formats:

```bash
python src/cli.py --input_folder /path/to/audio/files --output_folder /path/to/output --enhance_for_reading --format_as_interview
```

## Command-Line Parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `--input_folder` | Yes | Path to the folder containing audio files (`.wav`, `.mp3`, `.m4a`) |
| `--output_folder` | Yes | Path to the folder where transcriptions will be saved |
| `--enhance_for_reading` | No | Generate an enhanced version with improved punctuation, capitalization, and paragraph breaks |
| `--format_as_interview` | No | Generate an interview-formatted version with Interviewer/Interviewee labels |

## Output Files

The tool generates files with the following naming conventions:

- **`<filename>_transcription.txt`**: Verbatim transcription from Whisper (always generated)
- **`<filename>_enhanced.txt`**: Readability-enhanced version (if `--enhance_for_reading` is used)
- **`<filename>_enhanced_interview.txt`**: Interview-formatted version (if `--format_as_interview` is used)

### Example
For an input file named `meeting.mp3`:
- `meeting_transcription.txt` (verbatim)
- `meeting_enhanced.txt` (if enhanced)
- `meeting_enhanced_interview.txt` (if interview format)

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENAI_API_KEY` | Yes | Your OpenAI API key for accessing Whisper and GPT-4 models |

## How It Works

1. **File Discovery**: Scans the input folder for supported audio files (`.wav`, `.mp3`, `.m4a`)
2. **File Size Check**: Determines if files need to be split (files larger than 20MB)
3. **Transcription**: 
   - Small files: Sent directly to Whisper API
   - Large files: Automatically split into chunks, transcribed separately, then combined
4. **Enhancement** (optional): Uses GPT-4.1-mini to improve readability while preserving meaning
5. **Interview Formatting** (optional): Uses GPT-4.1-mini to structure content as an interview dialogue
6. **Output**: Saves all requested formats to the output folder with appropriate filenames

## Project Structure
```
voice-transcription-engine
├── src
│   ├── cli.py           # Command-line interface implementation
│   ├── transcriber.py   # Core transcription functionality with chunking logic
│   ├── logger.py        # JSON-formatted logging utilities
│   └── utils.py         # Utility functions for file handling and formatting
├── tests
│   ├── test_cli.py      # Unit tests for CLI functionality
│   ├── test_transcriber.py # Unit tests for Transcriber class
│   └── test_utils.py    # Unit tests for utility functions
├── notebook
│   └── development.ipynb # Development and experimentation notebook
├── requirements.txt     # List of Python dependencies
├── setup.py             # Packaging and distribution configuration
├── .gitignore           # Files and directories to ignore in version control
└── README.md            # This file
```

## Troubleshooting

### "OPENAI_API_KEY environment variable is required"
Ensure you've set the `OPENAI_API_KEY` environment variable:
```bash
export OPENAI_API_KEY='your-api-key-here'
```

### "Input folder does not exist"
Verify the path to your input folder is correct and the directory exists.

### FFmpeg-related errors
Ensure FFmpeg is installed and accessible in your system PATH:
```bash
ffmpeg -version
```

### Large File Processing Issues
For very large M4A files, the tool automatically splits them into 5-minute chunks. If you encounter issues, ensure you have sufficient disk space for temporary files.

### API Rate Limits
If processing many files, you may hit OpenAI API rate limits. The tool will log errors for individual files and continue processing others.

## Development

### Running Tests
```bash
python -m pytest tests/
```

### Adding New Features
The codebase is modular:
- **CLI logic**: Modify `src/cli.py`
- **Transcription logic**: Modify `src/transcriber.py`
- **Utilities**: Modify `src/utils.py`
- **Logging**: Modify `src/logger.py`

## Contributing
Contributions are welcome! Please follow these steps:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes and test thoroughly
4. Commit your changes (`git commit -m 'Add amazing feature'`)
5. Push to the branch (`git push origin feature/amazing-feature`)
6. Open a Pull Request

Please ensure your code follows the existing style and includes appropriate tests.

## License
This project is licensed under the MIT License. See the LICENSE file for more details.

## Acknowledgments
- OpenAI for the Whisper and GPT-4 models
- The pydub library for audio processing capabilities

## Support
For issues, questions, or suggestions, please open an issue on the GitHub repository.