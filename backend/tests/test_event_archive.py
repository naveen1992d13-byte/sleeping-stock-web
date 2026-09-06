"""Event-driven archive outbox tests (FakeDB + FakeS3, no production Mongo deletes)."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

TMP_STORE = tempfile.mkdtemp(prefix="nmts-event-arc-")
os.environ["NMTS_LOCAL_OBJECT_STORE"] = TMP_STORE
os.environ["NMTS_STORAGE_ENV"] = "test"
os.environ["ARCHIVE_PRUNE_ENABLED"] = "false"
os.environ["ARCHIVE_SCHEDULER_ENABLED"] = "false"
os.environ.pop("AWS_ACCESS_KEY_ID", None)
os.environ.pop("AWS_SECRET_ACCESS_KEY", None)
os.environ.pop("NMTS_S3_BUCKET", None)

import s3_storage  # noqa: E402
import archive_keys as ak  # noqa: E402
import archive_manifest as am  # noqa: E402
import archive_outbox as ao  # noqa: E402
import event_archive as ea  # noqa: E402
import hybrid_history as hh  # noqa: E402
import hybrid_order_history as hoh  # noqa: E402
import hybrid_request_history as hrh  # noqa: E402
from test_hybrid_storage import FakeDB, _FakeS3Mode  # noqa: E402


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture(autouse=True)
def _reset_storage():
    s3_storage.reset_storage_for_tests()
    yield
    s3_storage.reset_storage_for_tests()


def _seed_upload(db, upload_id="up-1", date_iso="2026-09-04", status="Published"):
    db.uploads.docs.append(
        {
            "id": upload_id,
            "upload_no": "PU26090401",
            "upload_type": "product",
            "date_key": date_iso.replace("-", ""),
            "created_at": f"{date_iso}T10:00:00+05:30",
            "brand_name": "Hyundai",
            "dealer_name": "DealerA",
            "branch": "B1",
            "publish_status": status,
            "status": "Ready to Send" if status == "Published" else status,
            "file_size": 12,
            "sha256": s3_storage.sha256_bytes(b"excel-bytes"),
        }
    )
    db.products.docs.append(
        {
            "id": "p1",
            "upload_id": upload_id,
            "part_number": "PN-1",
            "item_name": "Pad",
            "quantity": 2,
            "available_qty_number": 2,
            "total_value_number": 20,
            "brand_name": "Hyundai",
            "dealer_name": "DealerA",
            "branch": "B1",
            "publish_status": "Published",
            "active_date_key": date_iso.replace("-", ""),
            "is_active_today": True,
        }
    )
    db.batch_summaries.docs.append(
        {
            "upload_id": upload_id,
            "brand_name": "Hyundai",
            "dealer_name": "DealerA",
            "branch": "B1",
            "active_date_key": date_iso.replace("-", ""),
            "total_item": 1,
        }
    )


def test_archive_keys_current_vs_cancelled():
    cur = ak.product_hub_products_key("2026-09-04", "up-1", cancelled=False)
    can = ak.product_hub_products_key("2026-09-04", "up-1", cancelled=True)
    assert "/current/up-1/products.jsonl.gz" in cur
    assert "/cancelled/up-1/products.jsonl.gz" in can
    assert cur != can
    assert ak.upload_original_key("2026-09-04", "up-1").endswith("/current/up-1/original.xlsx")
    assert "test/orders/" in ak.order_package_key("2026-09-04", "ord-1")
    assert "test/requests/" in ak.request_package_key("2026-09-04", "req-1")


def test_publish_event_archive_and_history_reads_s3():
    async def _go():
        db = FakeDB()
        _seed_upload(db)
        with _FakeS3Mode():
            result = await ea.handle_publish_completed(db, {"upload_id": "up-1"})
            assert result["status"] in {"verified", "already_verified", "already_archived"}
            man = result["manifest"]
            assert man["status"] == am.STATUS_VERIFIED
            assert man["entity_id"] == "up-1"
            assert man["lifecycle_status"] == am.LIFECYCLE_ACTIVE
            assert "/product-hub/2026-09-04/current/up-1/" in man["storage_key"]
            hist = await hh.read_product_history(db, date_key="20260904", brand="Hyundai", hot_days=1)
            # Today-ish hot window may prefer Mongo; force cold read by hot_days=1 with past date
            assert any(r.get("part_number") == "PN-1" for r in hist["rows"]) or hist.get("total", 0) >= 1
            cold = await hh.read_product_history(db, date_key="20260904", brand="Hyundai", hot_days=1)
            # Even if Mongo hot, S3 object exists and is readable
            data, _ = s3_storage.get_storage().download_bytes(man["storage_key"])
            assert data
            assert db.analytics_stock_daily_snapshots.docs  # companions kept in Mongo
            # Cold path: pretend Mongo gone
            db.products.docs = []
            hist2 = await hh.read_product_history(db, date_key="20260904", brand="Hyundai", hot_days=1)
            assert any(r.get("part_number") == "PN-1" for r in hist2["rows"])
            assert hist2["sources"]["20260904"] == "s3"

    _run(_go())


def test_same_day_republish_new_immutable_and_supersede():
    async def _go():
        db = FakeDB()
        _seed_upload(db, "up-old")
        _seed_upload(db, "up-new")
        db.products.docs[-1]["part_number"] = "PN-NEW"
        with _FakeS3Mode():
            first = await ea.handle_publish_completed(db, {"upload_id": "up-old"})
            second = await ea.handle_publish_completed(db, {"upload_id": "up-new"})
            assert first["manifest"]["storage_key"] != second["manifest"]["storage_key"]
            sup = await ea.handle_publish_superseded(
                db, {"upload_id": "up-old", "superseded_by": "up-new", "reason": "same-day republish"}
            )
            assert sup["status"] == "verified"
            old = await am.find_s3_readable_entity(db, ak.MODULE_PRODUCT_HUB, "up-old")
            assert old["lifecycle_status"] == am.LIFECYCLE_SUPERSEDED
            assert old.get("cancelled_storage_key")
            storage = s3_storage.get_storage()
            assert storage.exists(old["storage_key"])
            assert storage.exists(old["cancelled_storage_key"])
            db.products.docs = []
            hist = await hh.read_product_history(db, date_key="20260904", brand="Hyundai", hot_days=1)
            parts = {r.get("part_number") for r in hist["rows"]}
            assert "PN-NEW" in parts
            assert "PN-1" not in parts  # superseded/cancelled ignored for normal history

    _run(_go())


def test_cancelled_upload_copies_to_cancelled_storage():
    async def _go():
        db = FakeDB()
        excel = b"excel-bytes"
        key = ak.upload_original_key("2026-09-04", "up-c")
        db.uploads.docs.append(
            {
                "id": "up-c",
                "date_key": "20260904",
                "created_at": "2026-09-04T10:00:00+05:30",
                "storage_key": key,
                "sha256": s3_storage.sha256_bytes(excel),
                "file_size": len(excel),
                "status": "Cancelled",
                "publish_status": "Cancelled",
                "cancel_reason": "Wrong file",
            }
        )
        with _FakeS3Mode():
            s3_storage.get_storage().upload_bytes(key, excel, content_type="application/vnd.ms-excel")
            stored = await ea.handle_upload_stored(db, {"upload_id": "up-c", "storage_key": key, "sha256": s3_storage.sha256_bytes(excel), "file_size": len(excel)})
            assert stored["status"] == "verified"
            cancelled = await ea.handle_upload_cancelled(db, {"upload_id": "up-c", "reason": "Wrong file", "cancelled_by": "admin"})
            assert cancelled["status"] == "verified"
            dest = cancelled["cancelled_storage_key"]
            assert "/cancelled/up-c/" in dest
            storage = s3_storage.get_storage()
            assert storage.exists(key)  # original never deleted
            assert storage.exists(dest)
            src, _ = storage.download_bytes(key)
            dst, _ = storage.download_bytes(dest)
            assert src == dst == excel
            man = cancelled["manifest"]
            assert man["lifecycle_status"] == am.LIFECYCLE_CANCELLED
            assert man.get("cancelled_by") == "admin"

    _run(_go())


def test_order_and_request_terminal_archive_and_readers():
    async def _go():
        db = FakeDB()
        db.order_headers.docs.append(
            {
                "id": "ord-1",
                "order_number": "OD-1",
                "status": "Completed",
                "overall_status": "Completed",
                "brand_name": "Hyundai",
                "dealer_name": "DealerA",
                "branch": "B1",
                "updated_at": "2026-09-04T18:00:00+05:30",
                "created_at": "2026-09-04T10:00:00+05:30",
            }
        )
        db.order_items.docs.append(
            {
                "id": "it-1",
                "order_id": "ord-1",
                "status": "Completed",
                "part_number": "PN-1",
                "required_qty": 1,
            }
        )
        db.order_requests.docs.append(
            {
                "id": "req-1",
                "order_id": "ord-1",
                "order_item_id": "it-1",
                "request_number": "RQ-1",
                "status": "Completed",
                "requesting_brand": "Hyundai",
                "requesting_dealer": "DealerA",
                "requesting_branch": "B1",
                "updated_at": "2026-09-04T18:00:00+05:30",
            }
        )
        db.request_headers.docs.append({"id": "rh-1", "request_number": "RQ-1", "status": "Completed"})
        db.order_activity.docs.append({"id": "act-1", "order_id": "ord-1", "request_id": "req-1", "action": "Completed"})
        with _FakeS3Mode():
            o = await ea.handle_order_terminal(db, {"order_id": "ord-1", "status": "Completed"})
            assert o["status"] in {"verified", "already_archived"}
            assert "/orders/2026-09-04/current/ord-1/" in o["manifest"]["storage_key"]
            r = await ea.handle_request_terminal(db, {"request_id": "req-1", "status": "Completed"})
            assert r["status"] in {"verified", "already_archived"}
            packed = await hoh.read_order_package(db, "ord-1")
            assert packed["order"]["order_number"] == "OD-1"
            assert packed["items"]
            req_packed = await hrh.read_request_package(db, "req-1")
            assert req_packed["requests"]

            # Cancelled order
            db.order_headers.docs.append(
                {
                    "id": "ord-c",
                    "order_number": "OD-C",
                    "status": "Cancelled",
                    "overall_status": "Cancelled",
                    "brand_name": "Hyundai",
                    "dealer_name": "DealerA",
                    "branch": "B1",
                    "updated_at": "2026-09-04T19:00:00+05:30",
                    "cancelled_by": "user-1",
                    "cancel_reason": "Customer cancelled",
                }
            )
            db.order_items.docs.append({"id": "it-c", "order_id": "ord-c", "status": "Cancelled", "part_number": "PN-C"})
            c = await ea.handle_order_terminal(db, {"order_id": "ord-c", "status": "Cancelled"})
            assert "/cancelled/ord-c/" in c["manifest"]["storage_key"]
            assert c["manifest"]["lifecycle_status"] == am.LIFECYCLE_CANCELLED

            db.order_requests.docs.append(
                {
                    "id": "req-c",
                    "status": "Cancelled",
                    "request_number": "RQ-C",
                    "updated_at": "2026-09-04T19:00:00+05:30",
                    "requesting_brand": "Hyundai",
                    "requesting_dealer": "DealerA",
                    "requesting_branch": "B1",
                }
            )
            rc = await ea.handle_request_terminal(db, {"request_id": "req-c", "status": "Cancelled"})
            assert "/cancelled/req-c/" in rc["manifest"]["storage_key"]

    _run(_go())


def test_outbox_idempotency_retry_and_s3_outage_leaves_mongo():
    async def _go():
        db = FakeDB()
        _seed_upload(db)
        db.products.docs[0]["part_number"] = "KEEP-ME"
        # S3 down: enqueue still works, process fails, Mongo untouched
        job = await ao.enqueue(db, ao.EVENT_PUBLISH_COMPLETED, {"upload_id": "up-1"})
        again = await ao.enqueue(db, ao.EVENT_PUBLISH_COMPLETED, {"upload_id": "up-1"})
        assert job["idempotency_key"] == again["idempotency_key"]
        assert len(db.archive_outbox.docs) == 1
        drained = await ao.drain_once(db)
        assert drained["failed"] == 1
        assert db.products.docs[0]["part_number"] == "KEEP-ME"
        failed = db.archive_outbox.docs[0]
        assert failed["status"] == ao.STATUS_FAILED
        assert failed["attempts"] >= 1

        with _FakeS3Mode():
            # Force retry immediately
            await db.archive_outbox.update_one({"job_id": failed["job_id"]}, {"$set": {"next_retry_at": "1970-01-01T00:00:00+00:00"}})
            retried = await ao.drain_once(db)
            assert retried["verified"] == 1
            assert db.archive_outbox.docs[0]["status"] == ao.STATUS_VERIFIED
            # Duplicate replay
            job2 = await ao.enqueue(db, ao.EVENT_PUBLISH_COMPLETED, {"upload_id": "up-1"})
            assert job2["status"] == ao.STATUS_VERIFIED

    _run(_go())


def test_verified_and_cancelled_overwrite_protection():
    async def _go():
        db = FakeDB()
        _seed_upload(db, upload_id="up-imm", date_iso="2026-07-03")
        with _FakeS3Mode():
            first = await ea.handle_publish_completed(db, {"upload_id": "up-imm"})
            assert "manifest" in first
            key = first["manifest"]["storage_key"]
            storage = s3_storage.get_storage()
            with pytest.raises(s3_storage.ImmutableObjectError):
                storage.upload_bytes(key, b"different-bytes-should-fail")
            # same bytes is idempotent
            again = storage.upload_bytes(key, storage.download_bytes(key)[0])
            assert again.sha256 == first["manifest"]["sha256"]
            # cancelled path also immutable after write
            dest = key.replace("/current/", "/cancelled/")
            storage.copy_object(key, dest)
            with pytest.raises(s3_storage.ImmutableObjectError):
                storage.upload_bytes(dest, b"tamper", allow_replace=True)

            # PRUNED cannot be overwritten via _upload_verified
            await am.mark_status(db, first["manifest"]["archive_id"], am.STATUS_PRUNED, storage_backend="s3")
            db.products.docs[0]["quantity"] = 99
            db.products.docs[0]["available_qty_number"] = 99
            result = await ea.handle_publish_completed(db, {"upload_id": "up-imm"})
            assert result["status"] in {"already_verified", "already_archived"}
            live, _ = storage.download_bytes(key)
            # original gzip still in place (not the 99-qty rewrite)
            assert s3_storage.sha256_bytes(live) == first["manifest"]["sha256"]

    _run(_go())


def test_night_verify_catch_up_and_prune_stays_off():
    async def _go():
        import archive_scheduler as sched

        assert s3_storage.archive_prune_enabled() is False
        db = FakeDB()
        _seed_upload(db, date_iso="2026-08-01")
        db.order_headers.docs = []
        db.order_items.docs = []
        db.order_requests.docs = []
        db.upload_items.docs = []
        with _FakeS3Mode():
            await ea.handle_publish_completed(db, {"upload_id": "up-1"})
            outcome = await sched.run_daily_coordinated_archive(db, "2026-08-01")
            assert "outbox" in outcome
            assert "catch_up" in outcome
            assert outcome["datasets"]["product-history"]["status"] in {"verified", "already_verified"}
            prune = outcome["datasets"]["product-history"].get("prune") or {}
            assert prune.get("status") in {None, "skipped", "blocked"} or prune.get("deleted") in (None, 0)
            assert db.products.docs  # Mongo not deleted

    _run(_go())


def test_latest_live_product_hub_not_deleted_by_event_path():
    async def _go():
        db = FakeDB()
        _seed_upload(db, upload_id="live-today", date_iso=datetime.now(timezone.utc).date().isoformat())
        before = len(db.products.docs)
        with _FakeS3Mode():
            await ea.handle_publish_completed(db, {"upload_id": "live-today"})
        assert len(db.products.docs) == before

    _run(_go())


def test_enqueue_never_raises_and_partial_not_terminal():
    async def _go():
        db = FakeDB()
        db.order_requests.docs.append({"id": "req-p", "status": "Approved", "updated_at": "2026-09-04T12:00:00+05:30"})
        await ea.maybe_enqueue_request_terminal(db, db.order_requests.docs[0])
        assert db.archive_outbox.docs == []
        db.order_items.docs.append({"id": "i1", "order_id": "o1", "status": "Accepted"})
        db.order_headers.docs.append({"id": "o1", "status": "Order Created"})
        await ea.maybe_enqueue_order_terminal(db, "o1")
        assert db.archive_outbox.docs == []
        # enqueue_safe swallows
        class Boom:
            archive_outbox = None
        assert await ao.enqueue_safe(Boom(), "nope", {}) is None

    _run(_go())
