"""Unit tests for logger.py module."""
import logging
import json
import pytest
from src.logger import JsonDetailsFormatter, get_logger, info, warning, error


class TestJsonDetailsFormatter:
    """Tests for JsonDetailsFormatter class."""
    
    def test_format_with_details(self):
        """Test formatting a log record with details."""
        formatter = JsonDetailsFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="Test message",
            args=(),
            exc_info=None
        )
        record.details = {"key": "value", "count": 42}
        
        result = formatter.format(record)
        
        assert "INFO: Test message" in result
        assert '{"key": "value", "count": 42}' in result
    
    def test_format_without_details(self):
        """Test formatting a log record without details."""
        formatter = JsonDetailsFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.WARNING,
            pathname="",
            lineno=0,
            msg="Warning message",
            args=(),
            exc_info=None
        )
        
        result = formatter.format(record)
        
        assert "WARNING: Warning message" in result
        assert "{}" in result
    
    def test_format_with_complex_details(self):
        """Test formatting with nested details."""
        formatter = JsonDetailsFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname="",
            lineno=0,
            msg="Error occurred",
            args=(),
            exc_info=None
        )
        record.details = {
            "error": "File not found",
            "metadata": {"file": "test.txt", "line": 10}
        }
        
        result = formatter.format(record)
        
        assert "ERROR: Error occurred" in result
        # Verify it's valid JSON
        details_part = result.split(" | ")[1]
        parsed = json.loads(details_part)
        assert parsed["error"] == "File not found"
        assert parsed["metadata"]["file"] == "test.txt"
    
    def test_format_with_unserializable_details(self):
        """Test handling of unserializable details."""
        formatter = JsonDetailsFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="Test",
            args=(),
            exc_info=None
        )
        
        # Create an unserializable object
        class UnserializableClass:
            def __repr__(self):
                raise Exception("Cannot serialize")
        
        record.details = {"obj": UnserializableClass()}
        
        result = formatter.format(record)
        
        # Should still format without crashing
        assert "INFO: Test" in result


class TestGetLogger:
    """Tests for get_logger function."""
    
    def test_get_logger_returns_logger(self):
        """Test that get_logger returns a logger instance."""
        logger = get_logger("test_module")
        assert isinstance(logger, logging.Logger)
        assert logger.name == "test_module"
    
    def test_get_logger_has_handler(self):
        """Test that logger has a handler configured."""
        logger = get_logger("test_with_handler")
        assert len(logger.handlers) > 0
        assert isinstance(logger.handlers[0], logging.StreamHandler)
    
    def test_get_logger_has_formatter(self):
        """Test that logger has JsonDetailsFormatter."""
        logger = get_logger("test_formatter")
        handler = logger.handlers[0]
        assert isinstance(handler.formatter, JsonDetailsFormatter)
    
    def test_get_logger_level_is_info(self):
        """Test that logger level is set to INFO."""
        logger = get_logger("test_level")
        assert logger.level == logging.INFO
    
    def test_get_logger_no_duplicate_handlers(self):
        """Test that calling get_logger multiple times doesn't add duplicate handlers."""
        logger1 = get_logger("test_duplicate")
        initial_handler_count = len(logger1.handlers)
        
        logger2 = get_logger("test_duplicate")
        
        # Should be the same logger instance
        assert logger1 is logger2
        # Handler count should not increase
        assert len(logger2.handlers) == initial_handler_count


class TestConvenienceFunctions:
    """Tests for convenience logging functions."""
    
    def test_info_function(self, caplog):
        """Test info convenience function."""
        with caplog.at_level(logging.INFO):
            info("Test info message", {"detail": "value"})
        
        assert "Test info message" in caplog.text
    
    def test_warning_function(self, caplog):
        """Test warning convenience function."""
        with caplog.at_level(logging.WARNING):
            warning("Test warning", {"code": 123})
        
        assert "Test warning" in caplog.text
    
    def test_error_function(self, caplog):
        """Test error convenience function."""
        with caplog.at_level(logging.ERROR):
            error("Test error", {"exception": "RuntimeError"})
        
        assert "Test error" in caplog.text
    
    def test_functions_with_none_details(self, caplog):
        """Test convenience functions with None details."""
        with caplog.at_level(logging.INFO):
            info("Message without details", None)
            warning("Warning without details", None)
            error("Error without details", None)
        
        assert "Message without details" in caplog.text
        assert "Warning without details" in caplog.text
        assert "Error without details" in caplog.text


class TestLoggerIntegration:
    """Integration tests for logger functionality."""
    
    def test_logger_outputs_correct_format(self, capsys):
        """Test that logger outputs in expected format."""
        logger = get_logger("integration_test")
        logger.info("Integration test", extra={"details": {"key": "value"}})
        
        # Check stderr for the formatted output
        captured = capsys.readouterr()
        assert "INFO: Integration test" in captured.err
        assert '{"key": "value"}' in captured.err
    
    def test_multiple_log_calls(self, capsys):
        """Test multiple sequential log calls."""
        logger = get_logger("multi_test")
        
        logger.info("First message", extra={"details": {"id": 1}})
        logger.warning("Second message", extra={"details": {"id": 2}})
        logger.error("Third message", extra={"details": {"id": 3}})
        
        captured = capsys.readouterr()
        log_output = captured.err
        
        assert "First message" in log_output
        assert "Second message" in log_output
        assert "Third message" in log_output
        assert '"id": 1' in log_output
        assert '"id": 2' in log_output
        assert '"id": 3' in log_output
