"""Structured JSON logger for Lambda functions."""

import json
import logging
import os
import sys


# LogRecord attributes we must not clobber via `extra=` (stdlib raises if we do)
_RESERVED = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "taskName", "message", "asctime",
}


class StructuredLogger:
    """Adapter so handlers can call `log.info("msg", key=value, ...)`.

    The stdlib Logger only accepts a fixed set of kwargs (exc_info, stack_info,
    extra, ...) — arbitrary kwargs raise TypeError. This wrapper routes extra
    kwargs into `extra={...}`, which JsonFormatter then renders as JSON fields.
    """

    def __init__(self, logger: logging.Logger):
        self._logger = logger

    def _emit(self, level: int, msg: str, **kwargs) -> None:
        exc_info = kwargs.pop("exc_info", None)
        extra = {}
        for key, value in kwargs.items():
            # Prefix any key that would collide with a reserved LogRecord attr
            extra[f"x_{key}" if key in _RESERVED else key] = value
        self._logger.log(level, msg, exc_info=exc_info, extra=extra)

    def debug(self, msg: str, **kwargs) -> None:
        self._emit(logging.DEBUG, msg, **kwargs)

    def info(self, msg: str, **kwargs) -> None:
        self._emit(logging.INFO, msg, **kwargs)

    def warning(self, msg: str, **kwargs) -> None:
        self._emit(logging.WARNING, msg, **kwargs)

    def error(self, msg: str, **kwargs) -> None:
        self._emit(logging.ERROR, msg, **kwargs)


def get_logger(name: str) -> StructuredLogger:
    """
    Return a JSON-formatted structured logger.

    CloudWatch Insights parses JSON well; makes queries easy.
    Example log: {"level":"INFO","msg":"post created","post_id":"post_123"}
    """
    logger = logging.getLogger(name)
    if not logger.handlers:
        level = os.environ.get("LOG_LEVEL", "INFO").upper()
        logger.setLevel(level)

        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.propagate = False

    return StructuredLogger(logger)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "level": record.levelname,
            "msg": record.getMessage(),
            "logger": record.name,
        }
        # Include any extra fields passed to logger
        for key, value in record.__dict__.items():
            if key in payload or key.startswith("_"):
                continue
            if key in (
                "args", "msg", "levelname", "levelno", "pathname", "filename",
                "module", "exc_info", "exc_text", "stack_info", "lineno",
                "funcName", "created", "msecs", "relativeCreated", "thread",
                "threadName", "processName", "process", "name", "taskName",
            ):
                continue
            try:
                json.dumps(value)  # ensure serializable
                payload[key] = value
            except (TypeError, ValueError):
                payload[key] = str(value)

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload)
