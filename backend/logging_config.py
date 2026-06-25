from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv

from backend.paths import ENV_PATH

load_dotenv(ENV_PATH)

SUPPORTED_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR"}
STANDARD_RECORD_KEYS = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}
SENSITIVE_KEYS = {"password", "token", "access_token", "api_key", "authorization"}


class SenteroFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds")
        message = record.getMessage()
        parts = [
            f"ts={timestamp}",
            f"level={record.levelname}",
            f"logger={record.name}",
            f"module={record.module}",
            f"message={quote_value(message)}",
        ]
        for key, value in sorted(extra_fields(record).items()):
            parts.append(f"{key}={quote_value(mask_if_sensitive(key, value))}")
        if record.exc_info:
            parts.append(f"exception={quote_value(self.formatException(record.exc_info))}")
        return " ".join(parts)


def configure_logging() -> None:
    level_name = os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO"
    if level_name not in SUPPORTED_LEVELS:
        level_name = "INFO"
    logging.basicConfig(level=getattr(logging, level_name), handlers=[logging.StreamHandler(sys.stdout)], force=True)
    root = logging.getLogger()
    for handler in root.handlers:
        handler.setFormatter(SenteroFormatter())
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING if level_name != "DEBUG" else logging.INFO)
    get_logger(__name__).info("Logging configured", extra={"component": "logging", "log_level": level_name})


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def is_debug_logging() -> bool:
    return logging.getLogger().isEnabledFor(logging.DEBUG)


def extra_fields(record: logging.LogRecord) -> dict[str, Any]:
    return {key: value for key, value in record.__dict__.items() if key not in STANDARD_RECORD_KEYS and not key.startswith("_")}


def mask_if_sensitive(key: str, value: Any) -> Any:
    lowered = key.lower()
    if any(secret in lowered for secret in SENSITIVE_KEYS):
        return "***" if value else ""
    if isinstance(value, dict):
        return {item_key: mask_if_sensitive(str(item_key), item_value) for item_key, item_value in value.items()}
    return value


def quote_value(value: Any) -> str:
    text = str(value)
    if not text:
        return '""'
    if any(char.isspace() for char in text) or "=" in text:
        return repr(text)
    return text
