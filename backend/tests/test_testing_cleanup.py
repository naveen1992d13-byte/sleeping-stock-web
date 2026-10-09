"""Testing cleanup dry-run inventory tests. No live S3/DocumentDB."""
import asyncio
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import testing_cleanup as tc
from tests.test_testing_overlay import FakeCollection


class FakeDB(dict):
    def __getitem__(self, name):
        if name not in self:
            super().__setitem__(name, FakeCollection())
        return super().__getitem__(name)


def test_confirm_phrases():
    assert tc.confirm_clear_ok("CLEAR TESTING DATA") is True
    assert tc.confirm_clear_ok("MERGE AND CLEAR TEST DATA") is True
    assert tc.confirm_clear_ok("please clear") is False


def test_cleanup_dry_run_does_not_delete(monkeypatch):
    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("DB_NAME", "nmts_testing")
    monkeypatch.setenv("NMTS_STORAGE_ENV", "testing")
    monkeypatch.delenv("TESTING_LIVE_CLEANUP", raising=False)
    db = FakeDB()
    db["order_headers"] = FakeCollection([
        {"id": "1", "order_number": "TS-OR1", "data_origin": "testing"},
        {"id": "2", "order_number": "ORHY1", "data_origin": "snapshot"},
    ])

    async def fake_s3(limit=5000):
        return {"prefix": "testing/", "object_count": 0, "bytes": 0, "keys_preview": [], "keys": []}

    tc.list_testing_s3_objects = lambda limit=5000: {
        "prefix": "testing/", "object_count": 0, "bytes": 0, "keys_preview": [], "keys": []
    }

    result = asyncio.run(tc.cleanup_testing_data(
        db,
        confirm_text="CLEAR TESTING DATA",
        actor={"id": "u1", "role": "master"},
        operation_id="op-dry",
        dry_run=True,
    ))
    assert result["ok"] is True
    assert result["status"] == "dry_run"
    assert db["order_headers"].rows[0]["order_number"] == "TS-OR1"
    assert db["order_headers"].rows[1]["data_origin"] == "snapshot"
    assert result["receipt"]["inventory"]["retain_snapshot_copies"] is False


def test_cleanup_live_deletes_testing_created_only(monkeypatch):
    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("DB_NAME", "nmts_testing")
    monkeypatch.setenv("NMTS_STORAGE_ENV", "testing")
    monkeypatch.setenv("TESTING_LIVE_CLEANUP", "true")
    db = FakeDB()
    db["order_headers"] = FakeCollection([
        {"id": "1", "order_number": "TS-OR1", "data_origin": "testing"},
        {"id": "2", "order_number": "ORHY1", "data_origin": "snapshot"},
    ])
    db["testing_overlays"] = FakeCollection([{"collection": "products", "production_id": "p1"}])
    db["testing_tombstones"] = FakeCollection([{"collection": "products", "production_id": "p2"}])
    db["testing_audit_receipts"] = FakeCollection()
    tc.list_testing_s3_objects = lambda limit=5000: {
        "prefix": "testing/", "object_count": 0, "bytes": 0, "keys_preview": [], "keys": []
    }
    result = asyncio.run(tc.cleanup_testing_data(
        db,
        confirm_text="CLEAR TESTING DATA",
        actor={"id": "u1", "role": "master"},
        operation_id="op-live",
        dry_run=False,
    ))
    assert result["status"] == "cleaned"
    remaining = {r["id"] for r in db["order_headers"].rows}
    assert remaining == set()
    assert db["testing_overlays"].rows == []
    assert db["testing_tombstones"].rows == []
    assert db["testing_audit_receipts"].rows
