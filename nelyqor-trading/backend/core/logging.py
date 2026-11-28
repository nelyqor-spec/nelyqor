"""Journalisation structurée JSON avec masquage des secrets."""
from __future__ import annotations

import json
import logging
import re
import sys
from typing import Any

_SECRET_KEYS = re.compile(r"(secret|api[_-]?key|signature|password|token|authorization|cookie)", re.I)
_REDACTED = "***"
_registered_secrets: set[str] = set()


def register_secret(value: str | None) -> None:
    """Toute valeur enregistrée ici est masquée dans tous les logs."""
    if value and len(value) >= 6:
        _registered_secrets.add(value)


def redact(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: (_REDACTED if _SECRET_KEYS.search(str(k)) else redact(v)) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [redact(v) for v in obj]
    if isinstance(obj, str):
        for s in _registered_secrets:
            obj = obj.replace(s, _REDACTED)
        return obj
    return obj


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "lvl": record.levelname,
            "mod": record.name,
            "msg": redact(record.getMessage()),
        }
        extra = getattr(record, "data", None)
        if extra is not None:
            payload["data"] = redact(extra)
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(level: str = "INFO") -> None:
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [h]
    root.setLevel(level)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
