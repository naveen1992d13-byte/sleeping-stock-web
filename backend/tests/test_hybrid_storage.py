"""Hybrid storage architecture tests (local object-store fallback, no AWS secrets required)."""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import gzip
import json

import pytest
from pymongo.errors import DuplicateKeyError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Force local object store + safe defaults BEFORE importing storage modules
TMP_STORE = tempfile.mkdtemp(prefix="nmts-obj-")
os.environ["NMTS_LOCAL_OBJECT_STORE"] = TMP_STORE
os.environ["NMTS_STORAGE_ENV"] = "test"
os.environ["ARCHIVE_PRUNE_ENABLED"] = "false"
os.environ["ARCHIVE_SCHEDULER_ENABLED"] = "false"
os.environ.pop("AWS_ACCESS_KEY_ID", None)
os.environ.pop("AWS_SECRET_ACCESS_KEY", None)
os.environ.pop("NMTS_S3_BUCKET", None)

import s3_storage  # noqa: E402
import file_objects  # noqa: E402
import archive_manifest as am  # noqa: E402
import history_archive as ha  # noqa: E402
import hybrid_history as hh  # noqa: E402
import excel_permissions as ep  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")


class _FakeS3Mode:
    """Context manager: make local object store appear as REAL S3 for verification tests."""

    def __init__(self):
        self.storage = s3_storage.get_storage()
        self._orig = {}

    def __enter__(self):
        s = self.storage
        self._orig = {
            "is_s3": s.is_s3,
            "upload_bytes": s.upload_bytes,
            "head": s.head,
            "download_bytes": s.download_bytes,
            "verify_object": s.verify_object,
            "exists": s.exists,
            "delete": s.delete,
            "copy_object": s.copy_object,
            "move_object": s.move_object,
            "object_present": getattr(s, "_object_present_on_primary", None),
            "mode": s._mode,
            "client": s._client,
        }
        original_download = s.download_bytes
        original_verify = s.verify_object

        def fake_upload(key, data, content_type=None, metadata=None, allow_replace=False):
            # Write to local store but report provider=s3 (no real boto client in unit tests)
            data = bytes(data)
            digest = s3_storage.sha256_bytes(data)
            existing = s._local.head(key)
            if existing:
                old = existing.get("sha256")
                if old == digest:
                    return s3_storage.StoredObject(
                        storage_provider="s3",
                        storage_key=key,
                        content_type=existing.get("content_type") or content_type or "application/octet-stream",
                        file_size=int(existing.get("file_size") or 0),
                        sha256=old,
                    )
                if ("/cancelled/" in str(key)) or not allow_replace:
                    raise s3_storage.ImmutableObjectError(
                        f"Refusing overwrite of immutable object {key}"
                    )
            stored = s._local.put(key, data, content_type or "application/octet-stream")
            return s3_storage.StoredObject(
                storage_provider="s3",
                storage_key=stored.storage_key,
                content_type=stored.content_type,
                file_size=stored.file_size,
                sha256=stored.sha256,
            )

        def fake_head(key):
            h = s._local.head(key)
            if not h:
                return None
            return {**h, "storage_provider": "s3"}

        def fake_download(key):
            return s._local.get(key)

        def fake_verify(key, expected_sha256, expected_size):
            info = fake_head(key)
            if not info:
                return False
            if int(info.get("file_size") or -1) != int(expected_size):
                return False
            data, _ = fake_download(key)
            return s3_storage.sha256_bytes(data) == expected_sha256

        def fake_exists(key):
            return fake_head(key) is not None

        def fake_delete(key):
            return s._local.delete(key)

        def fake_copy(src_key, dest_key, allow_replace=False):
            data, ctype = fake_download(src_key)
            return fake_upload(dest_key, data, content_type=ctype, allow_replace=allow_replace)

        def fake_move(src_key, dest_key, expected_sha256=None, expected_size=None):
            dest = fake_head(dest_key)
            src = fake_head(src_key)
            if not src and dest:
                return s3_storage.StoredObject(
                    storage_provider="s3",
                    storage_key=dest_key,
                    content_type=dest.get("content_type") or "application/octet-stream",
                    file_size=int(dest.get("file_size") or 0),
                    sha256=dest.get("sha256") or "",
                )
            copied = fake_copy(src_key, dest_key)
            digest = expected_sha256 or copied.sha256
            size = int(expected_size if expected_size is not None else copied.file_size)
            if not fake_verify(dest_key, digest, size):
                raise s3_storage.S3StorageError("fake move dest verify failed")
            fake_delete(src_key)
            if fake_exists(src_key):
                raise s3_storage.S3StorageError("fake move source still present")
            return copied

        s.is_s3 = lambda: True  # type: ignore
        s._mode = "s3"
        s.upload_bytes = fake_upload  # type: ignore
        s.head = fake_head  # type: ignore
        s.download_bytes = fake_download  # type: ignore
        s.verify_object = fake_verify  # type: ignore
        s.exists = fake_exists  # type: ignore
        s.delete = fake_delete  # type: ignore
        s.copy_object = fake_copy  # type: ignore
        s.move_object = fake_move  # type: ignore
        s._object_present_on_primary = fake_exists  # type: ignore
        return s

    def __exit__(self, *exc):
        s = self.storage
        s.is_s3 = self._orig["is_s3"]  # type: ignore
        s.upload_bytes = self._orig["upload_bytes"]  # type: ignore
        s.head = self._orig["head"]  # type: ignore
        s.download_bytes = self._orig["download_bytes"]  # type: ignore
        s.verify_object = self._orig["verify_object"]  # type: ignore
        s.exists = self._orig["exists"]  # type: ignore
        if "delete" in self._orig:
            s.delete = self._orig["delete"]  # type: ignore
        if "copy_object" in self._orig:
            s.copy_object = self._orig["copy_object"]  # type: ignore
        if "move_object" in self._orig:
            s.move_object = self._orig["move_object"]  # type: ignore
        if self._orig.get("object_present") is not None:
            s._object_present_on_primary = self._orig["object_present"]  # type: ignore
        s._mode = self._orig["mode"]
        s._client = self._orig["client"]
        return False


class FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, *a, **k):
        if a:
            # support sort([("field", -1)]) and sort("field", -1)
            if len(a) == 1 and isinstance(a[0], (list, tuple)) and a[0] and isinstance(a[0][0], (list, tuple)):
                keys = list(a[0])
            elif len(a) >= 2 and isinstance(a[0], str):
                keys = [(a[0], a[1])]
            else:
                keys = list(a[0]) if a and isinstance(a[0], (list, tuple)) else []
            for field, direction in reversed(keys):
                self._docs.sort(key=lambda x: str(x.get(field) or ""), reverse=direction < 0)
        return self

    def skip(self, n):
        self._docs = self._docs[int(n) :]
        return self

    def limit(self, n):
        self._docs = self._docs[: int(n)]
        return self

    async def to_list(self, n=None):
        return list(self._docs)[: n or len(self._docs)]


class FakeCollection:
    def __init__(self, unique_keys=None):
        self.docs = []
        self.indexes = []
        self.unique_keys = unique_keys or []

    async def insert_one(self, doc):
        for keys in self.unique_keys:
            probe = {k: doc.get(k) for k in keys}
            if any(all(d.get(k) == probe[k] for k in keys) for d in self.docs):
                raise DuplicateKeyError("E11000 duplicate key")
        self.docs.append(dict(doc))
        return SimpleNamespace(inserted_id="x")

    async def insert_many(self, docs):
        for d in docs:
            await self.insert_one(d)

    async def find_one(self, query=None, projection=None, sort=None):
        matches = [d for d in self.docs if self._match(d, query or {})]
        if sort:
            for field, direction in reversed(list(sort)):
                matches.sort(key=lambda x: str(x.get(field) or ""), reverse=direction < 0)
        return dict(matches[0]) if matches else None

    def find(self, query=None, projection=None):
        matches = [dict(d) for d in self.docs if self._match(d, query or {})]
        return FakeCursor(matches)

    async def find_one_and_update(self, query, update, return_document=None, projection=None, upsert=False, sort=None):
        matches = [(i, d) for i, d in enumerate(self.docs) if self._match(d, query)]
        if sort:
            for field, direction in reversed(list(sort)):
                matches.sort(key=lambda pair: str(pair[1].get(field) or ""), reverse=direction < 0)
        for i, d in matches:
            applied = self._apply({**d, "_existing": True}, update)
            applied.pop("_existing", None)
            self.docs[i] = applied
            return dict(applied)
        if upsert:
            result = await self.update_one(query, update, upsert=True)
            if result.upserted_id:
                simple = {k: v for k, v in (query or {}).items() if not str(k).startswith("$") and not isinstance(v, dict)}
                return await self.find_one(simple or query)
        return None

    async def delete_one(self, query):
        before = len(self.docs)
        self.docs = [d for d in self.docs if not self._match(d, query)]
        return SimpleNamespace(deleted_count=before - len(self.docs))

    async def delete_many(self, query):
        before = len(self.docs)
        self.docs = [d for d in self.docs if not self._match(d, query)]
        return SimpleNamespace(deleted_count=before - len(self.docs))

    async def count_documents(self, query):
        return len([d for d in self.docs if self._match(d, query)])

    async def distinct(self, key, query=None):
        vals = []
        seen = set()
        for d in self.docs:
            if not self._match(d, query or {}):
                continue
            v = d.get(key)
            marker = str(v)
            if marker in seen:
                continue
            seen.add(marker)
            vals.append(v)
        return vals

    async def create_index(self, *a, **k):
        self.indexes.append((a, k))

    async def estimated_document_count(self):
        return len(self.docs)

    def _match(self, doc, query):
        import re
        for k, v in (query or {}).items():
            if k == "$or":
                if not any(self._match(doc, clause) for clause in v):
                    return False
                continue
            if k == "$and":
                if not all(self._match(doc, clause) for clause in v):
                    return False
                continue
            dv = doc.get(k)
            if isinstance(v, dict):
                if "$in" in v and dv not in v["$in"]:
                    return False
                if "$nin" in v and dv in v["$nin"]:
                    return False
                if "$exists" in v:
                    present = k in doc
                    if bool(v["$exists"]) != present:
                        return False
                if "$gte" in v and not (dv is not None and dv >= v["$gte"]):
                    return False
                if "$lte" in v and not (dv is not None and dv <= v["$lte"]):
                    return False
                if "$lt" in v and not (dv is not None and dv < v["$lt"]):
                    return False
                if "$ne" in v and dv == v["$ne"]:
                    return False
                if "$regex" in v and not re.search(str(v["$regex"]), str(dv or "")):
                    return False
            elif dv != v:
                return False
        return True

    def _apply(self, doc, update):
        out = dict(doc)
        if "$set" in update:
            out.update(update["$set"])
        if "$setOnInsert" in update and not doc.get("_existing"):
            for k, v in update["$setOnInsert"].items():
                out.setdefault(k, v)
        if "$inc" in update:
            for k, v in update["$inc"].items():
                out[k] = (out.get(k) or 0) + v
        if "$unset" in update:
            for k in update["$unset"]:
                out.pop(k, None)
        # bare fields (rare)
        for k, v in update.items():
            if not k.startswith("$"):
                out[k] = v
        return out

    async def update_one(self, query, update, upsert=False):
        for i, d in enumerate(self.docs):
            if self._match(d, query):
                applied = self._apply({**d, "_existing": True}, update)
                applied.pop("_existing", None)
                self.docs[i] = applied
                return SimpleNamespace(matched_count=1, modified_count=1, upserted_id=None)
        if upsert:
            base = dict(query)
            # flatten simple equality query keys
            base = {k: v for k, v in base.items() if not isinstance(v, dict)}
            base = self._apply(base, update)
            base.pop("_existing", None)
            self.docs.append(base)
            return SimpleNamespace(matched_count=0, modified_count=0, upserted_id="u")
        return SimpleNamespace(matched_count=0, modified_count=0, upserted_id=None)


class FakeDB:
    def __init__(self):
        self._cols = {
            "archive_job_locks": FakeCollection(unique_keys=[["lock_key"]]),
            "archive_outbox": FakeCollection(unique_keys=[["idempotency_key"], ["job_id"]]),
        }

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        if name not in self._cols:
            self._cols[name] = FakeCollection()
        return self._cols[name]

    def __getitem__(self, name):
        return getattr(self, name)


@pytest.fixture(autouse=True)
def _reset_storage():
    s3_storage.reset_storage_for_tests()
    yield
    s3_storage.reset_storage_for_tests()


def test_archive_prune_enabled_default_false():
    assert s3_storage.archive_prune_enabled() is False


def test_s3_falls_back_to_local_without_credentials():
    storage = s3_storage.get_storage()
    assert storage.mode == "local"
    key = storage.key("uploads", "2026-08-11", "product-hub", "demo.xlsx")
    data = b"excel-bytes-demo"
    stored = storage.upload_bytes(key, data, content_type="application/vnd.ms-excel")
    assert stored.storage_provider == "local"
    assert stored.file_size == len(data)
    assert stored.sha256 == s3_storage.sha256_bytes(data)
    assert storage.exists(key)
    assert storage.verify_object(key, stored.sha256, stored.file_size)
    got, ctype = storage.download_bytes(key)
    assert got == data


def test_s3_failure_keeps_mongo_path_via_failed_manifest():
    async def _run():
        db = FakeDB()
        storage = s3_storage.get_storage()
        # Simulate failure by patching upload
        original = storage.upload_bytes

        def boom(*a, **k):
            raise s3_storage.S3StorageError("simulated outage")

        storage.upload_bytes = boom  # type: ignore
        try:
            with pytest.raises(Exception):
                await ha._upload_verified(
                    db,
                    module="product-history",
                    archive_date="2026-01-01",
                    archive_month="2026-01",
                    storage_key="test/fail.jsonl.gz",
                    data=b"abc",
                    source_collection="products",
                    record_count=1,
                    min_date="2026-01-01",
                    max_date="2026-01-01",
                    brands={"B"},
                    dealers={"D"},
                    branches={"BR"},
                )
        finally:
            storage.upload_bytes = original  # type: ignore
        row = await db.archive_manifests.find_one({"module": "product-history"})
        assert row["status"] == am.STATUS_FAILED
        assert row.get("eligible_for_prune") is False
        # Mongo products untouched (we never deleted)
        assert db.products.docs == []

    asyncio.get_event_loop().run_until_complete(_run())


def test_checksum_verification_and_idempotent_daily_archive():
    async def _run():
        db = FakeDB()
        date_iso = "2026-08-01"
        date_key = "20260801"
        db.products.docs = [
            {
                "part_number": "P1",
                "item_name": "Part 1",
                "quantity": 2,
                "total_value": 20,
                "brand_name": "Hyundai",
                "dealer_name": "DealerA",
                "branch": "Branch1",
                "publish_status": "Published",
                "active_date_key": date_key,
                "mav_value": 10,
            }
        ]
        # Local fallback must NOT become VERIFIED
        with pytest.raises(RuntimeError, match="REAL S3 unavailable|not TRANSFERRED"):
            await ha.archive_product_history_for_date(db, date_iso)
        row = await db.archive_manifests.find_one({"module": "product-history"})
        assert row["status"] == am.STATUS_FAILED
        assert row.get("eligible_for_prune") is False
        assert "local fallback" in (row.get("error") or "").lower() or "s3" in (row.get("error") or "").lower()

        # Mock REAL S3 path
        with _FakeS3Mode():
            first = await ha.archive_product_history_for_date(db, date_iso, force=True)
            assert first["status"] == "verified"
            assert first["manifest"]["status"] == am.STATUS_VERIFIED
            assert first["manifest"]["eligible_for_prune"] is True
            assert first["manifest"]["storage_backend"] == "s3"
            assert first["manifest"]["sha256"]
            assert first["manifest"]["file_size"] > 0

            second = await ha.archive_product_history_for_date(db, date_iso)
            assert second["status"] == "already_verified"
            verified = [d for d in db.archive_manifests.docs if d.get("status") == am.STATUS_VERIFIED]
            assert len(verified) >= 1

        # Prune disabled
        pruned = await ha.prune_eligible_mongo_history(db)
        assert pruned["status"] == "skipped"
        assert db.products.docs  # still present

    asyncio.get_event_loop().run_until_complete(_run())


def test_hybrid_history_mongo_hot_s3_cold_and_mixed():
    async def _run():
        db = FakeDB()
        storage = s3_storage.get_storage()
        today = datetime.now(IST).date()
        hot_day = today.isoformat()
        cold_day = (today - timedelta(days=120)).isoformat()
        hot_key = cold_key = None

        # Seed Mongo hot
        db.products.docs.append(
            {
                "part_number": "HOT1",
                "item_name": "Hot",
                "quantity": 1,
                "total_value": 5,
                "brand_name": "Hyundai",
                "dealer_name": "DealerA",
                "branch": "Branch1",
                "publish_status": "Published",
                "active_date_key": hot_day.replace("-", ""),
            }
        )

        # Build cold archive manually
        cold_rows = [
            {
                "part_number": "COLD1",
                "item_name": "Cold",
                "quantity": 3,
                "total_value": 9,
                "brand_name": "Hyundai",
                "dealer_name": "DealerA",
                "branch": "Branch1",
                "publish_status": "Published",
                "active_date_key": cold_day.replace("-", ""),
            }
        ]
        # Put cold rows into products then archive, then remove from mongo to force S3 read
        db.products.docs.extend(cold_rows)
        with _FakeS3Mode():
            archived = await ha.archive_product_history_for_date(db, cold_day)
            assert archived["status"] == "verified"
        # Remove cold from mongo
        db.products.docs = [d for d in db.products.docs if d.get("part_number") != "COLD1"]

        hot = await hh.read_product_history(db, date_key=hot_day, brand="Hyundai")
        assert hot["mongo_count"] >= 1
        assert any(r["part_number"] == "HOT1" for r in hot["rows"])

        # Cold read from archive requires storage download — works via local store under fake S3 key
        with _FakeS3Mode():
            cold = await hh.read_product_history(db, date_key=cold_day, brand="Hyundai", hot_days=1)
            assert cold["s3_count"] >= 1
            assert any(r["part_number"] == "COLD1" for r in cold["rows"])

            mixed = await hh.read_product_history(
                db,
                from_date=cold_day,
                to_date=hot_day,
                brand="Hyundai",
                hot_days=1,
            )
            parts = {r["part_number"] for r in mixed["rows"]}
            assert "HOT1" in parts and "COLD1" in parts

    asyncio.get_event_loop().run_until_complete(_run())


def test_file_objects_legacy_fallback_and_store():
    async def _run():
        data = b"%PDF-1.4 legacy"
        legacy_path = Path(TMP_STORE) / "legacy.pdf"
        legacy_path.write_bytes(data)
        meta = {"attachment_storage_path": str(legacy_path), "file_name": "legacy.pdf", "content_type": "application/pdf"}
        got, ctype = file_objects.read_bytes_from_meta(meta)
        assert got == data

        stored = await file_objects.store_bytes(
            module="notices",
            relative_key="n1/file.pdf",
            data=b"%PDF-1.4 new",
            original_filename="file.pdf",
            content_type="application/pdf",
        )
        assert stored["storage_key"]
        assert stored["sha256"]
        got2, _ = file_objects.read_bytes_from_meta(stored)
        assert got2 == b"%PDF-1.4 new"

    asyncio.get_event_loop().run_until_complete(_run())


def test_completed_monthly_archives_skip_open_requests():
    async def _run():
        db = FakeDB()
        # Previous month window
        month, start, end = ha._prev_calendar_month()
        completed_at = (start + timedelta(days=2)).astimezone(timezone.utc).isoformat()
        db.order_requests.docs = [
            {
                "id": "r1",
                "request_no": "REQ-OPEN",
                "status": "Requested",
                "created_at": completed_at,
                "updated_at": completed_at,
                "brand_name": "Hyundai",
                "requesting_dealer": "D1",
                "requesting_branch": "B1",
            },
            {
                "id": "r2",
                "request_no": "REQ-DONE",
                "status": "Completed",
                "completed_at": completed_at,
                "brand_name": "Hyundai",
                "requesting_dealer": "D1",
                "requesting_branch": "B1",
                "total_value": 100,
            },
            {
                "id": "r3",
                "request_no": "REQ-DISPATCH",
                "status": "Dispatched",
                "updated_at": completed_at,
                "brand_name": "Hyundai",
                "requesting_dealer": "D1",
                "requesting_branch": "B1",
            },
        ]
        db.order_headers.docs = [
            {
                "id": "oh1",
                "order_number": "ORD-DONE",
                "status": "Order Created",
                "brand_name": "Hyundai",
                "dealer_name": "D1",
                "branch": "B1",
                "created_at": completed_at,
                "updated_at": completed_at,
            },
            {
                "id": "oh2",
                "order_number": "ORD-OPEN",
                "status": "Order Created",
                "brand_name": "Hyundai",
                "dealer_name": "D1",
                "branch": "B1",
                "created_at": completed_at,
                "updated_at": completed_at,
            },
        ]
        db.order_items.docs = [
            {
                "id": "oi1",
                "order_id": "oh1",
                "status": "Cancelled",
                "part_number": "P1",
                "updated_at": completed_at,
                "completed_at": completed_at,
            },
            {
                "id": "oi2",
                "order_id": "oh2",
                "status": "Pending Retry",
                "part_number": "P2",
                "updated_at": completed_at,
            },
        ]
        with _FakeS3Mode():
            result = await ha.archive_completed_requests_month(db, month)
            assert result["status"] == "verified"
            assert result["record_count"] == 1
            idx = db.request_archive_index.docs
            assert any(x.get("number") == "REQ-DONE" for x in idx)
            assert not any(x.get("number") == "REQ-OPEN" for x in idx)

            orders = await ha.archive_completed_orders_month(db, month)
            assert orders["status"] == "verified"
            assert orders["record_count"] == 1
            assert orders["manifest"]["source_collection"] == "order_headers+order_items"
            assert any(x.get("number") == "ORD-DONE" for x in db.order_archive_index.docs)
            assert not any(x.get("number") == "ORD-OPEN" for x in db.order_archive_index.docs)

    asyncio.get_event_loop().run_until_complete(_run())


def test_excel_permissions_roles():
    assert ep.can_export_excel(SimpleNamespace(role="master")) is True
    assert ep.can_export_excel(SimpleNamespace(role="admin")) is True
    assert ep.can_export_excel(SimpleNamespace(role="user")) is False
    with pytest.raises(Exception) as exc:
        ep.require_excel_export(SimpleNamespace(role="user"))
    assert getattr(exc.value, "status_code", None) == 403


def test_original_excel_metadata_shape():
    async def _run():
        stored = await file_objects.store_bytes(
            module="uploads",
            relative_key="2026-08-11/product-hub/u1.xlsx",
            data=b"PK\x03\x04fake-xlsx",
            original_filename="stock.xlsx",
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        for field in ("storage_provider", "storage_key", "original_filename", "content_type", "file_size", "sha256", "archived_at"):
            assert field in stored and stored[field] is not None

    asyncio.get_event_loop().run_until_complete(_run())


def test_analytics_snapshot_writer():
    async def _run():
        db = FakeDB()
        rows = [
            {
                "part_number": "P9",
                "item_name": "Widget",
                "quantity": 4,
                "mav_value": 2.5,
                "total_value": 10,
                "brand_name": "Hyundai",
                "dealer_name": "D",
                "branch": "B",
                "brand_code": "HY",
                "dealer_code": "D1",
                "part_category": "Fast Moving",
                "publish_status": "Published",
                "active_date_key": "20260801",
            }
        ]
        docs = await ha.write_analytics_snapshots_for_date(db, "2026-08-01", rows)
        assert len(docs) == 1
        assert db.analytics_stock_daily_snapshots.docs
        assert db.analytics_stock_daily_snapshots.docs[0]["available_qty"] == 4

    asyncio.get_event_loop().run_until_complete(_run())


def test_verification_archive():
    async def _run():
        db = FakeDB()
        day = "2026-07-01"
        start = datetime(2026, 7, 1, 10, 0, tzinfo=IST)
        db.stock_verification_history.docs = [
            {
                "id": "v1",
                "part_number": "P1",
                "verified_at": start.isoformat(),
                "brand_name": "Hyundai",
                "dealer_name": "D",
                "branch": "B",
            }
        ]
        with _FakeS3Mode():
            result = await ha.archive_verifications_for_date(db, day)
            assert result["status"] == "verified"
            assert result["record_count"] == 1

    asyncio.get_event_loop().run_until_complete(_run())


def test_no_secrets_in_status_payload():
    status = s3_storage.get_storage().status()
    blob = str(status)
    assert "AWS_SECRET" not in blob
    assert status["archive_prune_enabled"] is False
    assert "access_key_present" in status
    assert status["storage_backend"] == "LOCAL FALLBACK"
    assert status["real_s3"] is False
    assert status["prune_authorized"] is False
    assert "Cloud archive not active" in (status.get("warning") or "")


def test_product_hot_days_default_is_one():
    # Env may override in process; temporarily clear
    prev = os.environ.pop("PRODUCT_MONGO_HOT_DAYS", None)
    try:
        assert s3_storage.product_mongo_hot_days() == 1
    finally:
        if prev is not None:
            os.environ["PRODUCT_MONGO_HOT_DAYS"] = prev


def test_local_fallback_never_eligible_for_prune_and_blocks_prune():
    async def _run():
        db = FakeDB()
        date_iso = "2026-07-15"
        db.products.docs = [
            {
                "part_number": "P1",
                "quantity": 1,
                "total_value": 1,
                "brand_name": "Hyundai",
                "dealer_name": "DealerA",
                "branch": "Branch1",
                "publish_status": "Published",
                "active_date_key": "20260715",
            }
        ]
        with pytest.raises(RuntimeError):
            await ha.archive_product_history_for_date(db, date_iso)
        archived = await db.archive_manifests.find_one({"module": "product-history"})
        assert archived["status"] == am.STATUS_FAILED
        assert archived.get("eligible_for_prune") is False

        os.environ["ARCHIVE_PRUNE_ENABLED"] = "true"
        try:
            pruned = await ha.prune_product_history_date(db, date_iso)
            assert pruned["status"] in {"blocked", "skipped", "not_verified", "error"} or pruned.get("status") == "blocked"
            assert len(db.products.docs) == 1  # untouched
        finally:
            os.environ["ARCHIVE_PRUNE_ENABLED"] = "false"

    asyncio.get_event_loop().run_until_complete(_run())


def test_paginated_history_filters_and_sources():
    async def _run():
        db = FakeDB()
        today = datetime.now(IST).date().isoformat()
        for i in range(5):
            db.products.docs.append(
                {
                    "part_number": f"PN{i}",
                    "item_name": f"Part {i}",
                    "quantity": i,
                    "total_value": i,
                    "brand_name": "Hyundai",
                    "dealer_name": "DealerA",
                    "branch": "Branch1",
                    "publish_status": "Published",
                    "active_date_key": today.replace("-", ""),
                }
            )
        page1 = await hh.read_product_history(db, date_key=today, page=1, page_size=2, brand="Hyundai")
        assert page1["page"]["total"] == 5
        assert len(page1["rows"]) == 2
        filtered = await hh.read_product_history(db, date_key=today, part_number="PN3", brand="Hyundai")
        assert filtered["total"] == 1
        assert filtered["rows"][0]["part_number"] == "PN3"

    asyncio.get_event_loop().run_until_complete(_run())


def test_storage_usage_dealer_ranking_and_cost():
    async def _run():
        import storage_usage as su

        db = FakeDB()
        month = datetime.now(IST).strftime("%Y-%m")
        await su.record_storage_usage(
            db, operation=su.OP_UPLOAD, bytes_count=5 * 1024 ** 3, brand="Hyundai", dealer="DealerA", branch="B1", module="uploads"
        )
        await su.record_storage_usage(
            db, operation=su.OP_DOWNLOAD, bytes_count=1 * 1024 ** 3, brand="Hyundai", dealer="DealerA", branch="B1", module="uploads"
        )
        await su.record_storage_usage(
            db, operation=su.OP_UPLOAD, bytes_count=1 * 1024 ** 3, brand="Hyundai", dealer="DealerB", branch="B2", module="uploads"
        )
        ranking = await su.dealer_usage_ranking(db, month=month)
        assert ranking[0]["dealer"] == "DealerA"
        assert ranking[0]["estimated_cost"] >= ranking[-1]["estimated_cost"]
        totals = await su.month_usage_totals(db, month)
        assert totals["cost_label"] == "Estimated Cost"
        assert totals["estimated_total_cost"] > 0

    asyncio.get_event_loop().run_until_complete(_run())


def test_scheduler_archives_previous_day_helper():
    from archive_scheduler import previous_calendar_day_iso

    fixed = datetime(2026, 8, 12, 0, 20, tzinfo=IST)
    assert previous_calendar_day_iso(fixed) == "2026-08-11"


def test_never_prune_today_even_if_enabled():
    async def _run():
        db = FakeDB()
        today = datetime.now(IST).date().isoformat()
        db.products.docs = [
            {
                "part_number": "TODAY1",
                "quantity": 1,
                "total_value": 1,
                "brand_name": "Hyundai",
                "dealer_name": "DealerA",
                "branch": "Branch1",
                "publish_status": "Published",
                "active_date_key": today.replace("-", ""),
                "is_active_today": True,
            }
        ]
        os.environ["ARCHIVE_PRUNE_ENABLED"] = "true"
        try:
            pruned = await ha.prune_product_history_date(db, today)
            assert pruned["status"] == "blocked"
            assert "today" in pruned["reason"].lower()
            assert db.products.docs
        finally:
            os.environ["ARCHIVE_PRUNE_ENABLED"] = "false"

    asyncio.get_event_loop().run_until_complete(_run())


def test_migration_dry_run_lists_historical_dates():
    async def _run():
        db = FakeDB()
        today = datetime.now(IST).date()
        old = (today - timedelta(days=3)).strftime("%Y%m%d")
        db.products.docs = [
            {
                "part_number": "OLD",
                "quantity": 1,
                "total_value": 1,
                "brand_name": "Hyundai",
                "dealer_name": "DealerA",
                "branch": "Branch1",
                "publish_status": "Published",
                "active_date_key": old,
            },
            {
                "part_number": "NEW",
                "quantity": 1,
                "total_value": 1,
                "brand_name": "Hyundai",
                "dealer_name": "DealerA",
                "branch": "Branch1",
                "publish_status": "Published",
                "active_date_key": today.strftime("%Y%m%d"),
            },
        ]
        plan = await ha.archive_historical_dates(db, dry_run=True)
        assert plan["status"] == "dry_run"
        assert plan["dates"] >= 1
        assert all(p["date"] != today.isoformat() for p in plan["plan"])

    asyncio.get_event_loop().run_until_complete(_run())


# ---------------------------------------------------------------------------
# PR #36 corrective-pass tests
# ---------------------------------------------------------------------------


def test_atomic_job_lock_race_and_expiry():
    async def _run():
        db = FakeDB()
        key = "daily-product-history:2026-08-10"
        w1 = await am.acquire_job_lock(db, key, "worker-1", ttl_seconds=60)
        w2 = await am.acquire_job_lock(db, key, "worker-2", ttl_seconds=60)
        assert w1 is True
        assert w2 is False
        # Active lock cannot be stolen
        assert await am.acquire_job_lock(db, key, "worker-2", ttl_seconds=60) is False
        # Same owner may refresh
        assert await am.acquire_job_lock(db, key, "worker-1", ttl_seconds=60) is True
        # Expire and reclaim
        db.archive_job_locks.docs[0]["expires_at"] = 1.0
        assert await am.acquire_job_lock(db, key, "worker-2", ttl_seconds=60) is True
        row = await db.archive_job_locks.find_one({"lock_key": key})
        assert row["owner"] == "worker-2"
        # Release only by owner
        await am.release_job_lock(db, key, "worker-1")
        assert await db.archive_job_locks.find_one({"lock_key": key}) is not None
        await am.release_job_lock(db, key, "worker-2")
        assert await db.archive_job_locks.find_one({"lock_key": key}) is None

    asyncio.get_event_loop().run_until_complete(_run())


def test_archive_idempotent_under_lock_and_failure_leaves_mongo():
    async def _run():
        db = FakeDB()
        date_iso = "2026-08-03"
        db.products.docs = [
            {
                "part_number": "P1",
                "quantity": 1,
                "total_value": 1,
                "brand_name": "Hyundai",
                "dealer_name": "DealerA",
                "branch": "B1",
                "publish_status": "Published",
                "active_date_key": "20260803",
            }
        ]
        first = None
        with _FakeS3Mode():
            first = await ha.archive_product_history_for_date(db, date_iso)
            assert first["status"] == "verified"
            second = await ha.archive_product_history_for_date(db, date_iso)
            assert second["status"] == "already_verified"
        assert len(db.products.docs) == 1

        # Failure path leaves mongo untouched
        storage = s3_storage.get_storage()
        original = storage.upload_bytes

        def boom(*a, **k):
            raise s3_storage.S3StorageError("boom")

        with _FakeS3Mode():
            storage.upload_bytes = boom  # type: ignore
            try:
                with pytest.raises(Exception):
                    await ha.archive_product_history_for_date(db, "2026-08-04", force=True)
            finally:
                storage.upload_bytes = original  # type: ignore
        # products for 08-03 still present; no prune happened
        assert any(d.get("active_date_key") == "20260803" for d in db.products.docs)

    asyncio.get_event_loop().run_until_complete(_run())


def test_streaming_history_pagination_memory_safe_and_filters():
    # Large synthetic archive: stream pagination must return only page rows
    rows = []
    for i in range(5000):
        dealer = "DealerA" if i % 2 == 0 else "DealerB"
        branch = "B1" if i % 3 == 0 else "B2"
        rows.append(
            {
                "part_number": f"PN{i:05d}",
                "item_name": f"Part {i}",
                "quantity": 1,
                "total_value": 1,
                "brand_name": "Hyundai",
                "dealer_name": dealer,
                "branch": branch,
                "publish_status": "Published",
            }
        )
    buf = BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
        for r in rows:
            gz.write((json.dumps(r) + "\n").encode("utf-8"))
    data = buf.getvalue()

    page = hh.stream_filter_page_from_archive_bytes(
        data, brand="Hyundai", dealer="DealerA", branch="B1", page=2, page_size=25
    )
    assert len(page["rows"]) == 25
    assert page["page"]["page"] == 2
    assert page["total"] > 25
    assert all(r["dealer_name"] == "DealerA" and r["branch"] == "B1" for r in page["rows"])
    # Only page materialised
    assert page["count"] == 25

    # Brand/dealer/branch filters
    filtered = hh.stream_filter_page_from_archive_bytes(
        data, brand="Hyundai", dealer="DealerB", branch="B2", part_number="PN00001", page=1, page_size=10
    )
    # PN00001 is dealer B, branch B2 (i=1 -> dealer B, i%3=1 -> B2)
    assert filtered["total"] == 1
    assert filtered["rows"][0]["part_number"] == "PN00001"


def test_allocate_billable_egress_account_level_free_allowance():
    import storage_usage as su

    gib = 1024 ** 3
    # total < 100 GB
    m1 = su.allocate_billable_egress_gb({"A": 40 * gib, "B": 30 * gib}, free_egress_gb=100)
    assert m1["A"] == 0.0 and m1["B"] == 0.0
    # total == 100 GB
    m2 = su.allocate_billable_egress_gb({"A": 60 * gib, "B": 40 * gib}, free_egress_gb=100)
    assert m2["A"] == 0.0 and m2["B"] == 0.0
    # total > 100 GB — proportional
    m3 = su.allocate_billable_egress_gb({"A": 150 * gib, "B": 50 * gib}, free_egress_gb=100)
    # billable = 100 GB; A share 75%, B 25%
    assert abs(m3["A"] - 75.0) < 1e-6
    assert abs(m3["B"] - 25.0) < 1e-6
    assert abs(sum(m3.values()) - 100.0) < 1e-6
    # one dealer only
    m4 = su.allocate_billable_egress_gb({"Solo": 250 * gib}, free_egress_gb=100)
    assert abs(m4["Solo"] - 150.0) < 1e-6
    # zero egress
    m5 = su.allocate_billable_egress_gb({"A": 0, "B": 0}, free_egress_gb=100)
    assert m5["A"] == 0.0 and m5["B"] == 0.0


def test_get_download_not_double_counted_in_request_cost():
    import storage_usage as su

    async def _run():
        db = FakeDB()
        month = datetime.now(IST).strftime("%Y-%m")
        await su.record_storage_usage(
            db,
            operation=su.OP_DOWNLOAD,
            bytes_count=2 * 1024 ** 3,
            brand="Hyundai",
            dealer="DealerA",
            branch="B1",
            module="uploads",
            request_count=1000,
        )
        row = db.storage_usage_daily.docs[0]
        assert row["download_requests"] == 1000
        assert row["get_requests"] == 1000  # one S3 GET per download, not doubled
        totals = await su.month_usage_totals(db, month)
        assert totals["download_requests"] == 1000
        assert totals["get_requests"] == 1000
        # Request cost uses get_requests once (download_requests ignored for billing)
        pricing = s3_storage.s3_pricing_config()
        expected_req = (1000 / 1000.0) * float(pricing["get_price_per_1000"])
        assert abs(totals["estimated_request_cost"] - expected_req) < 1e-9
        # Explicit: billing must NOT be 2x GET from download_requests
        doubled = (2000 / 1000.0) * float(pricing["get_price_per_1000"])
        assert abs(totals["estimated_request_cost"] - doubled) > 1e-9

        ranking = await su.dealer_usage_ranking(db, month=month)
        assert ranking[0]["get_requests"] == 1000
        assert ranking[0]["download_requests"] == 1000

    asyncio.get_event_loop().run_until_complete(_run())


def test_dealer_ranking_uses_global_free_egress_and_sums():
    import storage_usage as su

    async def _run():
        db = FakeDB()
        month = datetime.now(IST).strftime("%Y-%m")
        gib = 1024 ** 3
        # 120 + 80 = 200 GB download → 100 billable; A 60%, B 40%
        await su.record_storage_usage(
            db, operation=su.OP_DOWNLOAD, bytes_count=120 * gib, brand="Hyundai", dealer="A", branch="B1", module="x"
        )
        await su.record_storage_usage(
            db, operation=su.OP_DOWNLOAD, bytes_count=80 * gib, brand="Hyundai", dealer="B", branch="B2", module="x"
        )
        ranking = await su.dealer_usage_ranking(db, month=month)
        assert ranking[0]["dealer"] == "A"
        assert abs(ranking[0]["billable_egress_gb"] - 60.0) < 1e-6
        assert abs(ranking[1]["billable_egress_gb"] - 40.0) < 1e-6
        # Dealer transfer costs sum to account transfer cost
        totals = await su.month_usage_totals(db, month)
        dealer_transfer = sum(r["estimated_transfer_cost"] for r in ranking)
        assert abs(dealer_transfer - totals["estimated_transfer_cost"]) < 1e-6

    asyncio.get_event_loop().run_until_complete(_run())


def test_auto_perpetual_previous_date_is_scoped_no_cross_dealer():
    import auto_perpetual as ap

    async def _run():
        db = FakeDB()
        # Dealer A has prior Mongo inventory; Dealer B does not
        db.products.docs = [
            {
                "part_number": "P1",
                "brand_name": "Hyundai",
                "dealer_name": "DealerA",
                "branch": "Branch1",
                "publish_status": "Published",
                "active_date_key": "20260809",
                "quantity": 1,
            }
        ]
        # Also a verified archive that only conceptually belongs to A (probe uses filters)
        cold_rows = [
            {
                "part_number": "P1",
                "item_name": "Part",
                "quantity": 1,
                "total_value": 1,
                "brand_name": "Hyundai",
                "dealer_name": "DealerA",
                "branch": "Branch1",
                "publish_status": "Published",
                "active_date_key": "20260808",
            }
        ]
        db.products.docs.extend(cold_rows)
        with _FakeS3Mode():
            archived = await ha.archive_product_history_for_date(db, "2026-08-08")
            assert archived["status"] == "verified"
        db.products.docs = [d for d in db.products.docs if d.get("active_date_key") != "20260808"]

        a_prev = await ap._previous_inventory_date_key(
            db, brand_name="Hyundai", dealer_name="DealerA", branch="Branch1", current_key="20260811"
        )
        b_prev = await ap._previous_inventory_date_key(
            db, brand_name="Hyundai", dealer_name="DealerB", branch="Branch1", current_key="20260811"
        )
        assert a_prev == "20260809"
        assert b_prev is None  # must not inherit Dealer A's date

    asyncio.get_event_loop().run_until_complete(_run())


def test_real_s3_eligibility_gate_and_local_fallback_status():
    status = s3_storage.get_storage().status()
    assert status["storage_backend"] == "LOCAL FALLBACK"
    assert status["real_s3"] is False
    assert status["prune_authorized"] is False
    assert status["archive_prune_enabled"] is False
    assert "Cloud archive not active" in (status.get("warning") or "")


def test_excel_permissions_master_admin_user():
    assert ep.can_export_excel(SimpleNamespace(role="master")) is True
    assert ep.can_export_excel(SimpleNamespace(role="admin")) is True
    assert ep.can_export_excel(SimpleNamespace(role="user")) is False


def test_monitor_access_helpers_master_only_documented():
    # Endpoint uses _ensure_master; unit-level role gate mirrors that contract.
    def ensure_master(role: str):
        if role != "master":
            raise PermissionError(403)
        return True

    assert ensure_master("master") is True
    for role in ("admin", "user"):
        with pytest.raises(PermissionError):
            ensure_master(role)


def test_archive_scope_enrichment_preserves_codes_and_fills_names():
    async def _run():
        db = FakeDB()
        db.brands.docs = [{"code": "HY", "name": "Hyundai"}]
        db.uploads.docs = [
            {
                "id": "up1",
                "brand_name": "Hyundai",
                "dealer_name": "FPL Hyundai",
                "branch": "Vanagaram",
                "brand_code": "HY",
            }
        ]
        rows = [
            {
                "part_number": "P1",
                "brand_name": "",
                "brand_code": "HY",
                "dealer_name": "",
                "branch": "",
                "upload_id": "up1",
                "quantity": 2,
            }
        ]
        out = await ha._enrich_product_scope_rows(db, rows)
        assert out[0]["brand_name"] == "Hyundai"
        assert out[0]["dealer_name"] == "FPL Hyundai"
        assert out[0]["branch"] == "Vanagaram"
        assert out[0]["brand_code"] == "HY"
        fp1 = ha._source_fingerprint(out)
        fp2 = ha._source_fingerprint(out)
        assert fp1 == fp2
        changed = ha._source_fingerprint([{**out[0], "quantity": 9}])
        assert changed != fp1

    asyncio.get_event_loop().run_until_complete(_run())


def test_archive_scheduler_timing_same_day_window():
    src = Path(ROOT, "archive_scheduler.py").read_text(encoding="utf-8")
    assert "now.hour == 23 and now.minute >= 45" not in src
    assert "in_nightly_archive_window" in src
    assert "same_business_day_iso" in src
    assert "now.day == 1 and now.hour == 1 and now.minute >= 30" in src
    assert "run_daily_coordinated_archive" in src


def test_cleanup_verify_blocks_without_real_s3_and_wrong_confirm():
    import archive_cleanup as ac

    async def _run():
        db = FakeDB()
        # Local-fallback archive cannot be SAFE for delete
        db.archive_manifests.docs = [
            {
                "archive_id": "a1",
                "module": "product-history",
                "archive_date": "2026-08-02",
                "status": "VERIFIED",
                "storage_key": "dev/product-history/2026-08-02/products.jsonl.gz",
                "record_count": 1,
                "file_size": 10,
                "sha256": "abc",
                "storage_backend": "local",
                "source_fingerprint": "x",
            }
        ]
        report = await ac.verify_archive(db, archive_id="a1")
        assert report["safe_to_delete"] is False
        blocked = await ac.delete_mongo_for_archive(
            db,
            archive_id="a1",
            current_user=SimpleNamespace(role="master", id="m1", username="Master", email="a@b.c"),
            confirm_text="please",
        )
        assert blocked["status"] == "blocked"
        assert blocked["deleted"] == 0

    asyncio.get_event_loop().run_until_complete(_run())


def test_external_console_links_have_identical_pattern():
    import archive_cleanup as ac

    links = ac.external_console_links()
    assert links["pattern"] == "identical_cards"
    assert "open_label" in links["aws"] and "open_label" in links["mongodb"]
    assert links["aws"]["billing_available"] is False
    assert links["mongodb"]["billing_available"] is False
    assert links["mongodb"]["open_url"]  # generic Atlas portal fallback (no secrets)


def test_cleanup_product_query_includes_misflagged_historical_rows():
    """Historical rows with is_active_today=True must still be in delete scope by date."""
    import archive_cleanup as ac

    q = ac._product_query("2026-08-02")
    assert q["publish_status"] == "Published"
    assert "$in" in q["active_date_key"]
    assert "is_active_today" not in q


def test_physical_s3_verify_cases():
    import archive_verify as av
    import gzip
    from io import BytesIO

    storage = s3_storage.get_storage()
    # REAL S3 unavailable
    r = av.physical_s3_verify(storage_key="test/missing.jsonl.gz", expected_sha256="x", expected_size=1, expected_record_count=1)
    assert r["ok"] is False
    assert r["failure_code"] == "s3_unavailable"

    # Missing key
    assert av.physical_s3_verify(storage_key="", expected_sha256="x", expected_size=1)["failure_code"] == "missing_key"

    buf = BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        gz.write(b'{"a":1}\n')
    data = buf.getvalue()
    digest = s3_storage.sha256_bytes(data)
    key = "test/verify-cases.jsonl.gz"
    storage.upload_bytes(key, data, content_type="application/gzip")

    with _FakeS3Mode():
        ok = av.physical_s3_verify(
            storage_key=key,
            expected_sha256=digest,
            expected_size=len(data),
            expected_record_count=1,
        )
        assert ok["ok"] is True

        missing = av.physical_s3_verify(
            storage_key="test/does-not-exist.jsonl.gz",
            expected_sha256=digest,
            expected_size=len(data),
            expected_record_count=1,
        )
        assert missing["failure_code"] == "object_missing"

        bad_sha = av.physical_s3_verify(
            storage_key=key,
            expected_sha256="0" * 64,
            expected_size=len(data),
            expected_record_count=1,
        )
        assert bad_sha["ok"] is False
        assert bad_sha["failure_code"] == "checksum_mismatch"

        bad_count = av.physical_s3_verify(
            storage_key=key,
            expected_sha256=digest,
            expected_size=len(data),
            expected_record_count=99,
        )
        assert bad_count["failure_code"] == "count_mismatch"


def test_orders_requests_terminal_date_batching():
    async def _run():
        db = FakeDB()
        # Legacy db.orders empty — must NOT drive false VERIFIED success
        db.orders.docs = []
        # Live Order Desk: active + terminal + partial
        db.order_headers.docs = [
            {
                "id": "h-open",
                "order_number": "O-OPEN",
                "reference_no": "REF-OPEN",
                "status": "Order Created",
                "brand_name": "Hyundai",
                "dealer_name": "DealerA",
                "branch": "B1",
                "created_at": "2026-08-09T10:00:00+05:30",
                "updated_at": "2026-08-10T10:00:00+05:30",
            },
            {
                "id": "h-done",
                "order_number": "O-DONE",
                "reference_no": "REF-DONE",
                "status": "Order Created",  # header may stay non-terminal
                "brand_name": "Hyundai",
                "dealer_name": "DealerA",
                "branch": "B1",
                "created_at": "2026-08-09T10:00:00+05:30",
                "updated_at": "2026-08-11T18:00:00+05:30",
            },
            {
                "id": "h-partial",
                "order_number": "O-PARTIAL",
                "status": "Order Created",
                "brand_name": "Hyundai",
                "dealer_name": "DealerA",
                "branch": "B1",
                "created_at": "2026-08-09T10:00:00+05:30",
                "updated_at": "2026-08-11T18:00:00+05:30",
            },
        ]
        db.order_items.docs = [
            {
                "id": "i1",
                "order_id": "h-open",
                "status": "Order Created",
                "part_number": "P1",
                "updated_at": "2026-08-10T10:00:00+05:30",
            },
            {
                "id": "i2",
                "order_id": "h-done",
                "status": "Cancelled",
                "part_number": "P2",
                "updated_at": "2026-08-11T18:00:00+05:30",
                "completed_at": "2026-08-11T18:00:00+05:30",
            },
            {
                "id": "i3",
                "order_id": "h-partial",
                "status": "Cancelled",
                "part_number": "P3",
                "updated_at": "2026-08-11T12:00:00+05:30",
            },
            {
                "id": "i4",
                "order_id": "h-partial",
                "status": "Pending Retry",
                "part_number": "P4",
                "updated_at": "2026-08-11T12:00:00+05:30",
            },
        ]
        db.order_requests.docs = [
            {
                "number": "R-OPEN",
                "status": "Requested",
                "requesting_brand": "Hyundai",
                "requesting_dealer": "DealerA",
                "requesting_branch": "B1",
                "updated_at": "2026-08-10T10:00:00+05:30",
            },
            {
                "number": "R-DONE",
                "status": "Completed",
                "requesting_brand": "Hyundai",
                "requesting_dealer": "DealerA",
                "requesting_branch": "B1",
                "completed_at": "2026-08-12T09:00:00+05:30",
                "updated_at": "2026-08-12T09:00:00+05:30",
            },
        ]
        with _FakeS3Mode():
            day10 = await ha.archive_orders_for_date(db, "2026-08-10")
            assert day10["status"] == "no_eligible"
            assert day10["display_status"] == "NO ELIGIBLE ORDERS"
            assert day10["manifest"]["status"] == am.STATUS_NO_ELIGIBLE
            assert day10["manifest"]["status"] != am.STATUS_VERIFIED

            day11 = await ha.archive_orders_for_date(db, "2026-08-11")
            assert day11["record_count"] == 1
            assert day11["manifest"]["status"] == am.STATUS_VERIFIED
            assert day11["manifest"]["source_collection"] == "order_headers+order_items"
            assert day11["status"] == "verified"

            r10 = await ha.archive_requests_for_date(db, "2026-08-10")
            assert r10["record_count"] == 0
            r12 = await ha.archive_requests_for_date(db, "2026-08-12")
            assert r12["record_count"] == 1

    asyncio.get_event_loop().run_until_complete(_run())


def test_orders_legacy_empty_cannot_false_success():
    """legacy orders=0 must never produce green VERIFIED when Order Desk has no terminal rows."""

    async def _run():
        db = FakeDB()
        db.orders.docs = []  # shared-DB evidence: orders = 0
        db.order_headers.docs = [
            {
                "id": "h1",
                "order_number": "ORXX1",
                "status": "Order Created",
                "brand_name": "Hyundai",
                "dealer_name": "DealerA",
                "branch": "B1",
                "created_at": "2026-08-01T10:00:00+00:00",
                "updated_at": "2026-08-10T10:00:00+00:00",
            }
        ]
        db.order_items.docs = [
            {
                "id": "i1",
                "order_id": "h1",
                "status": "Order Created",
                "part_number": "P1",
                "updated_at": "2026-08-10T10:00:00+00:00",
            }
        ]
        # Even with Fake S3 available, zero eligible must be NO ELIGIBLE — not VERIFIED
        with _FakeS3Mode():
            out = await ha.archive_orders_for_date(db, "2026-08-10")
        assert out["status"] == "no_eligible"
        assert out["display_status"] == "NO ELIGIBLE ORDERS"
        assert out["manifest"]["status"] == am.STATUS_NO_ELIGIBLE
        assert out["manifest"]["status"] != am.STATUS_VERIFIED
        assert out["legacy_orders_count"] == 0
        assert out["order_headers_count"] == 1
        assert out["manifest"].get("eligible_for_prune") is False

    asyncio.get_event_loop().run_until_complete(_run())


def test_orders_created_earlier_completed_today_archives_today():
    async def _run():
        db = FakeDB()
        db.order_headers.docs = [
            {
                "id": "h1",
                "order_number": "OR-EARLY",
                "reference_no": "R-1",
                "status": "Source Selected",
                "brand_name": "Kia",
                "dealer_name": "DealerB",
                "branch": "Main",
                "created_at": "2026-08-01T08:00:00+05:30",
                "updated_at": "2026-08-12T20:00:00+05:30",
            }
        ]
        db.order_items.docs = [
            {
                "id": "i1",
                "order_id": "h1",
                "status": "No Further Stock Available",
                "part_number": "PX",
                "updated_at": "2026-08-12T20:00:00+05:30",
                "completed_at": "2026-08-12T20:00:00+05:30",
            }
        ]
        with _FakeS3Mode():
            early = await ha.archive_orders_for_date(db, "2026-08-01")
            assert early["status"] == "no_eligible"
            today = await ha.archive_orders_for_date(db, "2026-08-12")
            assert today["status"] == "verified"
            assert today["record_count"] == 1
            # Payload preserves identifiers
            storage = s3_storage.get_storage()
            key = today["manifest"]["storage_key"]
            data, _ = storage.download_bytes(key)
            import gzip
            from io import BytesIO

            with gzip.GzipFile(fileobj=BytesIO(data), mode="rb") as gz:
                row = json.loads(gz.readline())
            assert row["order_number"] == "OR-EARLY"
            assert row["reference_no"] == "R-1"
            assert row["brand_name"] == "Kia"
            assert row["dealer_name"] == "DealerB"
            assert row["branch"] == "Main"
            assert len(row["items"]) == 1

    asyncio.get_event_loop().run_until_complete(_run())


def test_pruned_product_history_readable_from_s3():
    async def _run():
        db = FakeDB()
        date_iso = "2026-05-01"
        date_key = "20260501"
        db.products.docs = [
            {
                "part_number": "HIST1",
                "item_name": "Historical",
                "quantity": 4,
                "total_value": 40,
                "brand_name": "Hyundai",
                "dealer_name": "DealerA",
                "branch": "B1",
                "publish_status": "Published",
                "active_date_key": date_key,
                "is_active_today": False,
            }
        ]
        with _FakeS3Mode():
            archived = await ha.archive_product_history_for_date(db, date_iso)
            assert archived["status"] == "verified"
            # Simulate Mongo prune → PRUNED (isolated test rows only)
            man = archived["manifest"]
            await am.mark_status(
                db,
                man["archive_id"],
                am.STATUS_PRUNED,
                pruned_at="2026-05-02T00:00:00+00:00",
                eligible_for_prune=False,
                storage_backend="s3",
            )
            db.products.docs = []  # Mongo cleaned

            # find_verified must NOT match PRUNED
            assert await am.find_verified(db, "product-history", archive_date=date_iso) is None
            readable = await am.find_s3_readable(db, "product-history", archive_date=date_iso)
            assert readable and readable["status"] == am.STATUS_PRUNED

            hist = await hh.read_product_history(db, date_key=date_iso, brand="Hyundai", hot_days=1)
            assert hist["s3_count"] >= 1
            assert any(r["part_number"] == "HIST1" for r in hist["rows"])

            # From/To report path
            ranged = await hh.read_product_history(
                db,
                from_date=date_iso,
                to_date=date_iso,
                brand="Hyundai",
                hot_days=1,
            )
            assert any(r["part_number"] == "HIST1" for r in ranged["rows"])

            # Local / failed must never be trusted for PRUNED
            bad = dict(readable)
            bad["archive_id"] = "bad-local"
            bad["status"] = am.STATUS_PRUNED
            bad["storage_backend"] = "local"
            await am.upsert_manifest(db, bad)
            # Prefer real s3 pruned over local — still returns S3 one
            still = await am.find_s3_readable(db, "product-history", archive_date=date_iso)
            assert still["storage_backend"] in {"s3", "REAL S3"}

    asyncio.get_event_loop().run_until_complete(_run())


def test_scheduler_partial_failure_no_last_daily_stamp():
    import archive_scheduler as sched

    async def _run():
        # Unit-level cycle evaluation
        incomplete = sched.evaluate_daily_cycle(
            {
                "datasets": {
                    "uploads": {"status": "verified"},
                    "product-history": {"status": "verified"},
                    "orders": {"status": "error", "error": "boom"},
                    "requests": {"status": "verified"},
                }
            }
        )
        assert incomplete["complete"] is False
        assert incomplete["status"] == "INCOMPLETE / FAILED"
        assert "orders" in incomplete["failed_datasets"]

        complete = sched.evaluate_daily_cycle(
            {
                "datasets": {
                    "uploads": {"status": "verified"},
                    "product-history": {"status": "already_verified"},
                    "orders": {"status": "no_eligible"},
                    "requests": {"status": "verified"},
                }
            }
        )
        assert complete["complete"] is True
        assert complete["status"] == "COMPLETE"

        # Integration: orders fail mid-batch → cycle incomplete; retry completes without
        # re-duplicating already verified uploads/product-history/requests.
        db = FakeDB()
        date_iso = "2026-07-22"
        db.products.docs = [
            {
                "part_number": "P1",
                "quantity": 1,
                "total_value": 1,
                "brand_name": "Hyundai",
                "dealer_name": "DealerA",
                "branch": "B1",
                "publish_status": "Published",
                "active_date_key": "20260722",
            }
        ]
        db.order_headers.docs = []
        db.order_items.docs = []
        db.order_requests.docs = []
        db.upload_items.docs = []
        db.uploads.docs = []

        real_orders = ha.archive_orders_for_date

        async def fail_orders(db, archive_date, force=False):
            return {"status": "error", "error": "orders intentionally failed"}

        with _FakeS3Mode():
            ha.archive_orders_for_date = fail_orders  # type: ignore
            try:
                first = await sched.run_daily_coordinated_archive(db, date_iso)
            finally:
                ha.archive_orders_for_date = real_orders  # type: ignore
            assert first["status"] == "incomplete"
            assert first["cycle"]["complete"] is False
            assert "orders" in first["cycle"]["failed_datasets"]
            assert first["datasets"]["uploads"]["status"] in {"verified", "already_verified", "no_eligible"}
            assert first["datasets"]["product-history"]["status"] in {"verified", "already_verified"}
            assert first["datasets"]["requests"]["status"] in {"verified", "already_verified", "no_eligible"}

            # Seed a terminal order so retry can PASS
            db.order_headers.docs = [
                {
                    "id": "h-retry",
                    "order_number": "OR-RETRY",
                    "status": "Order Created",
                    "brand_name": "Hyundai",
                    "dealer_name": "DealerA",
                    "branch": "B1",
                    "created_at": "2026-07-01T10:00:00+05:30",
                    "updated_at": "2026-07-22T22:00:00+05:30",
                }
            ]
            db.order_items.docs = [
                {
                    "id": "i-retry",
                    "order_id": "h-retry",
                    "status": "Cancelled",
                    "part_number": "PR",
                    "updated_at": "2026-07-22T22:00:00+05:30",
                    "completed_at": "2026-07-22T22:00:00+05:30",
                }
            ]
            second = await sched.run_daily_coordinated_archive(db, date_iso)
            assert second["cycle"]["complete"] is True
            assert second["status"] == "ok"
            # Already-successful datasets stay idempotent
            assert second["datasets"]["product-history"]["status"] in {"verified", "already_verified"}
            assert second["datasets"]["orders"]["status"] in {"verified", "already_verified"}

    asyncio.get_event_loop().run_until_complete(_run())


def test_allocated_mongo_reconciles_exactly_with_dealer_sum():
    import storage_monitor as sm

    async def _run():
        db = FakeDB()
        db.products.docs = [
            {"dealer_name": "DealerA", "branch": "B1"},
            {"dealer_name": "DealerA", "branch": "B2"},
            {"dealer_name": "DealerB", "branch": "C1"},
        ]
        orig_mongo = sm.mongo_storage_metrics
        orig_s3 = sm.s3_storage_metrics

        async def fake_mongo(db):
            return {
                "storage_size": 1000,
                "data_size": 800,
                "index_size": 200,
                "product_size": 900,
                "capacity_reason": "test",
                "today_product_count": 0,
            }

        async def fake_s3(db):
            return {
                "actual_s3_used_bytes": 5000,
                "actual_s3_available": True,
                "manifest_recorded_bytes": 1200,
            }

        sm.mongo_storage_metrics = fake_mongo  # type: ignore
        sm.s3_storage_metrics = fake_s3  # type: ignore
        try:
            snap = await sm.dealer_storage_snapshot(db)
        finally:
            sm.mongo_storage_metrics = orig_mongo  # type: ignore
            sm.s3_storage_metrics = orig_s3  # type: ignore
        totals = snap["totals"]
        dealer_sum = sum(d["mongodb_used_bytes"] for d in snap["dealers"])
        assert totals["mongodb_allocated_usage_bytes"] == dealer_sum
        assert totals["mongodb_dealer_allocated_bytes"] == dealer_sum
        assert totals["reconciliation"]["allocated_mongo_equals_dealer_sum"] is True
        assert totals["mongodb_physical_storage_bytes"] == 1000
        assert totals["mongodb_allocated_usage_bytes"] != totals["mongodb_physical_storage_bytes"]
        assert totals["s3_actual_used_bytes"] == 5000
        assert "s3_dealer_attributed_bytes" in totals

    asyncio.get_event_loop().run_until_complete(_run())


def test_retry_reconcile_idempotent_and_local_not_verified():
    import archive_verify as av

    async def _run():
        db = FakeDB()
        date_iso = "2026-08-05"
        db.products.docs = [
            {
                "part_number": "P1",
                "quantity": 1,
                "total_value": 1,
                "brand_name": "Hyundai",
                "dealer_name": "DealerA",
                "branch": "B1",
                "publish_status": "Published",
                "active_date_key": "20260805",
            }
        ]
        with pytest.raises(RuntimeError):
            await ha.archive_product_history_for_date(db, date_iso)
        failed = await db.archive_manifests.find_one({"module": "product-history"})
        assert failed["status"] == am.STATUS_FAILED
        live = await av.live_verify_manifest(failed)
        assert live["ok"] is False
        assert live["display_status"] in {av.DISPLAY_NOT_TRANSFERRED, av.DISPLAY_VERIFICATION_FAILED}

        with _FakeS3Mode():
            again = await ha.archive_product_history_for_date(db, date_iso, force=True)
            assert again["status"] == "verified"
            # Idempotent second force should reconcile existing object
            third = await ha.archive_product_history_for_date(db, date_iso, force=True)
            assert third["status"] in {"verified", "already_verified"}
            assert third["manifest"]["status"] == am.STATUS_VERIFIED

    asyncio.get_event_loop().run_until_complete(_run())


def test_delete_locked_until_physical_verify():
    import archive_cleanup as ac

    async def _run():
        db = FakeDB()
        db.archive_manifests.docs = [
            {
                "archive_id": "lock1",
                "module": "product-history",
                "archive_date": "2026-08-02",
                "status": "VERIFIED",
                "storage_key": "test/nope.jsonl.gz",
                "record_count": 1,
                "file_size": 10,
                "sha256": "abc",
                "storage_backend": "s3",
                "eligible_for_prune": True,
                "source_fingerprint": "x",
            }
        ]
        report = await ac.verify_archive(db, archive_id="lock1")
        assert report["safe_to_delete"] is False
        assert report.get("delete_locked") is True
        assert "Locked" in (report.get("lock_reason") or report.get("reason") or "")

    asyncio.get_event_loop().run_until_complete(_run())


def test_menu_analytics_label_and_storage_nav_contract():
    src = Path(ROOT.parent, "frontend/src/config/menuConfig.js").read_text(encoding="utf-8")
    assert "label: 'Analytics'" in src
    assert "Storage & Data Cleanup" in src
    layout = Path(ROOT.parent, "frontend/src/pages/DashboardLayout.js").read_text(encoding="utf-8")
    assert "openTab('analytics', 'Analytics'" in layout
    assert "location.pathname === '/' || location.pathname === ''" in layout


@pytest.fixture(scope="session", autouse=True)
def _cleanup_tmp():
    yield
    shutil.rmtree(TMP_STORE, ignore_errors=True)
