"""Async Production verification polling tests. No live GitHub or Production calls."""
import asyncio
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import testing_verify as tv
from tests.test_testing_overlay import FakeCollection


class FakeDB(dict):
    def __getitem__(self, name):
        if name not in self:
            super().__setitem__(name, FakeCollection())
        return super().__getitem__(name)


def test_verification_polls_until_sha_matches(monkeypatch):
    monkeypatch.setenv("TESTING_VERIFY_POLL_SECONDS", "1")
    monkeypatch.setenv("TESTING_VERIFY_TIMEOUT_SECONDS", "30")
    monkeypatch.delenv("TESTING_LIVE_CLEANUP", raising=False)
    db = FakeDB()
    calls = {"n": 0}

    async def fake_verify(expected_sha):
        calls["n"] += 1
        if calls["n"] < 2:
            return {"ok": False, "status": "failed", "checks": {"sha_match": False}}
        return {"ok": True, "status": "verified", "checks": {"sha_match": True}}

    monkeypatch.setattr(tv.tg, "verify_production_deployment", fake_verify)
    result = asyncio.run(tv.run_verification_loop(
        db,
        "op-poll",
        "abc123",
        {"id": "u1", "role": "master"},
        "MERGE AND CLEAR TEST DATA",
    ))
    assert calls["n"] >= 2
    assert result["result_status"] == tv.STATUS_CLEANUP_DEFERRED
    stored = asyncio.run(tv.load_operation(db, "op-poll"))
    assert stored["result_status"] == tv.STATUS_CLEANUP_DEFERRED


def test_verification_timeout_retains_testing_data(monkeypatch):
    monkeypatch.setenv("TESTING_VERIFY_POLL_SECONDS", "1")
    monkeypatch.setenv("TESTING_VERIFY_TIMEOUT_SECONDS", "1")
    db = FakeDB()

    async def always_fail(expected_sha):
        return {"ok": False, "status": "failed"}

    monkeypatch.setattr(tv.tg, "verify_production_deployment", always_fail)
    result = asyncio.run(tv.run_verification_loop(
        db,
        "op-timeout",
        "abc123",
        {"id": "u1", "role": "master"},
        "MERGE AND CLEAR TEST DATA",
    ))
    assert result["result_status"] == tv.STATUS_TIMEOUT
    assert result["cleanup"]["status"] == tv.STATUS_CLEANUP_RETAINED


def test_ops_db_never_bools_motor_objects():
    class MotorLike:
        def __bool__(self):
            raise NotImplementedError("Database objects do not implement truth value testing")

    raw = MotorLike()
    fallback = object()
    assert tv.ops_db(raw, fallback) is raw
    assert tv.ops_db(None, fallback) is fallback


def test_public_labels_cover_requested_states():
    assert tv.public_label(tv.STATUS_MERGE_SUBMITTED) == "Merge submitted"
    assert tv.public_label(tv.STATUS_WAITING) == "Waiting for Production deployment"
    assert tv.public_label(tv.STATUS_VERIFIED) == "Production verification passed"
    assert "failed" in tv.public_label(tv.STATUS_FAILED).lower()
    assert "timed out" in tv.public_label(tv.STATUS_TIMEOUT).lower()
    assert tv.public_label(tv.STATUS_CLEANUP_COMPLETED) == "Testing cleanup completed"
    assert "retained" in tv.public_label(tv.STATUS_CLEANUP_RETAINED).lower()
    assert "deferred" in tv.public_label(tv.STATUS_CLEANUP_DEFERRED).lower()


def test_retry_does_not_merge_again(monkeypatch):
    db = FakeDB()
    db[tv.ov.OPERATION_COLLECTION] = FakeCollection([{
        "operation_id": "op-1",
        "result_status": tv.STATUS_TIMEOUT,
        "expected_sha": "abc",
        "kind": "github_merge",
    }])
    started = []

    def fake_start(test_db, operation_id, expected_sha, actor, confirm_text):
        started.append((operation_id, expected_sha))

    monkeypatch.setattr(tv, "start_verification", fake_start)
    result = asyncio.run(tv.retry_verification(db, "op-1", {"id": "u1", "role": "master"}))
    assert result["ok"] is True
    assert result["status"] == tv.STATUS_WAITING
    assert started == [("op-1", "abc")]


def _retry(db, status, monkeypatch, started, extra=None):
    row = {
        "operation_id": f"op-{status}",
        "result_status": status,
        "expected_sha": "abc",
        "kind": "github_merge",
    }
    if extra:
        row.update(extra)
    db[tv.ov.OPERATION_COLLECTION] = FakeCollection([row])
    monkeypatch.setattr(tv, "start_verification", lambda *a, **k: started.append(a[1]))
    return asyncio.run(tv.retry_verification(db, row["operation_id"], {"id": "u1", "role": "master"}))


def test_retry_allowed_for_retryable_statuses(monkeypatch):
    for status in (
        tv.STATUS_WAITING,
        tv.STATUS_FAILED,
        tv.STATUS_TIMEOUT,
        tv.STATUS_CLEANUP_RETAINED,
    ):
        db = FakeDB()
        started = []
        result = _retry(db, status, monkeypatch, started)
        assert result["ok"] is True
        assert result["status"] == tv.STATUS_WAITING
        assert started == [f"op-{status}"]
        stored = asyncio.run(tv.load_operation(db, f"op-{status}"))
        assert stored["result_status"] == tv.STATUS_WAITING


def test_retry_rejected_for_blocked_dry_run_and_unknown(monkeypatch):
    for status in ("blocked", "dry_run", "rejected", "not_eligible", "unknown"):
        db = FakeDB()
        started = []
        result = _retry(db, status, monkeypatch, started)
        assert result["ok"] is False
        assert result["error"] == "retry_not_allowed"
        assert result["status"] == status
        assert started == []
        stored = asyncio.run(tv.load_operation(db, f"op-{status}"))
        assert stored["result_status"] == status


def test_retry_idempotent_replay_for_completed_statuses(monkeypatch):
    for status in (tv.STATUS_VERIFIED, tv.STATUS_CLEANUP_DEFERRED, tv.STATUS_CLEANUP_COMPLETED):
        db = FakeDB()
        started = []
        result = _retry(db, status, monkeypatch, started)
        assert result["ok"] is True
        assert result["status"] == "idempotent_replay"
        assert started == []
        stored = asyncio.run(tv.load_operation(db, f"op-{status}"))
        assert stored["result_status"] == status
