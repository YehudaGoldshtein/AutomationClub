"""Batching Axiom sink for the stdlib logger.

Short-lived GH Actions runs (~3 min) make this easy: buffer every record in
memory, POST them all at process exit via atexit. No background thread, no
lost logs on graceful shutdown, and one HTTPS call per run instead of per
event. Failures are swallowed — logging must never crash the app.
"""
from __future__ import annotations

import atexit
import json
import logging
import os
from typing import Any

import httpx


class AxiomBatchHandler(logging.Handler):
    def __init__(
        self,
        *,
        api_url: str,
        api_token: str,
        dataset: str,
        service: str,
        timeout: float = 10.0,
    ):
        super().__init__()
        self._endpoint = f"{api_url.rstrip('/')}/v1/datasets/{dataset}/ingest"
        self._headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        }
        self._service = service
        self._timeout = timeout
        self._buffer: list[dict[str, Any]] = []

    def emit(self, record: logging.LogRecord) -> None:
        payload: dict[str, Any] = {
            "_time": record.created,  # Axiom reads Unix seconds or ISO-8601
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "service": self._service,
        }
        ctx = getattr(record, "context", None)
        if isinstance(ctx, dict):
            for k, v in ctx.items():
                payload[k] = v
        if record.exc_info:
            payload["exc"] = self.format(record) if self.formatter else None
        self._buffer.append(payload)

    def flush(self) -> None:
        if not self._buffer:
            return
        batch = self._buffer
        self._buffer = []
        try:
            httpx.post(self._endpoint, headers=self._headers, json=batch, timeout=self._timeout)
        except Exception:
            # Logging must never crash the app. Silent drop on failure.
            pass


def attach_if_configured(root_logger: logging.Logger, service: str) -> AxiomBatchHandler | None:
    """Attach an AxiomBatchHandler when AXIOM_API_TOKEN is set in the environment.

    Registers flush() with atexit so a clean process exit ships the batch.
    No-op when the token isn't configured — safe to call in local dev.
    """
    token = os.environ.get("AXIOM_API_TOKEN", "").strip()
    dataset = os.environ.get("AXIOM_DATASET", "").strip()
    api_url = os.environ.get("AXIOM_API_URL", "https://api.axiom.co").strip()
    if not (token and dataset):
        return None
    handler = AxiomBatchHandler(
        api_url=api_url, api_token=token, dataset=dataset, service=service
    )
    root_logger.addHandler(handler)
    atexit.register(handler.flush)
    return handler
