"""Phase 1 B/C: Master Admin protection + fresh-start reset safety helpers."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import roles as role_defs
import testing_cleanup as tc
from tests.test_testing_overlay import FakeCollection


class FakeDB(dict):
    def __getitem__(self, name):
        if name not in self:
            super().__setitem__(name, FakeCollection())
        return super().__getitem__(name)

    async def list_collection_names(self):
        return list(self.keys())


def test_protected_master_covers_testing_and_role():
    assert role_defs.is_protected_master_admin_doc(
        {"role": "master_admin", "email": "testing.master@sleepingstock.in", "is_testing_master": True}
    )
    assert role_defs.is_protected_master_admin_doc(
        {"role": "user", "email": "testing.master@sleepingstock.in"}
    )
    assert role_defs.is_mirrored_production_master_admin(
        {"email": "admin@sleepingstock.in", "data_origin": "production"}
    )


def test_cleanup_preserves_testing_master(monkeypatch):
    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("DB_NAME", "nmts_testing")
    monkeypatch.setenv("NMTS_STORAGE_ENV", "testing")
    monkeypatch.setenv("TESTING_LIVE_CLEANUP", "true")

    db = FakeDB()
    db["users"] = FakeCollection([
        {
            "id": "ma1",
            "email": "testing.master@sleepingstock.in",
            "role": "master_admin",
            "is_testing_master": True,
            "data_origin": "testing",
        },
        {
            "id": "u2",
            "email": "other@example.com",
            "role": "user",
            "data_origin": "testing",
        },
    ])
    db["brands"] = FakeCollection([{"id": "b1", "name": "X", "data_origin": "testing"}])
    db["testing_overlays"] = FakeCollection()
    db["testing_tombstones"] = FakeCollection()
    db["testing_audit_receipts"] = FakeCollection()
    tc.list_testing_s3_objects = lambda limit=5000: {
        "prefix": "testing/", "object_count": 0, "bytes": 0, "keys_preview": [], "keys": []
    }

    async def fake_reisolate(test_db):
        return {"tombstoned": {"brands": 0}, "hide_mirrored": {"tombstoned": 0, "ids": []}}

    monkeypatch.setattr(tc, "reisolate_testing_hub_from_production", fake_reisolate)

    result = asyncio.run(tc.cleanup_testing_data(
        db,
        confirm_text="CLEAR TESTING DATA",
        actor={"id": "ma1", "role": "master_admin"},
        operation_id="op-ma-protect",
        dry_run=False,
    ))
    assert result["status"] == "cleaned"
    emails = {r["email"] for r in db["users"].rows}
    assert emails == {"testing.master@sleepingstock.in"}
    assert db["brands"].rows == []


def test_reset_script_isolation_guards():
    """Import-level sanity: reset script aborts on wrong DB/storage names."""
    root = Path(__file__).resolve().parents[2]
    script = (root / "deploy/testing/reset_testing_fresh_start.py").read_text(encoding="utf-8")
    assert 'DB_NAME={db_name!r} (expected nmts_testing)' in script or "expected nmts_testing" in script
    assert "expected testing" in script
    assert "testing.master@sleepingstock.in" in script
    assert "NMTS_TESTING_MASTER_PASSWORD" in script
    assert "never touches" not in script.lower() or "ABORT" in script
