"""Daily / monthly archive builders for Product History, Orders, Requests, Verifications."""

from __future__ import annotations

import gzip
import json
import logging
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import Any, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

import archive_manifest as am
from s3_storage import get_storage, sha256_bytes

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

MODULE_PRODUCT_HISTORY = "product-history"
MODULE_ORDERS = "orders"
MODULE_REQUESTS = "requests"
MODULE_VERIFICATIONS = "verifications"
MODULE_ANALYTICS_SNAPSHOT = "analytics-snapshots"


def _ist_now() -> datetime:
    return datetime.now(IST)


def date_key_to_iso(date_key: str) -> str:
    dk = (date_key or "").replace("-", "")[:8]
    if len(dk) != 8:
        return date_key or ""
    return f"{dk[0:4]}-{dk[4:6]}-{dk[6:8]}"


def iso_to_date_key(value: str) -> str:
    return (value or "").replace("-", "")[:8]


def _jsonable(doc: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    for k, v in doc.items():
        if k == "_id":
            continue
        if isinstance(v, datetime):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


def _gzip_jsonl(rows: Iterable[Dict[str, Any]]) -> bytes:
    buf = BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        for row in rows:
            line = json.dumps(_jsonable(row), ensure_ascii=False, default=str) + "\n"
            gz.write(line.encode("utf-8"))
    return buf.getvalue()


def _gzip_json(payload: Any) -> bytes:
    raw = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    buf = BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        gz.write(raw)
    return buf.getvalue()


async def _upload_verified(
    db,
    *,
    module: str,
    archive_date: Optional[str],
    archive_month: Optional[str],
    storage_key: str,
    data: bytes,
    source_collection: str,
    record_count: int,
    min_date: Optional[str],
    max_date: Optional[str],
    brands: set,
    dealers: set,
    branches: set,
    existing: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    storage = get_storage()
    digest = sha256_bytes(data)
    manifest = existing or am.base_manifest(
        module=module,
        archive_date=archive_date,
        archive_month=archive_month,
        storage_key=storage_key,
        format="jsonl.gz",
        source_collection=source_collection,
    )
    manifest.update(
        {
            "storage_key": storage_key,
            "record_count": record_count,
            "file_size": len(data),
            "sha256": digest,
            "status": am.STATUS_CREATING,
            "source_collection": source_collection,
            "min_date": min_date,
            "max_date": max_date,
            "brand_count": len(brands),
            "dealer_count": len(dealers),
            "branch_count": len(branches),
            "error": None,
            "eligible_for_prune": False,
        }
    )
    manifest = await am.upsert_manifest(db, manifest)

    try:
        storage.upload_bytes(storage_key, data, content_type="application/gzip")
        await am.mark_status(db, manifest["archive_id"], am.STATUS_UPLOADED)
    except Exception as exc:
        await am.mark_status(
            db,
            manifest["archive_id"],
            am.STATUS_FAILED,
            error=str(exc)[:1000],
            eligible_for_prune=False,
        )
        raise

    # Verify exists + size + sha256
    ok = storage.verify_object(storage_key, digest, len(data))
    if not ok:
        await am.mark_status(
            db,
            manifest["archive_id"],
            am.STATUS_FAILED,
            error="S3 integrity verification failed",
            eligible_for_prune=False,
        )
        raise RuntimeError("Archive integrity verification failed")

    # Verify archive can be read and record count matches (jsonl.gz only)
    try:
        read_back, _ = storage.download_bytes(storage_key)
        if storage_key.endswith(".jsonl.gz") or (manifest.get("format") == "jsonl.gz"):
            read_count = 0
            with gzip.GzipFile(fileobj=BytesIO(read_back), mode="rb") as gz:
                for line in gz:
                    if line.strip():
                        read_count += 1
            if int(read_count) != int(record_count):
                await am.mark_status(
                    db,
                    manifest["archive_id"],
                    am.STATUS_FAILED,
                    error=f"Archive record count mismatch: expected={record_count} got={read_count}",
                    eligible_for_prune=False,
                )
                raise RuntimeError("Archive record count verification failed")
        elif sha256_bytes(read_back) != digest:
            await am.mark_status(
                db,
                manifest["archive_id"],
                am.STATUS_FAILED,
                error="Archive read-back checksum mismatch",
                eligible_for_prune=False,
            )
            raise RuntimeError("Archive read-back verification failed")
    except RuntimeError:
        raise
    except Exception as exc:
        await am.mark_status(
            db,
            manifest["archive_id"],
            am.STATUS_FAILED,
            error=f"Archive read-back failed: {exc}"[:1000],
            eligible_for_prune=False,
        )
        raise

    # Prune eligibility requires REAL S3 — local fallback never authorizes prune.
    eligible = bool(storage.is_s3())
    return await am.mark_status(
        db,
        manifest["archive_id"],
        am.STATUS_VERIFIED,
        eligible_for_prune=eligible,
        storage_backend="s3" if eligible else storage.mode,
    )


# -------------------- Product History (daily) --------------------

async def archive_product_history_for_date(db, archive_date: str, force: bool = False) -> Dict[str, Any]:
    """Archive ALL brands/dealers/branches for one IST calendar date.

    Sequence: read Mongo → build → upload → verify exists/size/sha256 →
    write manifest VERIFIED → mark eligible for future prune (prune itself off).
    Idempotent: verified archive for date is returned as-is unless force=True.
    """
    date_iso = date_key_to_iso(archive_date)
    date_key = iso_to_date_key(date_iso)
    existing = await am.find_verified(db, MODULE_PRODUCT_HISTORY, archive_date=date_iso)
    if existing and not force:
        return {"status": "already_verified", "manifest": existing}

    failed_or_partial = await am.find_any(db, MODULE_PRODUCT_HISTORY, archive_date=date_iso)

    query = {
        "publish_status": "Published",
        "active_date_key": {"$in": [date_key, date_iso]},
    }
    cursor = db.products.find(query, {"_id": 0})
    rows = await cursor.to_list(500000)

    # Also include batch summaries + a compact analytics snapshot payload for the day
    summaries = await db.batch_summaries.find(
        {"active_date_key": {"$in": [date_key, date_iso]}},
        {"_id": 0},
    ).to_list(50000)

    brands, dealers, branches = set(), set(), set()
    for r in rows:
        if r.get("brand_name"):
            brands.add(str(r["brand_name"]))
        if r.get("dealer_name"):
            dealers.add(str(r["dealer_name"]))
        if r.get("branch"):
            branches.add(str(r["branch"]))

    storage = get_storage()
    products_key = storage.key("product-history", date_iso, "products.jsonl.gz")
    summary_key = storage.key("product-history", date_iso, "batch-summaries.json")
    snap_key = storage.key("product-history", date_iso, "analytics-snapshots.json")

    products_bytes = _gzip_jsonl(rows)
    summary_bytes = json.dumps(summaries, ensure_ascii=False, default=str).encode("utf-8")

    # Build analytics snapshot payload in-memory first. Upload/verify the
    # authoritative products archive BEFORE writing snapshots back to Mongo so
    # archival still succeeds when Atlas is over quota / write-blocked.
    snap_docs = _build_analytics_snapshot_docs(date_iso, rows)
    snap_bytes = json.dumps(snap_docs, ensure_ascii=False, default=str).encode("utf-8")

    # Upload companion objects (best-effort; main products archive is authoritative)
    try:
        storage.upload_bytes(summary_key, summary_bytes, content_type="application/json")
        storage.upload_bytes(snap_key, snap_bytes, content_type="application/json")
    except Exception as exc:
        logger.warning("Companion archive upload failed for %s: %s", date_iso, exc)

    manifest = await _upload_verified(
        db,
        module=MODULE_PRODUCT_HISTORY,
        archive_date=date_iso,
        archive_month=date_iso[:7],
        storage_key=products_key,
        data=products_bytes,
        source_collection="products",
        record_count=len(rows),
        min_date=date_iso,
        max_date=date_iso,
        brands=brands,
        dealers=dealers,
        branches=branches,
        existing=None if force else failed_or_partial,
    )

    # Best-effort Mongo snapshot upsert (may fail when cluster is over quota)
    try:
        await write_analytics_snapshots_for_date(db, date_iso, rows)
    except Exception as exc:
        logger.warning("Analytics snapshot Mongo write skipped for %s: %s", date_iso, exc)

    try:
        import storage_usage as su

        await su.record_storage_usage(
            db,
            operation=su.OP_ARCHIVE_WRITE,
            bytes_count=len(products_bytes),
            module=MODULE_PRODUCT_HISTORY,
            request_count=1,
        )
    except Exception:
        pass
    return {"status": "verified", "manifest": manifest, "record_count": len(rows)}


def _build_analytics_snapshot_docs(date_iso: str, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    date_key = iso_to_date_key(date_iso)
    date_iso = date_key_to_iso(date_key)
    docs: List[Dict[str, Any]] = []
    for r in rows:
        qty = float(r.get("available_qty_number", r.get("quantity", 0)) or 0)
        unit = float(r.get("unit_value_number", r.get("mav_value", r.get("mav", 0))) or 0)
        value = float(r.get("total_value_number", r.get("total_value", qty * unit)) or 0)
        docs.append(
            {
                "snapshot_date_ist": date_iso,
                "brand_id": r.get("brand_id") or r.get("brand_code") or "",
                "dealer_id": r.get("dealer_id") or r.get("dealer_code") or "",
                "branch_id": r.get("branch_id") or r.get("branch") or "",
                "brand_name": r.get("brand_name"),
                "dealer_name": r.get("dealer_name"),
                "branch_name": r.get("branch"),
                "part_number": r.get("part_number"),
                "part_name": r.get("item_name") or r.get("part_name"),
                "category": r.get("part_category") or r.get("category") or "",
                "available_qty": qty,
                "unit_value": unit,
                "total_value": value,
                "purchase_aging_days": r.get("purchase_aging_days"),
                "sales_aging_days": r.get("sales_aging_days"),
                "last_receipt_date": r.get("last_receipt_date"),
                "last_sales_date": r.get("last_sales_date"),
                "upload_no": r.get("upload_no"),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    return docs


async def write_analytics_snapshots_for_date(db, date_iso: str, rows: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """Populate analytics_stock_daily_snapshots for a date (idempotent upsert)."""
    date_key = iso_to_date_key(date_iso)
    date_iso = date_key_to_iso(date_key)
    if rows is None:
        rows = await db.products.find(
            {"publish_status": "Published", "active_date_key": {"$in": [date_key, date_iso]}},
            {"_id": 0},
        ).to_list(500000)

    docs = _build_analytics_snapshot_docs(date_iso, rows)
    for doc in docs:
        await db.analytics_stock_daily_snapshots.update_one(
            {
                "snapshot_date_ist": date_iso,
                "brand_id": doc["brand_id"],
                "dealer_id": doc["dealer_id"],
                "branch_id": doc["branch_id"],
                "part_number": doc["part_number"],
            },
            {"$set": doc},
            upsert=True,
        )
    return docs


# -------------------- Orders / Requests (monthly) --------------------

TERMINAL_ORDER_STATUSES = {"Completed", "Cancelled", "Cancelled – No Response", "No Further Stock Available"}
TERMINAL_REQUEST_STATUSES = {"Completed", "Rejected", "Cancelled"}

# Open / in-flight statuses must NEVER be archived
OPEN_REQUEST_STATUSES = {
    "Requested",
    "Approved",
    "Partially Approved",
    "Dispatched",
    "Received",
}


def _prev_calendar_month(now: Optional[datetime] = None) -> Tuple[str, datetime, datetime]:
    now = now or _ist_now()
    first_this = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last_prev = first_this - timedelta(days=1)
    first_prev = last_prev.replace(day=1)
    month = first_prev.strftime("%Y-%m")
    return month, first_prev, first_this


def _parse_dt(value) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text)
    except Exception:
        return None


async def archive_completed_orders_month(db, archive_month: Optional[str] = None, force: bool = False) -> Dict[str, Any]:
    if not archive_month:
        archive_month, start, end = _prev_calendar_month()
    else:
        year, month = map(int, archive_month.split("-"))
        start = datetime(year, month, 1, tzinfo=IST)
        if month == 12:
            end = datetime(year + 1, 1, 1, tzinfo=IST)
        else:
            end = datetime(year, month + 1, 1, tzinfo=IST)

    existing = await am.find_verified(db, MODULE_ORDERS, archive_month=archive_month)
    if existing and not force:
        return {"status": "already_verified", "manifest": existing}

    # Prefer order_requests / order_desk structures — archive fully terminal only
    rows = []
    brands, dealers, branches = set(), set(), set()

    # Primary: order_requests that are Completed/Cancelled/Rejected for the month
    cursor = db.order_requests.find(
        {
            "status": {"$in": list(TERMINAL_REQUEST_STATUSES)},
        },
        {"_id": 0},
    )
    candidates = await cursor.to_list(200000)
    for r in candidates:
        # Skip open/incomplete
        if r.get("status") in OPEN_REQUEST_STATUSES:
            continue
        completed = (
            _parse_dt(r.get("completed_at"))
            or _parse_dt(r.get("updated_at"))
            or _parse_dt(r.get("created_at"))
        )
        if not completed:
            continue
        if completed.tzinfo is None:
            completed = completed.replace(tzinfo=timezone.utc)
        completed_ist = completed.astimezone(IST)
        if not (start <= completed_ist < end):
            continue
        rows.append(r)
        if r.get("brand_name") or r.get("brand"):
            brands.add(str(r.get("brand_name") or r.get("brand")))
        if r.get("dealer_name") or r.get("requesting_dealer") or r.get("supplying_dealer"):
            dealers.add(str(r.get("dealer_name") or r.get("requesting_dealer") or r.get("supplying_dealer")))
        if r.get("branch") or r.get("requesting_branch") or r.get("supplying_branch"):
            branches.add(str(r.get("branch") or r.get("requesting_branch") or r.get("supplying_branch")))

    # Also pull completed order_desk headers if present
    order_rows = []
    try:
        ocursor = db.orders.find({"status": {"$in": list(TERMINAL_ORDER_STATUSES)}}, {"_id": 0})
        for o in await ocursor.to_list(100000):
            completed = _parse_dt(o.get("completed_at")) or _parse_dt(o.get("updated_at")) or _parse_dt(o.get("created_at"))
            if not completed:
                continue
            if completed.tzinfo is None:
                completed = completed.replace(tzinfo=timezone.utc)
            completed_ist = completed.astimezone(IST)
            if start <= completed_ist < end:
                order_rows.append(o)
    except Exception:
        order_rows = []

    combined = {"requests": rows, "orders": order_rows}
    # Store requests archive separately via archive_completed_requests_month;
    # here store order headers when available, else empty verified placeholder with requests count note.
    payload_rows = order_rows if order_rows else rows
    storage = get_storage()
    key = storage.key("orders", archive_month, "completed-orders.jsonl.gz")
    data = _gzip_jsonl(payload_rows)

    manifest = await _upload_verified(
        db,
        module=MODULE_ORDERS,
        archive_date=None,
        archive_month=archive_month,
        storage_key=key,
        data=data,
        source_collection="orders",
        record_count=len(payload_rows),
        min_date=start.date().isoformat(),
        max_date=(end - timedelta(days=1)).date().isoformat(),
        brands=brands,
        dealers=dealers,
        branches=branches,
    )

    # Lightweight Mongo index for search
    for r in payload_rows:
        number = r.get("order_no") or r.get("order_id") or r.get("request_no") or r.get("id")
        idx = {
            "number": number,
            "brand": r.get("brand_name") or r.get("brand"),
            "dealer": r.get("dealer_name") or r.get("requesting_dealer") or r.get("supplying_dealer"),
            "branch": r.get("branch") or r.get("requesting_branch") or r.get("supplying_branch"),
            "created_date": r.get("created_at"),
            "completed_date": r.get("completed_at") or r.get("updated_at"),
            "status": r.get("status"),
            "total_items": r.get("total_items") or r.get("item_count"),
            "total_qty": r.get("total_qty") or r.get("quantity"),
            "total_value": r.get("total_value"),
            "archive_month": archive_month,
            "storage_key": key,
        }
        if number:
            await db.order_archive_index.update_one(
                {"number": number, "archive_month": archive_month},
                {"$set": idx},
                upsert=True,
            )

    return {"status": "verified", "manifest": manifest, "record_count": len(payload_rows), "combined_meta": {"requests": len(rows), "orders": len(order_rows)}}


async def archive_completed_requests_month(db, archive_month: Optional[str] = None, force: bool = False) -> Dict[str, Any]:
    if not archive_month:
        archive_month, start, end = _prev_calendar_month()
    else:
        year, month = map(int, archive_month.split("-"))
        start = datetime(year, month, 1, tzinfo=IST)
        if month == 12:
            end = datetime(year + 1, 1, 1, tzinfo=IST)
        else:
            end = datetime(year, month + 1, 1, tzinfo=IST)

    existing = await am.find_verified(db, MODULE_REQUESTS, archive_month=archive_month)
    if existing and not force:
        return {"status": "already_verified", "manifest": existing}

    brands, dealers, branches = set(), set(), set()
    rows = []
    cursor = db.order_requests.find({"status": {"$in": list(TERMINAL_REQUEST_STATUSES)}}, {"_id": 0})
    for r in await cursor.to_list(200000):
        if r.get("status") in OPEN_REQUEST_STATUSES:
            continue
        completed = (
            _parse_dt(r.get("completed_at"))
            or _parse_dt(r.get("updated_at"))
            or _parse_dt(r.get("created_at"))
        )
        if not completed:
            continue
        if completed.tzinfo is None:
            completed = completed.replace(tzinfo=timezone.utc)
        completed_ist = completed.astimezone(IST)
        if not (start <= completed_ist < end):
            continue
        rows.append(r)
        if r.get("brand_name") or r.get("brand"):
            brands.add(str(r.get("brand_name") or r.get("brand")))
        if r.get("dealer_name") or r.get("requesting_dealer") or r.get("supplying_dealer"):
            dealers.add(str(r.get("dealer_name") or r.get("requesting_dealer") or r.get("supplying_dealer")))
        if r.get("branch") or r.get("requesting_branch") or r.get("supplying_branch"):
            branches.add(str(r.get("branch") or r.get("requesting_branch") or r.get("supplying_branch")))

    storage = get_storage()
    key = storage.key("requests", archive_month, "completed-requests.jsonl.gz")
    data = _gzip_jsonl(rows)
    manifest = await _upload_verified(
        db,
        module=MODULE_REQUESTS,
        archive_date=None,
        archive_month=archive_month,
        storage_key=key,
        data=data,
        source_collection="order_requests",
        record_count=len(rows),
        min_date=start.date().isoformat(),
        max_date=(end - timedelta(days=1)).date().isoformat(),
        brands=brands,
        dealers=dealers,
        branches=branches,
    )

    for r in rows:
        number = r.get("request_no") or r.get("request_number") or r.get("id")
        idx = {
            "number": number,
            "brand": r.get("brand_name") or r.get("brand"),
            "dealer": r.get("dealer_name") or r.get("requesting_dealer") or r.get("supplying_dealer"),
            "branch": r.get("branch") or r.get("requesting_branch") or r.get("supplying_branch"),
            "created_date": r.get("created_at"),
            "completed_date": r.get("completed_at") or r.get("updated_at"),
            "status": r.get("status"),
            "total_items": r.get("total_items") or r.get("item_count"),
            "total_qty": r.get("total_qty") or r.get("quantity"),
            "total_value": r.get("total_value"),
            "archive_month": archive_month,
            "storage_key": key,
        }
        if number:
            await db.request_archive_index.update_one(
                {"number": number, "archive_month": archive_month},
                {"$set": idx},
                upsert=True,
            )

    return {"status": "verified", "manifest": manifest, "record_count": len(rows)}


# -------------------- Verification history --------------------

async def archive_verifications_for_date(db, archive_date: str, force: bool = False) -> Dict[str, Any]:
    date_iso = date_key_to_iso(archive_date)
    existing = await am.find_verified(db, MODULE_VERIFICATIONS, archive_date=date_iso)
    if existing and not force:
        return {"status": "already_verified", "manifest": existing}

    # Collect rows whose verified_at falls on that IST day
    day_start = datetime.fromisoformat(date_iso).replace(tzinfo=IST)
    day_end = day_start + timedelta(days=1)
    rows = []
    brands, dealers, branches = set(), set(), set()
    cursor = db.stock_verification_history.find({}, {"_id": 0})
    for r in await cursor.to_list(500000):
        verified = _parse_dt(r.get("verified_at")) or _parse_dt(r.get("created_at"))
        if not verified:
            continue
        if verified.tzinfo is None:
            verified = verified.replace(tzinfo=timezone.utc)
        verified_ist = verified.astimezone(IST)
        if day_start <= verified_ist < day_end:
            rows.append(r)
            if r.get("brand_name"):
                brands.add(str(r["brand_name"]))
            if r.get("dealer_name"):
                dealers.add(str(r["dealer_name"]))
            if r.get("branch"):
                branches.add(str(r["branch"]))

    storage = get_storage()
    key = storage.key("verifications", date_iso, "verifications.jsonl.gz")
    data = _gzip_jsonl(rows)
    manifest = await _upload_verified(
        db,
        module=MODULE_VERIFICATIONS,
        archive_date=date_iso,
        archive_month=date_iso[:7],
        storage_key=key,
        data=data,
        source_collection="stock_verification_history",
        record_count=len(rows),
        min_date=date_iso,
        max_date=date_iso,
        brands=brands,
        dealers=dealers,
        branches=branches,
    )
    return {"status": "verified", "manifest": manifest, "record_count": len(rows)}


# -------------------- Prune (disabled by default; REAL S3 required) --------------------

def _today_ist_keys() -> Tuple[str, str]:
    today = _ist_now().date()
    return today.strftime("%Y%m%d"), today.isoformat()


async def prune_product_history_date(db, archive_date: str, *, force: bool = False) -> Dict[str, Any]:
    """Delete Mongo product rows for one VERIFIED historical date only.

    Safety gates (all required unless force is used for tests with real S3):
    1. ARCHIVE_PRUNE_ENABLED=true
    2. Storage backend is REAL S3 (local fallback never prunes)
    3. Manifest status VERIFIED with matching checksum/size
    4. Date is NOT today (never touch live/current Product Hub set)
    5. Re-verify object exists before delete
    """
    from s3_storage import archive_prune_enabled, get_storage, product_mongo_hot_days

    storage = get_storage()
    date_iso = date_key_to_iso(archive_date)
    date_key = iso_to_date_key(date_iso)
    today_key, today_iso = _today_ist_keys()

    if date_key == today_key or date_iso == today_iso:
        return {
            "status": "blocked",
            "reason": "refusing to prune today's live Product dataset",
            "archive_date": date_iso,
            "deleted": 0,
        }

    if not archive_prune_enabled():
        return {
            "status": "skipped",
            "reason": "ARCHIVE_PRUNE_ENABLED=false",
            "archive_date": date_iso,
            "deleted": 0,
        }

    if not storage.is_s3():
        return {
            "status": "blocked",
            "reason": "Cloud archive not active — MongoDB pruning disabled.",
            "storage_backend": storage.mode,
            "archive_date": date_iso,
            "deleted": 0,
        }

    # Respect hot window: never prune dates still inside PRODUCT_MONGO_HOT_DAYS
    hot_days = product_mongo_hot_days()
    hot_cutoff = (_ist_now().date() - timedelta(days=max(0, hot_days - 1))).strftime("%Y%m%d")
    if date_key >= hot_cutoff:
        return {
            "status": "blocked",
            "reason": f"date still inside product hot window (hot_days={hot_days})",
            "archive_date": date_iso,
            "deleted": 0,
        }

    manifest = await am.find_verified(db, MODULE_PRODUCT_HISTORY, archive_date=date_iso)
    if not manifest:
        return {
            "status": "blocked",
            "reason": "no VERIFIED archive manifest for date",
            "archive_date": date_iso,
            "deleted": 0,
        }
    if not manifest.get("eligible_for_prune") and not force:
        return {
            "status": "blocked",
            "reason": "manifest not eligible_for_prune (requires REAL S3 verification)",
            "archive_date": date_iso,
            "deleted": 0,
        }

    # Re-verify object before any delete
    key = manifest.get("storage_key") or ""
    if not storage.verify_object(key, manifest.get("sha256") or "", int(manifest.get("file_size") or 0)):
        await am.mark_status(
            db,
            manifest["archive_id"],
            am.STATUS_FAILED,
            error="Pre-prune re-verification failed",
            eligible_for_prune=False,
        )
        return {
            "status": "failed",
            "reason": "pre-prune re-verification failed — Mongo untouched",
            "archive_date": date_iso,
            "deleted": 0,
        }

    query = {
        "publish_status": "Published",
        "active_date_key": {"$in": [date_key, date_iso]},
    }
    # Never delete rows still marked as today's active set
    query["is_active_today"] = {"$ne": True}

    before = await db.products.count_documents(query)
    result = await db.products.delete_many(query)
    deleted = int(getattr(result, "deleted_count", 0) or 0)

    # Companion batch summaries for that historical date (not today's active)
    sum_q = {"active_date_key": {"$in": [date_key, date_iso]}}
    sum_res = await db.batch_summaries.delete_many(sum_q)
    summaries_deleted = int(getattr(sum_res, "deleted_count", 0) or 0)

    await am.mark_status(
        db,
        manifest["archive_id"],
        am.STATUS_PRUNED,
        pruned_at=_ist_now().astimezone(timezone.utc).isoformat(),
        pruned_product_count=deleted,
        pruned_summary_count=summaries_deleted,
        eligible_for_prune=False,
    )
    return {
        "status": "pruned",
        "archive_date": date_iso,
        "deleted": deleted,
        "summaries_deleted": summaries_deleted,
        "counted_before": before,
        "manifest_id": manifest.get("archive_id"),
    }


async def prune_eligible_mongo_history(db, module: str = MODULE_PRODUCT_HISTORY) -> Dict[str, Any]:
    """Prune all VERIFIED eligible product-history dates one-by-one (never mass-blind)."""
    from s3_storage import archive_prune_enabled, get_storage

    if module != MODULE_PRODUCT_HISTORY:
        return {
            "status": "skipped",
            "reason": f"prune not implemented for module={module}",
            "deleted": 0,
        }
    if not archive_prune_enabled():
        return {
            "status": "skipped",
            "reason": "ARCHIVE_PRUNE_ENABLED=false",
            "deleted": 0,
        }
    if not get_storage().is_s3():
        return {
            "status": "blocked",
            "reason": "Cloud archive not active — MongoDB pruning disabled.",
            "deleted": 0,
        }

    manifests = await db.archive_manifests.find(
        {
            "module": MODULE_PRODUCT_HISTORY,
            "status": am.STATUS_VERIFIED,
            "eligible_for_prune": True,
        },
        {"_id": 0},
    ).to_list(5000)

    results = []
    total_deleted = 0
    for m in manifests:
        date_iso = m.get("archive_date")
        if not date_iso:
            continue
        one = await prune_product_history_date(db, date_iso)
        results.append(one)
        total_deleted += int(one.get("deleted") or 0)
    return {
        "status": "ok",
        "dates": len(results),
        "deleted": total_deleted,
        "results": results,
    }


async def cleanup_published_upload_items(db, *, dry_run: bool = True) -> Dict[str, Any]:
    """Remove obsolete Published staging rows that are no longer needed.

    Keeps Waiting/pending staging rows untouched. Published product truth already
    lives in `products` (and archives). This is the preferred first step when
    Atlas is over quota and cannot accept new manifest writes.
    """
    query = {"publish_status": "Published"}
    count = await db.upload_items.count_documents(query)
    waiting = await db.upload_items.count_documents({"publish_status": {"$ne": "Published"}})
    if dry_run:
        return {
            "status": "dry_run",
            "published_upload_items": count,
            "retained_non_published": waiting,
            "action": "would_delete_published_staging_only",
        }
    result = await db.upload_items.delete_many(query)
    deleted = int(getattr(result, "deleted_count", 0) or 0)
    return {
        "status": "cleaned",
        "deleted": deleted,
        "retained_non_published": waiting,
    }


async def list_historical_product_dates(db) -> List[Dict[str, Any]]:
    """Group published product rows by active_date_key (excluding today)."""
    today_key, today_iso = _today_ist_keys()
    pipeline = [
        {"$match": {"publish_status": "Published"}},
        {"$group": {"_id": "$active_date_key", "count": {"$sum": 1}}},
        {"$sort": {"_id": 1}},
    ]
    try:
        rows = await db.products.aggregate(pipeline).to_list(5000)
    except Exception:
        # FakeDB / drivers without aggregate — fall back
        counts: Counter = Counter()
        for doc in await db.products.find({"publish_status": "Published"}, {"_id": 0, "active_date_key": 1}).to_list(500000):
            counts[str(doc.get("active_date_key") or "")] += 1
        rows = [{"_id": k, "count": v} for k, v in sorted(counts.items()) if k]

    out = []
    for r in rows:
        raw = str(r.get("_id") or "")
        dk = iso_to_date_key(raw) if "-" in raw else raw.replace("-", "")[:8]
        if not dk or dk in {today_key, today_iso.replace("-", "")}:
            continue
        if raw in {today_key, today_iso}:
            continue
        out.append(
            {
                "active_date_key": raw,
                "date_iso": date_key_to_iso(dk),
                "count": int(r.get("count") or 0),
            }
        )
    return out


async def archive_historical_dates(
    db,
    *,
    dates: Optional[List[str]] = None,
    dry_run: bool = True,
    prune_after: bool = False,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    """Safe initial migration: archive each historical date, optionally prune.

    prune_after still requires REAL S3 + ARCHIVE_PRUNE_ENABLED.
    """
    if dates is None:
        hist = await list_historical_product_dates(db)
        dates = [h["date_iso"] for h in hist]
    if limit is not None:
        dates = list(dates)[: int(limit)]

    plan = []
    for d in dates:
        existing = await am.find_verified(db, MODULE_PRODUCT_HISTORY, archive_date=date_key_to_iso(d))
        plan.append(
            {
                "date": date_key_to_iso(d),
                "already_verified": bool(existing),
                "manifest_id": (existing or {}).get("archive_id"),
                "record_count": (existing or {}).get("record_count"),
            }
        )

    if dry_run:
        return {
            "status": "dry_run",
            "dates": len(plan),
            "plan": plan,
            "prune_after": prune_after,
        }

    results = []
    for d in dates:
        date_iso = date_key_to_iso(d)
        archived = await archive_product_history_for_date(db, date_iso)
        prune_result = None
        if prune_after:
            prune_result = await prune_product_history_date(db, date_iso)
        results.append({"date": date_iso, "archive": archived, "prune": prune_result})
    return {"status": "ok", "dates": len(results), "results": results}
