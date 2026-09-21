"""Unit tests for testing environment helpers. No live DocumentDB or S3."""
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import testing_runtime as tr


def test_production_default_does_not_prefix(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    assert tr.prefix_business_id("ORHY260712001") == "ORHY260712001"
    assert tr.is_testing_env() is False
    assert tr.origin_query("all") == {}


def test_testing_prefixes_business_ids(monkeypatch):
    monkeypatch.setenv("APP_ENV", "testing")
    assert tr.prefix_business_id("ORHY260712001") == "TS-ORHY260712001"
    assert tr.prefix_business_id("TS-RQHY1") == "TS-RQHY1"
    assert tr.testing_cancel_upload_no("PUHY260705001") == "TS-CNHY260705001"
    assert tr.testing_cancel_upload_no("TS-PUHY260705001") == "TS-CNHY260705001"


def test_assert_env_isolation_testing(monkeypatch):
    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("DB_NAME", "nmts_testing")
    monkeypatch.setenv("NMTS_STORAGE_ENV", "testing")
    tr.assert_env_isolation()


def test_assert_env_isolation_rejects_production_db(monkeypatch):
    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("DB_NAME", "nmts")
    monkeypatch.setenv("NMTS_STORAGE_ENV", "testing")
    try:
        tr.assert_env_isolation()
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "nmts_testing" in str(exc)


def test_production_rejects_testing_storage(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv("DB_NAME", "nmts")
    monkeypatch.setenv("NMTS_STORAGE_ENV", "testing")
    try:
        tr.assert_env_isolation()
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "testing" in str(exc)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv("DB_NAME", "nmts_testing")
    monkeypatch.setenv("NMTS_STORAGE_ENV", "dev")
    try:
        tr.assert_env_isolation()
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "nmts_testing" in str(exc)


def test_snapshot_date_and_origin_query(monkeypatch, tmp_path):
    meta = tmp_path / "snap.json"
    meta.write_text(json.dumps({
        "status": "active",
        "snapshot_version": "20260920T010000Z",
        "business_date_key": "20260919",
    }), encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("NMTS_SNAPSHOT_META_JSON", str(meta))
    assert tr.snapshot_business_date_key() == "20260919"
    query = tr.origin_query("all")
    assert "$or" in query
    snapshot_only = tr.origin_query("snapshot")
    assert snapshot_only["data_origin"] == "snapshot"
    assert snapshot_only["snapshot_version"] == "20260920T010000Z"
    assert tr.origin_query("testing") == {"data_origin": "testing"}


def test_stamp_origin_noop_in_production(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    assert "data_origin" not in tr.stamp_testing_origin({"id": "1"})
