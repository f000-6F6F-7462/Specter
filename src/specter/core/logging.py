"""Process-wide logging with plain text or JSON lines output."""

import json
import logging
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

TEXT_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
# httpx logs every request's URL at INFO, and go2rtc accepts a camera's source, password included,
# only in the URL; Docker keeps these logs on disk, so this logger only reports warnings.
CREDENTIAL_LEAKING_LOGGER_NAME = "httpx"

# Attributes that every LogRecord has; anything else was passed through ``extra=``.
STANDARD_RECORD_ATTRIBUTES = frozenset(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


class LogFormat(StrEnum):
    """Output format of log lines."""

    TEXT = "text"
    JSON = "json"


class JsonLogFormatter(logging.Formatter):
    """Formats each record as one JSON object, including fields passed through ``extra=``."""

    def format(self, record: logging.LogRecord) -> str:
        """Returns the record as a single-line JSON object."""
        payload: dict[str, Any] = {
            "logged_at": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update(
            (key, value)
            for key, value in vars(record).items()
            if key not in STANDARD_RECORD_ATTRIBUTES and not key.startswith("_")
        )
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, separators=(",", ":"))


def configure_logging(level: str, log_format: LogFormat) -> None:
    """Replaces the root logger's handlers with a single standard-error handler."""
    handler = logging.StreamHandler()
    if log_format is LogFormat.JSON:
        handler.setFormatter(JsonLogFormatter())
    else:
        handler.setFormatter(logging.Formatter(TEXT_FORMAT))
    root_logger = logging.getLogger()
    root_logger.handlers[:] = [handler]
    root_logger.setLevel(level.upper())
    logging.getLogger(CREDENTIAL_LEAKING_LOGGER_NAME).setLevel(logging.WARNING)
    logging.captureWarnings(True)
