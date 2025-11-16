# Tests

This directory contains the comprehensive test suite for the voice-transcription-engine project.

## Test Structure

```
tests/
├── conftest.py           # Shared pytest fixtures and configuration
├── test_cli.py           # Unit tests for CLI interface (13 tests)
├── test_logger.py        # Unit tests for logging utilities (15 tests)
├── test_transcriber.py   # Unit tests for transcription logic (16 tests)
├── test_utils.py         # Unit tests for utility functions (15 tests)
├── test_integration.py   # Integration tests for end-to-end workflows (7 tests)
└── fixtures/             # Test data and fixtures directory
```

## Running Tests

### Run all tests
```bash
pytest tests/
```

### Run with coverage report
```bash
pytest tests/ --cov=src --cov-report=term-missing
```

### Run specific test file
```bash
pytest tests/test_transcriber.py -v
```

### Run tests matching a pattern
```bash
pytest tests/ -k "transcribe" -v
```

### Run only unit tests
```bash
pytest tests/ -m unit -v
```

### Run only integration tests
```bash
pytest tests/ -m integration -v
```

## Test Coverage

Current test coverage (as of last update):
- **Total**: 85.20%
- **src/cli.py**: 85%
- **src/logger.py**: 100%
- **src/transcriber.py**: 81%
- **src/utils.py**: 100%

Coverage threshold is enforced at 85% in CI/CD pipeline.

## Test Categories

### Unit Tests
Unit tests focus on individual functions and methods in isolation:
- **test_utils.py**: Tests for path validation and text formatting
- **test_logger.py**: Tests for JSON logging formatter and logger configuration
- **test_transcriber.py**: Tests for transcription, audio splitting, and enhancement (with mocked OpenAI API)
- **test_cli.py**: Tests for CLI argument parsing and file processing logic

### Integration Tests
Integration tests verify end-to-end workflows:
- **test_integration.py**: Tests complete transcription pipelines, CLI workflows, and multi-module interactions

## Fixtures

Shared fixtures are defined in `conftest.py`:
- `mock_openai_client`: Mock OpenAI API client for testing
- `mock_audio_file`: Creates a minimal WAV file for testing
- `mock_large_audio_file`: Creates a file larger than 20MB for chunk testing
- `temp_input_folder`: Temporary directory with test audio files
- `temp_output_folder`: Temporary directory for test outputs
- `mock_env_openai_key`: Sets mock OPENAI_API_KEY environment variable
- `sample_transcription`: Sample transcription text for testing
- `sample_enhanced_transcription`: Sample enhanced transcription

## Mocking Strategy

Tests use extensive mocking to avoid:
1. **Real API calls**: OpenAI API calls are mocked to avoid costs and ensure test reliability
2. **File system dependencies**: Tests use temporary directories and mock files
3. **Network requests**: No real network calls are made during testing
4. **Environment dependencies**: API keys and environment variables are mocked

## Continuous Integration

Tests run automatically on GitHub Actions for:
- Push events to main, develop, and copilot/** branches
- Pull requests to main and develop branches
- Multiple Python versions: 3.9, 3.10, 3.11, 3.12

CI workflow includes:
1. Install system dependencies (ffmpeg)
2. Install Python dependencies
3. Run tests with coverage
4. Upload coverage reports to Codecov

## Writing New Tests

When adding new tests:

1. **Use pytest style**: Write test classes and functions with clear names
2. **Use fixtures**: Leverage existing fixtures in conftest.py
3. **Mock external dependencies**: Always mock API calls, file I/O where appropriate
4. **Test edge cases**: Include tests for error conditions and boundary cases
5. **Follow naming conventions**: Test files should be named `test_*.py`
6. **Add docstrings**: Document what each test verifies
7. **Mark integration tests**: Use `@pytest.mark.integration` for integration tests

Example test structure:
```python
class TestMyFeature:
    """Tests for MyFeature functionality."""
    
    def test_basic_case(self):
        """Test basic functionality."""
        # Arrange
        input_data = "test"
        
        # Act
        result = my_function(input_data)
        
        # Assert
        assert result == expected_output
```

## Troubleshooting

### Import Errors
If you get import errors, ensure you're running pytest from the project root:
```bash
cd /path/to/voice-transcription-engine
pytest tests/
```

### Missing Dependencies
Install test dependencies:
```bash
pip install -r requirements.txt
```

### Coverage Too Low
If coverage is below 85%, add tests for uncovered lines:
```bash
pytest tests/ --cov=src --cov-report=html
# Open htmlcov/index.html to see which lines need coverage
```

### FFmpeg Warnings
FFmpeg warnings in tests are expected and can be ignored. Tests mock audio processing and don't actually use FFmpeg.

## Test Maintenance

Tests should be updated when:
1. Adding new features or modules
2. Modifying existing functionality
3. Fixing bugs (add regression tests)
4. Refactoring code (ensure tests still pass)

Aim to maintain:
- At least 85% code coverage
- Fast test execution (< 5 seconds for unit tests)
- No flaky or intermittent failures
- Clear, descriptive test names and docstrings
