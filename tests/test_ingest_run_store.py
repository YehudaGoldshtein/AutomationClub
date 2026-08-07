"""Tests for SqlIngestRunStore — the frontend-trackable ingest outcome.

The dashboard generates a serial (run_ref) at upload, dispatches with it, and polls
this store to learn: running → success | rejected | error, with reason + counts.
"""
from __future__ import annotations

import sqlalchemy

from inventory_sync.log import get
from inventory_sync.persistence.ingest_run_store import SqlIngestRunStore

C = "maxbaby"


def _store():
    engine = sqlalchemy.create_engine("sqlite:///:memory:")
    s = SqlIngestRunStore(engine=engine, logger=get("test"))
    s.create_schema()
    return s


class TestIngestRunStore:
    def test_start_creates_running(self):
        s = _store()
        s.start("r1", C, "http://blob")
        rec = s.get("r1")
        assert rec.status == "running"
        assert rec.customer_id == C and rec.blob_url == "http://blob"
        assert rec.finished_at is None and rec.started_at is not None

    def test_finish_success_records_counts(self):
        s = _store()
        s.start("r1", C, "b")
        s.finish_success("r1", created=10, archived=5, skipped_existing=3, errors=1)
        rec = s.get("r1")
        assert rec.status == "success"
        assert (rec.created, rec.archived, rec.skipped_existing, rec.errors) == (10, 5, 3, 1)
        assert rec.finished_at is not None

    def test_finish_rejected_records_reason(self):
        s = _store()
        s.start("r1", C, "b")
        s.finish_rejected("r1", "missing required column(s): availability (מלאי)")
        rec = s.get("r1")
        assert rec.status == "rejected"
        assert "מלאי" in rec.reason and rec.finished_at is not None

    def test_finish_error_records_reason(self):
        s = _store()
        s.start("r1", C, "b")
        s.finish_error("r1", "boom")
        rec = s.get("r1")
        assert rec.status == "error" and rec.reason == "boom"

    def test_get_unknown_is_none(self):
        assert _store().get("nope") is None

    def test_start_is_idempotent_and_resets(self):
        s = _store()
        s.start("r1", C, "b")
        s.finish_success("r1", created=1, archived=0, skipped_existing=0, errors=0)
        s.start("r1", C, "b2")   # a retry reuses the same serial
        rec = s.get("r1")
        assert rec.status == "running" and rec.blob_url == "b2"
        assert rec.finished_at is None and rec.created is None and rec.reason is None

    def test_scoped_by_run_ref_only(self):
        s = _store()
        s.start("r1", C, "b1")
        s.start("r2", C, "b2")
        s.finish_rejected("r1", "bad")
        assert s.get("r1").status == "rejected"
        assert s.get("r2").status == "running"   # untouched
