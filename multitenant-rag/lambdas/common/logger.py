"""Structured JSON logger for Lambda functions."""

import json
import logging
import os
import sys


def get_logger(name: str) -> logging.Logger:
    """
    Return a JSON-formatted logger.

    CloudWatch Insights parses JSON well; makes queries easy.
    Example log: {"level":"INFO","msg":"post created","post_id":"post_123"}
    """
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger  # already configured

    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logger.setLevel(level)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.propagate = False
    return logger


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
