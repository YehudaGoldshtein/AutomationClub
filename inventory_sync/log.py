"""Structured logger behind a Protocol so backing sinks can be swapped (see ARCHITECTURE.md)."""
from __future__ import annotations

import json
import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Any, Protocol


class Logger(Protocol):
    def debug(self, event: str, **context: Any) -> None: ...
    def info(self, event: str, **context: Any) -> None: ...
    def warning(self, event: str, **context: Any) -> None: ...
    def error(self, event: str, **context: Any) -> None: ...
    def exception(self, event: str, **context: Any) -> None: ...
    def bind(self, **context: Any) -> "Logger": ...


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        ctx = getattr(record, "context", None)
        if isinstance(ctx, dict):
            for k, v in ctx.items():
                payload[k] = v
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class _HumanFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ts = self.formatTime(record, "%H:%M:%S")
        ctx = getattr(record, "context", None)
        ctx_str = ""
        if isinstance(ctx, dict) and ctx:
            ctx_str = " " + " ".join(f"{k}={v}" for k, v in ctx.items())
        line = f"{ts} {record.levelname:<8} {record.name} {record.getMessage()}{ctx_str}"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


class StdlibLogger:
    def __init__(self, underlying: logging.Logger, context: dict[str, Any] | None = None):
        self._log = underlying
        self._context = context or {}

    def _emit(self, level: int, event: str, exc_info: bool = False, **context: Any) -> None:
        merged = {**self._context, **context}
        self._log.log(level, event, exc_info=exc_info, extra={"context": merged})

    def debug(self, event: str, **context: Any) -> None:
        self._emit(logging.DEBUG, event, **context)

    def info(self, event: str, **context: Any) -> None:
        self._emit(logging.INFO, event, **context)

    def warning(self, event: str, **context: Any) -> None:
        self._emit(logging.WARNING, event, **context)

    def error(self, event: str, **context: Any) -> None:
        self._emit(logging.ERROR, event, **context)

    def exception(self, event: str, **context: Any) -> None:
        self._emit(logging.ERROR, event, exc_info=True, **context)

    def bind(self, **context: Any) -> "StdlibLogger":
        return StdlibLogger(self._log, {**self._context, **context})


_ROOT_NAME = "inventory_sync"


def configure(log_dir: Path | str = "logs", level: str = "DEBUG") -> Logger:
    """Configure application-wide logging. Call once at process startup."""
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger(_ROOT_NAME)
    root.setLevel(level.upper())
    root.handlers.clear()
    root.propagate = False

    file_handler = logging.handlers.TimedRotatingFileHandler(
        log_dir / f"{_ROOT_NAME}.log",
        when="midnight",
        backupCount=30,
        encoding="utf-8",
    )
    file_handler.setFormatter(_JsonFormatter())
    root.addHandler(file_handler)

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(_HumanFormatter())
    root.addHandler(stdout_handler)

    return StdlibLogger(root)


def get(name: str | None = None) -> Logger:
    """Get a module-level logger. Safe to call before `configure()`."""
    underlying = logging.getLogger(f"{_ROOT_NAME}.{name}" if name else _ROOT_NAME)
    return StdlibLogger(underlying)
