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

    return await am.mark_status(
        db,
        manifest["archive_id"],
        am.STATUS_VERIFIED,
        eligible_for_prune=True,
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

    # Build analytics snapshot docs (also written into Mongo)
    snap_docs = await write_analytics_snapshots_for_date(db, date_iso, rows)
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
    return {"status": "verified", "manifest": manifest, "record_count": len(rows)}


async def write_analytics_snapshots_for_date(db, date_iso: str, rows: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """Populate analytics_stock_daily_snapshots for a date (idempotent upsert)."""
    date_key = iso_to_date_key(date_iso)
    date_iso = date_key_to_iso(date_key)
    if rows is None:
        rows = await db.products.find(
            {"publish_status": "Published", "active_date_key": {"$in": [date_key, date_iso]}},
            {"_id": 0},
        ).to_list(500000)

    docs: List[Dict[str, Any]] = []
    for r in rows:
        qty = float(r.get("available_qty_number", r.get("quantity", 0)) or 0)
        unit = float(r.get("unit_value_number", r.get("mav_value", r.get("mav", 0))) or 0)
        value = float(r.get("total_value_number", r.get("total_value", qty * unit)) or 0)
        doc = {
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
        docs.append(doc)
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


# -------------------- Prune (disabled by default) --------------------

async def prune_eligible_mongo_history(db, module: str = MODULE_PRODUCT_HISTORY) -> Dict[str, Any]:
    """Prune capability only — refused unless ARCHIVE_PRUNE_ENABLED=true."""
    from s3_storage import archive_prune_enabled

    if not archive_prune_enabled():
        return {
            "status": "skipped",
            "reason": "ARCHIVE_PRUNE_ENABLED=false",
            "deleted": 0,
        }
    # Even when enabled, only delete rows covered by VERIFIED manifests and outside hot window.
    # Intentionally conservative — this PR ships prune capability but default remains off.
    return {
        "status": "not_implemented_mass_delete",
        "reason": "Mass prune requires separate approval even when flag is true",
        "deleted": 0,
    }
