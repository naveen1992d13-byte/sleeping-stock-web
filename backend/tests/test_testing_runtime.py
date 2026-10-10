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
    monkeypatch.setenv("TESTING_OVERLAY_MODE", "false")
    monkeypatch.setenv("NMTS_SNAPSHOT_META_JSON", str(meta))
    assert tr.snapshot_business_date_key() == "20260919"
    query = tr.origin_query("all")
    assert "$or" in query
    snapshot_only = tr.origin_query("snapshot")
    assert snapshot_only["data_origin"] == "snapshot"
    assert snapshot_only["snapshot_version"] == "20260920T010000Z"
    assert tr.origin_query("testing") == {"data_origin": "testing"}


def test_overlay_origin_query_does_not_use_snapshot_copy(monkeypatch):
    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("TESTING_OVERLAY_MODE", "true")
    assert tr.origin_query("all") == {}
    assert tr.origin_query("testing").get("_nmts_testing_only") is True
    assert tr.origin_query("snapshot").get("_nmts_base_only") is True
    status = tr.public_runtime_status()
    assert status["overlay_mode"] is True


def test_stamp_origin_noop_in_production(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    assert "data_origin" not in tr.stamp_testing_origin({"id": "1"})


def test_overlay_mode_does_not_freeze_business_date(monkeypatch):
    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("TESTING_OVERLAY_MODE", "true")
    monkeypatch.setenv("TESTING_SNAPSHOT_DATE_KEY", "20260920")
    assert tr.snapshot_business_date_key() == "20260920"
    assert tr.should_freeze_business_date() is False


def test_snapshot_mode_still_freezes_business_date(monkeypatch):
    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("TESTING_OVERLAY_MODE", "false")
    monkeypatch.setenv("TESTING_SNAPSHOT_DATE_KEY", "20260920")
    assert tr.should_freeze_business_date() is True
    assert tr.snapshot_business_date_key() == "20260920"
