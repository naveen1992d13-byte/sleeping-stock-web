"""Selection guards for the Testing-only Product History archive."""
from testing_product_history_archive import (
    assert_no_production_rows,
    is_oct10_testing_upload,
    is_testing_owned,
    production_leak_reason,
    upload_center_ist_date,
)


def test_oct10_testing_upload_accepted():
    doc = {
        "upload_no": "TS-PUHY261010002",
        "data_origin": "testing",
        "publish_status": "Published",
        "branch": "Koyambedu",
        "date_key": "20260920",
        "created_at": "2026-10-10T13:38:23+05:30",
    }
    assert is_testing_owned(doc)
    assert is_oct10_testing_upload(doc)
    assert production_leak_reason(doc) is None


def test_chrompet_production_is_rejected():
    doc = {
        "upload_no": "PUHY261010001",
        "data_origin": "production",
        "publish_status": "Published",
        "branch": "Chrompet",
        "dealer_name": "KUN Auto Company Pvt Ltd",
        "date_key": "20261010",
    }
    assert is_oct10_testing_upload(doc) is False
    assert production_leak_reason(doc)


def test_vanagaram_oct3_testing_is_not_oct10():
    doc = {
        "upload_no": "TS-PUHY261003001",
        "data_origin": "testing",
        "publish_status": "Published",
        "branch": "Vanagaram",
        "date_key": "20260920",
        "created_at": "2026-10-03T13:28:44+05:30",
    }
    assert is_testing_owned(doc)
    assert is_oct10_testing_upload(doc) is False


def test_cancelled_testing_upload_excluded():
    doc = {
        "upload_no": "TS-CNHY261010003",
        "data_origin": "testing",
        "publish_status": "Cancelled",
        "branch": "Koyambedu",
        "created_at": "2026-10-10T13:39:28+05:30",
    }
    assert is_oct10_testing_upload(doc) is False


def test_frozen_date_key_is_ignored_for_selection():
    frozen = {
        "upload_no": "TS-PUHY261010002",
        "data_origin": "testing",
        "publish_status": "Published",
        "date_key": "20260920",
        "created_at": "2026-10-10T13:38:23+05:30",
    }
    assert upload_center_ist_date(frozen) == "20261010"
    assert is_oct10_testing_upload(frozen) is True
    snapshot_key_only = {
        "upload_no": "TS-PUHY260920099",
        "data_origin": "testing",
        "publish_status": "Published",
        "date_key": "20261010",
        "created_at": "2026-09-20T09:00:00+05:30",
    }
    assert upload_center_ist_date(snapshot_key_only) == "20260920"
    assert is_oct10_testing_upload(snapshot_key_only) is False


def test_assert_stops_when_production_row_present():
    try:
        assert_no_production_rows([
            {"upload_no": "TS-PUHY261010005", "data_origin": "testing", "branch": "PR78-FIX-BRANCH"},
            {"upload_no": "PUHY261010001", "data_origin": "production", "branch": "Chrompet"},
        ])
    except RuntimeError as exc:
        assert "STOP" in str(exc)
        return
    assert False, "expected STOP"
