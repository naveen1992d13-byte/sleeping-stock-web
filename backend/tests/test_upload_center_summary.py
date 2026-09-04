"""Unit tests for Upload Center today-summary rules. No Mongo/S3 writes."""

from upload_center_summary import (
    balance_rows,
    expected_identities_from_branches,
    summarize_today,
    upload_identity,
)


def _u(**kwargs):
    row = {
        "id": kwargs.get("id"),
        "brand_name": kwargs.get("brand", "Hyundai"),
        "dealer_name": kwargs.get("dealer", "KUN Auto Company PVT LTD"),
        "branch": kwargs.get("branch", "Retteri"),
        "status": kwargs.get("status", "Uploaded"),
        "publish_status": kwargs.get("publish_status", "Waiting"),
        "item_count": kwargs.get("items", 1),
        "total_available_qty": kwargs.get("qty", 1),
        "total_value": kwargs.get("value", 1),
        "created_at": kwargs.get("created_at", "2026-09-04T10:00:00"),
        "upload_time": kwargs.get("upload_time", "10:00:00"),
    }
    return row


EXPECTED = {
    ("Hyundai", "KUN Auto Company PVT LTD", "Retteri"),
    ("Hyundai", "KUN Auto Company PVT LTD", "Chromepet"),
    ("Hyundai", "FPL Automobiles PVT LTD", "Koyambedu"),
    ("Hyundai", "FPL Automobiles PVT LTD", "Vanagaram"),
}


class TestWaitingIsNotCompleted:
    def test_three_waiting_same_branch_completed_zero(self):
        uploads = [
            _u(id="a", items=1, created_at="2026-09-04T10:00:00"),
            _u(id="b", items=1, created_at="2026-09-04T11:00:00"),
            _u(id="c", items=10130, created_at="2026-09-04T12:00:00"),
        ]
        s = summarize_today(uploads, EXPECTED)
        assert s["completedUploads"] == 0
        assert s["published"] == 0
        assert s["pending"] == 3
        assert s["uploadedItems"] == 10130
        assert s["pendingItems"] == 10130
        assert s["publishedItems"] == 0
        assert s["branchesUploaded"] == 1
        assert s["balanceUploads"] == 4

    def test_published_counts_as_completed(self):
        uploads = [
            _u(id="w", items=10, publish_status="Waiting", created_at="2026-09-04T10:00:00"),
            _u(id="p", items=8, publish_status="Published", created_at="2026-09-04T11:00:00"),
        ]
        s = summarize_today(uploads, EXPECTED)
        assert s["completedUploads"] == 1
        assert s["published"] == 1
        assert s["pending"] == 1
        # Latest valid is Published (later), so uploaded = 8; no newer Waiting
        assert s["uploadedItems"] == 8
        assert s["publishedItems"] == 8
        assert s["pendingItems"] == 0
        assert s["balanceUploads"] == 3

    def test_newer_waiting_after_publish_is_pending_not_extra_completed(self):
        uploads = [
            _u(id="p", items=8, publish_status="Published", created_at="2026-09-04T10:00:00"),
            _u(id="w", items=12, publish_status="Waiting", created_at="2026-09-04T12:00:00"),
        ]
        s = summarize_today(uploads, EXPECTED)
        assert s["completedUploads"] == 1
        assert s["uploadedItems"] == 12
        assert s["publishedItems"] == 8
        assert s["pendingItems"] == 12
        assert s["pending"] == 1

    def test_cancelled_and_failed_never_completed(self):
        uploads = [
            _u(id="c", status="Cancelled", publish_status="Waiting", items=99, created_at="2026-09-04T13:00:00"),
            _u(id="f", status="Failed", publish_status="Failed", items=0, created_at="2026-09-04T14:00:00"),
            _u(id="w", publish_status="Waiting", items=5, created_at="2026-09-04T12:00:00"),
        ]
        s = summarize_today(uploads, EXPECTED)
        assert s["completedUploads"] == 0
        assert s["cancelled"] == 1
        assert s["failed"] == 1
        assert s["pending"] == 1
        assert s["uploadedItems"] == 5

    def test_empty_identity_excluded_from_branch_and_item_cards(self):
        uploads = [
            _u(id="m", brand="", dealer="", branch="", items=1, created_at="2026-09-04T10:00:00"),
            _u(id="r", items=10130, created_at="2026-09-04T12:00:00"),
        ]
        s = summarize_today(uploads, EXPECTED)
        assert s["branchesUploaded"] == 1
        assert s["uploadedItems"] == 10130
        assert s["pending"] == 2

    def test_two_dealers_same_branch_name_are_distinct(self):
        uploads = [
            _u(id="1", dealer="KUN Auto Company PVT LTD", branch="Retteri", items=10),
            _u(id="2", dealer="FPL Automobiles PVT LTD", branch="Retteri", items=7, brand="Hyundai"),
        ]
        s = summarize_today(uploads, EXPECTED | {("Hyundai", "FPL Automobiles PVT LTD", "Retteri")})
        assert s["branchesUploaded"] == 2
        assert s["uploadedItems"] == 17
        assert s["completedUploads"] == 0


class TestExpectedFromBranches:
    def test_brand_on_branch_docs(self):
        docs = [
            {"brand": "Hyundai", "dealer": "KUN Auto Company PVT LTD", "name": "Retteri", "status": "active"},
            {"brand_name": "Honda", "dealer": "Other", "name": "X", "status": "active"},
            {"brand": "Hyundai", "dealer": "FPL", "name": "Old", "status": "inactive"},
        ]
        keys = expected_identities_from_branches(docs)
        assert ("Hyundai", "KUN Auto Company PVT LTD", "Retteri") in keys
        assert ("Honda", "Other", "X") in keys
        assert ("Hyundai", "FPL", "Old") not in keys

    def test_balance_modal_waiting_is_pending(self):
        s = summarize_today([_u(id="w", publish_status="Waiting")], EXPECTED)
        rows = balance_rows(EXPECTED, s["completed_keys"])
        assert rows["completed"] == []
        assert len(rows["pending"]) == 4
        assert upload_identity(_u()) == ("Hyundai", "KUN Auto Company PVT LTD", "Retteri")
