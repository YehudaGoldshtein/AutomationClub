"""Contract tests for the Logger infrastructure."""
from __future__ import annotations

import json
from pathlib import Path

from inventory_sync.log import configure, get


def _read_last_log_line(log_dir: Path) -> dict:
    log_file = log_dir / "inventory_sync.log"
    lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert lines, "log file was empty"
    return json.loads(lines[-1])


def test_configure_creates_log_dir_and_file(tmp_path: Path):
    log_dir = tmp_path / "logs"
    log = configure(log_dir=log_dir)
    log.info("hello")

    assert log_dir.is_dir()
    assert (log_dir / "inventory_sync.log").is_file()


def test_file_output_is_structured_json(tmp_path: Path):
    log_dir = tmp_path / "logs"
    log = configure(log_dir=log_dir)
    log.info("test_event", key="value", n=42)

    payload = _read_last_log_line(log_dir)
    assert payload["event"] == "test_event"
    assert payload["key"] == "value"
    assert payload["n"] == 42
    assert payload["level"] == "INFO"
    assert payload["logger"] == "inventory_sync"
    assert "ts" in payload


def test_bind_merges_context_into_future_logs(tmp_path: Path):
    log_dir = tmp_path / "logs"
    root = configure(log_dir=log_dir)
    bound = root.bind(run_id="xyz")
    bound.info("step", items=3)

    payload = _read_last_log_line(log_dir)
    assert payload["run_id"] == "xyz"
    assert payload["items"] == 3


def test_bind_does_not_mutate_original_logger(tmp_path: Path):
    log_dir = tmp_path / "logs"
    root = configure(log_dir=log_dir)
    root.bind(run_id="xyz")  # discarded
    root.info("no_context_should_leak")

    payload = _read_last_log_line(log_dir)
    assert "run_id" not in payload


def test_exception_captures_traceback(tmp_path: Path):
    log_dir = tmp_path / "logs"
    log = configure(log_dir=log_dir)
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        log.exception("failed", sku="X-1")

    payload = _read_last_log_line(log_dir)
    assert payload["event"] == "failed"
    assert payload["level"] == "ERROR"
    assert payload["sku"] == "X-1"
    assert "RuntimeError" in payload["exc"]
    assert "boom" in payload["exc"]


def test_get_returns_namespaced_logger(tmp_path: Path):
    configure(log_dir=tmp_path / "logs")
    child = get("scraper")
    child.info("child_event")

    payload = _read_last_log_line(tmp_path / "logs")
    assert payload["logger"] == "inventory_sync.scraper"


def test_levels_respected(tmp_path: Path):
    log_dir = tmp_path / "logs"
    log = configure(log_dir=log_dir, level="WARNING")
    log.debug("should_not_appear")
    log.info("should_not_appear_either")
    log.warning("should_appear")

    payload = _read_last_log_line(log_dir)
    assert payload["event"] == "should_appear"
    assert payload["level"] == "WARNING"
