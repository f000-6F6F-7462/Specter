"""Standard-library logging configuration."""

import json
import logging
import logging.config
from datetime import UTC, datetime
from typing import Any

# Attributes the logging machinery puts on every LogRecord. Anything else was passed
# by the caller via ``extra=`` and is worth emitting.
_RESERVED = frozenset(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName"}

_TEXT_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"


class JsonFormatter(logging.Formatter):
    """One compact JSON object per line; ``extra=`` fields are merged in.

    stdlib has no JSON formatter, so this stays hand-rolled — small and dependency-free.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        payload.update(
            (key, value)
            for key, value in vars(record).items()
            if key not in _RESERVED and not key.startswith("_")
        )
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)
        return json.dumps(payload, default=str, separators=(",", ":"))


def configure_logging(level: str = "INFO", *, json_output: bool = False) -> None:
    """Install a single stderr handler on the root logger. Safe to call more than once."""
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "text": {"format": _TEXT_FORMAT, "datefmt": "%H:%M:%S"},
                "json": {"()": JsonFormatter},
            },
            "handlers": {
                "stderr": {
                    "class": "logging.StreamHandler",
                    "stream": "ext://sys.stderr",
                    "formatter": "json" if json_output else "text",
                },
            },
            # Per-library levels go here.
            "loggers": {},
            "root": {"level": level.upper(), "handlers": ["stderr"]},
        }
    )
    logging.captureWarnings(True)
