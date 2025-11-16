import logging
import json
from typing import Any, Dict


class JsonDetailsFormatter(logging.Formatter):
    """Formatter that prints the message as plain text and a JSON-encoded `details` property.

    Output example:
    INFO: Starting transcription | {"file": "path/to/file.wav", "size_bytes": 12345}
    """

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        details = getattr(record, "details", None)
        if details is None:
            details_str = "{}"
        else:
            try:
                details_str = json.dumps(details, default=str, ensure_ascii=False)
            except Exception:
                details_str = json.dumps({"error": "could not serialize details"})
        return f"{record.levelname}: {message} | {details_str}"


def get_logger(name: str = __name__) -> logging.Logger:
    logger = logging.getLogger(name)
    # Avoid adding multiple handlers if get_logger is called repeatedly
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonDetailsFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


# Convenience functions
def info(message: str, details: Dict[str, Any] | None = None) -> None:
    get_logger().info(message, extra={"details": details})


def warning(message: str, details: Dict[str, Any] | None = None) -> None:
    get_logger().warning(message, extra={"details": details})


def error(message: str, details: Dict[str, Any] | None = None) -> None:
    get_logger().error(message, extra={"details": details})
