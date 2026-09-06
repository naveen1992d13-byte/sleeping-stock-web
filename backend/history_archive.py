"""Daily / monthly archive builders for Product History, Orders, Requests, Verifications."""

from __future__ import annotations

import asyncio
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
MODULE_UPLOADS = "uploads"


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


def _text(value: Any) -> str:
    return str(value or "").strip()


async def _enrich_product_scope_rows(db, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Fill brand/dealer/branch names on archive payload when source rows omit them.

    Additive mapping only — does not invent values. Uses:
    - brand/dealer/branch fields already on the row
    - brands master (code → name) when brand_code is a real code (not XX)
    - dealers master (id/code/name) when dealer_code is present
    - parent upload document (upload_id) when present
    - batch_summaries for the same upload_id as a last resort
    Codes are always preserved. Empty/placeholder codes (XX) are kept as-is.
    """
    if not rows:
        return rows

    brand_docs = await db.brands.find({}, {"_id": 0, "code": 1, "name": 1}).to_list(5000)
    brand_by_code = {
        _text(b.get("code")).upper(): _text(b.get("name"))
        for b in brand_docs
        if _text(b.get("code")) and _text(b.get("name"))
    }

    upload_ids = {_text(r.get("upload_id")) for r in rows if _text(r.get("upload_id"))}
    upload_map: Dict[str, Dict[str, Any]] = {}
    if upload_ids:
        uploads = await db.uploads.find(
            {"id": {"$in": list(upload_ids)}},
            {
                "_id": 0,
                "id": 1,
                "brand": 1,
                "brand_name": 1,
                "dealer_name": 1,
                "branch": 1,
                "branch_name": 1,
                "brand_code": 1,
                "dealer_code": 1,
            },
        ).to_list(len(upload_ids) + 10)
        upload_map = {_text(u.get("id")): u for u in uploads if _text(u.get("id"))}

    dealer_codes = {
        _text(r.get("dealer_code"))
        for r in rows
        if _text(r.get("dealer_code"))
    } | {
        _text(u.get("dealer_code"))
        for u in upload_map.values()
        if _text(u.get("dealer_code"))
    }
    dealer_by_key: Dict[str, str] = {}
    if dealer_codes:
        dealer_docs = await db.dealers.find(
            {
                "$or": [
                    {"id": {"$in": list(dealer_codes)}},
                    {"code": {"$in": list(dealer_codes)}},
                    {"dealer_code": {"$in": list(dealer_codes)}},
                    {"name": {"$in": list(dealer_codes)}},
                    {"dealer_name": {"$in": list(dealer_codes)}},
                ]
            },
            {"_id": 0, "id": 1, "code": 1, "dealer_code": 1, "name": 1, "dealer_name": 1},
        ).to_list(len(dealer_codes) + 50)
        for d in dealer_docs:
            name = _text(d.get("dealer_name") or d.get("name"))
            if not name:
                continue
            for key in (d.get("id"), d.get("code"), d.get("dealer_code"), d.get("name"), d.get("dealer_name")):
                k = _text(key)
                if k:
                    dealer_by_key[k] = name
                    dealer_by_key[k.upper()] = name

    summary_map: Dict[str, Dict[str, Any]] = {}
    if upload_ids:
        summaries = await db.batch_summaries.find(
            {"upload_id": {"$in": list(upload_ids)}},
            {"_id": 0, "upload_id": 1, "brand_name": 1, "dealer_name": 1, "branch": 1, "brand_code": 1},
        ).to_list(len(upload_ids) + 50)
        for s in summaries:
            uid = _text(s.get("upload_id"))
            if uid and uid not in summary_map:
                summary_map[uid] = s

    enriched: List[Dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        brand_name = _text(row.get("brand_name") or row.get("brand"))
        dealer_name = _text(row.get("dealer_name") or row.get("dealer"))
        branch = _text(row.get("branch") or row.get("branch_name"))
        brand_code = _text(row.get("brand_code"))
        dealer_code = _text(row.get("dealer_code"))

        upload = upload_map.get(_text(row.get("upload_id"))) or {}
        summary = summary_map.get(_text(row.get("upload_id"))) or {}
        if not brand_name:
            brand_name = _text(upload.get("brand_name") or upload.get("brand")) or _text(summary.get("brand_name"))
        if not dealer_name:
            dealer_name = _text(upload.get("dealer_name")) or _text(summary.get("dealer_name"))
        if not branch:
            branch = (
                _text(upload.get("branch") or upload.get("branch_name"))
                or _text(summary.get("branch"))
            )
        if not brand_code:
            brand_code = _text(upload.get("brand_code")) or _text(summary.get("brand_code"))
        if not dealer_code:
            dealer_code = _text(upload.get("dealer_code"))

        if not brand_name and brand_code and brand_code.upper() != "XX":
            brand_name = brand_by_code.get(brand_code.upper()) or ""
        if not dealer_name and dealer_code:
            dealer_name = dealer_by_key.get(dealer_code) or dealer_by_key.get(dealer_code.upper()) or ""

        if brand_name:
            row["brand_name"] = brand_name
            row["brand"] = row.get("brand") or brand_name
        if dealer_name:
            row["dealer_name"] = dealer_name
        if branch:
            row["branch"] = branch
        if brand_code:
            row["brand_code"] = brand_code
        if dealer_code:
            row["dealer_code"] = dealer_code
        enriched.append(row)
    return enriched


def _source_fingerprint(rows: Iterable[Dict[str, Any]]) -> str:
    """Deterministic fingerprint of archived product scope rows for change detection."""
    lines: List[str] = []
    for r in rows:
        qty = float(r.get("available_qty_number", r.get("quantity", 0)) or 0)
        lines.append(
            "|".join(
                [
                    _text(r.get("part_number")).upper(),
                    _text(r.get("brand_name") or r.get("brand")),
                    _text(r.get("dealer_name") or r.get("dealer")),
                    _text(r.get("branch") or r.get("branch_name")),
                    f"{qty:.6f}",
                ]
            )
        )
    lines.sort()
    return sha256_bytes("\n".join(lines).encode("utf-8"))


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
    extra_fields: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    storage = get_storage()
    digest = sha256_bytes(data)
    if existing and str(existing.get("status") or "") in {am.STATUS_VERIFIED, am.STATUS_PRUNED}:
        # Never clobber a trusted archive (same or different bytes).
        return existing
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
            "scope_brands": sorted(str(x) for x in brands if x)[:200],
            "scope_dealers": sorted(str(x) for x in dealers if x)[:200],
            "scope_branches": sorted(str(x) for x in branches if x)[:200],
            "error": None,
            "eligible_for_prune": False,
        }
    )
    if extra_fields:
        manifest.update(extra_fields)
    manifest = await am.upsert_manifest(db, manifest)

    import archive_verify as av
    from s3_storage import ImmutableObjectError

    # Idempotent retry: if the expected object already exists and passes physical
    # verification, reconcile the manifest instead of blindly re-uploading.
    allow_replace = bool(
        existing
        and str(existing.get("status") or "") in {am.STATUS_FAILED, am.STATUS_CREATING, am.STATUS_UPLOADED}
    )
    if existing and str(existing.get("status") or "") in {am.STATUS_VERIFIED, am.STATUS_PRUNED}:
        if existing.get("sha256") == digest and existing.get("storage_key") == storage_key:
            return existing
        allow_replace = False

    if storage.is_s3() and storage.exists(storage_key):
        pre = av.physical_s3_verify(
            storage_key=storage_key,
            expected_sha256=digest,
            expected_size=len(data),
            expected_record_count=record_count,
            require_jsonl_count=storage_key.endswith(".jsonl.gz")
            or (manifest.get("format") == "jsonl.gz"),
        )
        if pre.get("ok"):
            return await am.mark_status(
                db,
                manifest["archive_id"],
                am.STATUS_VERIFIED,
                eligible_for_prune=True,
                storage_backend="s3",
                error=None,
                reconciled=True,
            )
        if not allow_replace:
            await am.mark_status(
                db,
                manifest["archive_id"],
                existing.get("status") if existing and existing.get("status") in {am.STATUS_VERIFIED, am.STATUS_PRUNED} else am.STATUS_FAILED,
                error="Refusing overwrite of existing archive object with different bytes",
                eligible_for_prune=bool(existing and existing.get("status") == am.STATUS_VERIFIED),
            )
            if existing and existing.get("status") in {am.STATUS_VERIFIED, am.STATUS_PRUNED}:
                return existing
            raise RuntimeError("Refusing overwrite of existing archive object with different bytes")

    try:
        if not storage.is_s3():
            # Still write local for diagnostics, but never claim REAL S3 success.
            try:
                storage.upload_bytes(storage_key, data, content_type="application/gzip", allow_replace=allow_replace)
            except Exception:
                pass
            await am.mark_status(
                db,
                manifest["archive_id"],
                am.STATUS_FAILED,
                error="S3 credentials/config unavailable — local fallback is not REAL S3",
                eligible_for_prune=False,
                storage_backend=storage.mode,
            )
            raise RuntimeError("REAL S3 unavailable — archive not TRANSFERRED & VERIFIED")

        stored = storage.upload_bytes(
            storage_key, data, content_type="application/gzip", allow_replace=allow_replace
        )
        await am.mark_status(db, manifest["archive_id"], am.STATUS_UPLOADED)
        provider = str(getattr(stored, "storage_provider", None) or storage.mode or "").lower()
        if provider == "local":
            await am.mark_status(
                db,
                manifest["archive_id"],
                am.STATUS_FAILED,
                error="Upload resolved to local fallback, not REAL S3",
                eligible_for_prune=False,
                storage_backend="local",
            )
            raise RuntimeError("Upload failed — local fallback masquerading as S3 success blocked")
    except ImmutableObjectError as exc:
        await am.mark_status(
            db,
            manifest["archive_id"],
            existing.get("status") if existing and existing.get("status") in {am.STATUS_VERIFIED, am.STATUS_PRUNED} else am.STATUS_FAILED,
            error=str(exc)[:1000],
            eligible_for_prune=bool(existing and existing.get("status") == am.STATUS_VERIFIED),
        )
        if existing and existing.get("status") in {am.STATUS_VERIFIED, am.STATUS_PRUNED}:
            return existing
        raise
    except RuntimeError:
        raise
    except Exception as exc:
        await am.mark_status(
            db,
            manifest["archive_id"],
            am.STATUS_FAILED,
            error=f"upload failed: {exc}"[:1000],
            eligible_for_prune=False,
        )
        raise

    # Physical REAL S3 verification (exists, size, sha256, readable count)
    live = av.physical_s3_verify(
        storage_key=storage_key,
        expected_sha256=digest,
        expected_size=len(data),
        expected_record_count=record_count,
        require_jsonl_count=storage_key.endswith(".jsonl.gz") or (manifest.get("format") == "jsonl.gz"),
    )
    if not live.get("ok"):
        await am.mark_status(
            db,
            manifest["archive_id"],
            am.STATUS_FAILED,
            error=str(live.get("reason") or "S3 integrity verification failed")[:1000],
            eligible_for_prune=False,
            storage_backend=live.get("storage_backend") or storage.mode,
        )
        raise RuntimeError(live.get("reason") or "Archive integrity verification failed")

    # Prune eligibility requires REAL S3 — never authorize prune without it.
    return await am.mark_status(
        db,
        manifest["archive_id"],
        am.STATUS_VERIFIED,
        eligible_for_prune=True,
        storage_backend="s3",
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
    # Enrich empty brand/dealer/branch from codes / upload metadata before packaging.
    rows = await _enrich_product_scope_rows(db, rows)

    # Also include batch summaries + a compact analytics snapshot payload for the day
    summaries = await db.batch_summaries.find(
        {"active_date_key": {"$in": [date_key, date_iso]}},
        {"_id": 0},
    ).to_list(50000)

    brands, dealers, branches = set(), set(), set()
    brand_codes, dealer_codes = set(), set()
    for r in rows:
        if r.get("brand_name") or r.get("brand"):
            brands.add(str(r.get("brand_name") or r.get("brand")))
        if r.get("dealer_name") or r.get("dealer"):
            dealers.add(str(r.get("dealer_name") or r.get("dealer")))
        if r.get("branch") or r.get("branch_name"):
            branches.add(str(r.get("branch") or r.get("branch_name")))
        if r.get("brand_code"):
            brand_codes.add(str(r.get("brand_code")))
        if r.get("dealer_code"):
            dealer_codes.add(str(r.get("dealer_code")))

    storage = get_storage()
    products_key = storage.key("product-history", date_iso, "products.jsonl.gz")
    summary_key = storage.key("product-history", date_iso, "batch-summaries.json")
    snap_key = storage.key("product-history", date_iso, "analytics-snapshots.json")

    products_bytes = _gzip_jsonl(rows)
    summary_bytes = json.dumps(summaries, ensure_ascii=False, default=str).encode("utf-8")
    source_fp = _source_fingerprint(rows)

    # Build analytics snapshot payload in-memory first. Upload/verify the
    # authoritative products archive BEFORE writing snapshots back to Mongo so
    # archival still succeeds when Atlas is over quota / write-blocked.
    snap_docs = _build_analytics_snapshot_docs(date_iso, rows)
    snap_bytes = json.dumps(snap_docs, ensure_ascii=False, default=str).encode("utf-8")

    # Upload companion objects (best-effort; main products archive is authoritative)
    try:
        storage.upload_bytes(summary_key, summary_bytes, content_type="application/json", allow_replace=True)
        storage.upload_bytes(snap_key, snap_bytes, content_type="application/json", allow_replace=True)
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
        existing=failed_or_partial,
        extra_fields={
            "scope_brand_codes": sorted(brand_codes)[:200],
            "scope_dealer_codes": sorted(dealer_codes)[:200],
            "source_fingerprint": source_fp,
            "source_fingerprint_algo": "part|brand|dealer|branch|qty_v1",
        },
    )

    # Do NOT block the HTTP response on per-row analytics Mongo upserts.
    # Integrity of the S3 archive is already verified above.
    async def _bg_analytics():
        try:
            await write_analytics_snapshots_for_date(db, date_iso, rows)
        except Exception as exc:
            logger.warning("Analytics snapshot Mongo write skipped for %s: %s", date_iso, exc)

    try:
        asyncio.create_task(_bg_analytics())
    except Exception as exc:
        logger.warning("Could not schedule analytics snapshot task for %s: %s", date_iso, exc)

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

# Order Desk item-level terminal states (header often stays non-terminal while items finish).
TERMINAL_ORDER_ITEM_STATUSES = {
    "Completed",
    "Cancelled",
    "Cancelled – No Response",
    "No Further Stock Available",
    "Rejected",
}
TERMINAL_ORDER_STATUSES = set(TERMINAL_ORDER_ITEM_STATUSES) | {
    "Completed",
    "Cancelled",
    "Cancelled – No Response",
    "No Further Stock Available",
}
TERMINAL_REQUEST_STATUSES = {"Completed", "Rejected", "Cancelled"}

# Open / in-flight statuses must NEVER be archived
OPEN_REQUEST_STATUSES = {
    "Requested",
    "Approved",
    "Partially Approved",
    "Dispatched",
    "Received",
}
OPEN_ORDER_ITEM_STATUSES = {
    "Order Created",
    "Availability Checked",
    "Source Selected",
    "Requested",
    "Pending Retry",
    "Cancellation Requested",
    "Approved",
    "Partially Approved",
    "Dispatched",
    "Received",
    "Accepted",
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


def _order_terminal_at(header: dict, items: List[dict]) -> Optional[datetime]:
    """Real terminal/completion timestamp for an Order Desk order (IST-aware caller)."""
    candidates: List[datetime] = []
    for row in [header, *items]:
        for key in ("completed_at", "cancelled_at", "rejected_at", "factory_confirmed_at", "updated_at"):
            dt = _parse_dt(row.get(key))
            if dt:
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                candidates.append(dt)
    return max(candidates) if candidates else None


def _is_terminal_order_desk(header: dict, items: List[dict]) -> bool:
    """True only when the order is fully terminal — never partial / in-progress.

    Follows Order Desk workflow: items drive completion. An order archives only when
    every item is in a terminal status (Cancelled / Completed / No Further Stock /
    Rejected / Cancelled – No Response). Empty-item headers are terminal only when
    the header itself is terminal.
    """
    if not items:
        return str(header.get("status") or "") in TERMINAL_ORDER_STATUSES
    statuses = [str(i.get("status") or "") for i in items]
    if any(s in OPEN_ORDER_ITEM_STATUSES or s not in TERMINAL_ORDER_ITEM_STATUSES for s in statuses):
        return False
    return all(s in TERMINAL_ORDER_ITEM_STATUSES for s in statuses)


def _order_archive_row(header: dict, items: List[dict], terminal_at: datetime) -> Dict[str, Any]:
    """Flatten header + items while preserving Order No / Reference / Brand / Dealer / Branch."""
    order_no = (
        header.get("order_number")
        or header.get("order_no")
        or header.get("number")
        or header.get("id")
    )
    return {
        **_jsonable(header),
        "order_number": order_no,
        "order_no": order_no,
        "reference_no": header.get("reference_no") or header.get("reference_number") or "",
        "brand_name": header.get("brand_name") or header.get("brand") or "",
        "brand_code": header.get("brand_code") or "",
        "dealer_name": header.get("dealer_name") or header.get("dealer") or "",
        "branch": header.get("branch") or header.get("branch_name") or "",
        "status": header.get("status"),
        "terminal_status": "terminal",
        "terminal_at": terminal_at.isoformat(),
        "completed_at": terminal_at.isoformat(),
        "items": [_jsonable(i) for i in items],
        "item_count": len(items),
        "item_statuses": [str(i.get("status") or "") for i in items],
        "source_collection": "order_headers+order_items",
    }


async def _probe_order_desk_source(db) -> Dict[str, Any]:
    """Confirm live Order Desk collections exist (never treat legacy db.orders as source)."""
    try:
        # estimated_document_count / count — presence of collection API
        header_count = await db.order_headers.count_documents({})
        item_count = await db.order_items.count_documents({})
        return {
            "ok": True,
            "order_headers": int(header_count),
            "order_items": int(item_count),
            "legacy_orders": int(await db.orders.count_documents({})),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:500]}


async def _collect_terminal_order_desk_rows(
    db,
    *,
    date_iso: Optional[str] = None,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> Tuple[List[Dict[str, Any]], set, set, set]:
    """Collect fully-terminal Order Desk orders for a day or month window."""
    brands, dealers, branches = set(), set(), set()
    rows: List[Dict[str, Any]] = []
    headers = await db.order_headers.find({}, {"_id": 0}).to_list(200000)
    if not headers:
        return rows, brands, dealers, branches

    order_ids = [h.get("id") for h in headers if h.get("id")]
    items_by_order: Dict[str, List[dict]] = {oid: [] for oid in order_ids}
    if order_ids:
        all_items = await db.order_items.find(
            {"order_id": {"$in": order_ids}},
            {"_id": 0},
        ).to_list(500000)
        for it in all_items:
            oid = it.get("order_id")
            if oid in items_by_order:
                items_by_order[oid].append(it)
            else:
                items_by_order[oid] = [it]

    for header in headers:
        oid = header.get("id")
        items = items_by_order.get(oid, []) if oid else []
        if not _is_terminal_order_desk(header, items):
            continue
        terminal_at = _order_terminal_at(header, items)
        if not terminal_at:
            continue
        terminal_ist = terminal_at.astimezone(IST)
        if date_iso:
            if terminal_ist.date().isoformat() != date_iso:
                continue
        elif start is not None and end is not None:
            if not (start <= terminal_ist < end):
                continue
        else:
            continue
        row = _order_archive_row(header, items, terminal_at)
        rows.append(row)
        if row.get("brand_name"):
            brands.add(str(row["brand_name"]))
        if row.get("dealer_name"):
            dealers.add(str(row["dealer_name"]))
        if row.get("branch"):
            branches.add(str(row["branch"]))
    return rows, brands, dealers, branches


async def _mark_no_eligible(
    db,
    *,
    module: str,
    archive_date: Optional[str],
    archive_month: Optional[str],
    source_collection: str,
    message: str,
    existing: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Record genuine zero-eligible result — never a green VERIFIED empty dump."""
    manifest = existing or am.base_manifest(
        module=module,
        archive_date=archive_date,
        archive_month=archive_month,
        storage_key="",
        format="none",
        source_collection=source_collection,
    )
    manifest.update(
        {
            "storage_key": "",
            "record_count": 0,
            "file_size": 0,
            "sha256": "",
            "source_collection": source_collection,
            "min_date": archive_date,
            "max_date": archive_date,
            "error": message,
            "eligible_for_prune": False,
            "storage_backend": None,
        }
    )
    manifest = await am.upsert_manifest(db, manifest)
    manifest = await am.mark_status(
        db,
        manifest["archive_id"],
        am.STATUS_NO_ELIGIBLE,
        error=message,
        eligible_for_prune=False,
        storage_backend=None,
        record_count=0,
        file_size=0,
        sha256="",
        storage_key="",
    )
    return manifest


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

    probe = await _probe_order_desk_source(db)
    if not probe.get("ok"):
        raise RuntimeError(f"Order Desk source error: {probe.get('error') or 'order_headers unavailable'}")

    rows, brands, dealers, branches = await _collect_terminal_order_desk_rows(
        db, start=start, end=end
    )
    existing_any = await am.find_any(db, MODULE_ORDERS, archive_month=archive_month)
    if not rows:
        manifest = await _mark_no_eligible(
            db,
            module=MODULE_ORDERS,
            archive_date=None,
            archive_month=archive_month,
            source_collection="order_headers+order_items",
            message="NO ELIGIBLE ORDERS",
            existing=existing_any,
        )
        return {
            "status": "no_eligible",
            "manifest": manifest,
            "record_count": 0,
            "display_status": "NO ELIGIBLE ORDERS",
            "source": probe,
        }

    storage = get_storage()
    key = storage.key("orders", archive_month, "completed-orders.jsonl.gz")
    data = _gzip_jsonl(rows)

    manifest = await _upload_verified(
        db,
        module=MODULE_ORDERS,
        archive_date=None,
        archive_month=archive_month,
        storage_key=key,
        data=data,
        source_collection="order_headers+order_items",
        record_count=len(rows),
        min_date=start.date().isoformat(),
        max_date=(end - timedelta(days=1)).date().isoformat(),
        brands=brands,
        dealers=dealers,
        branches=branches,
        existing=existing_any,
    )

    for r in rows:
        number = r.get("order_number") or r.get("order_no") or r.get("id")
        idx = {
            "number": number,
            "reference_no": r.get("reference_no"),
            "brand": r.get("brand_name"),
            "dealer": r.get("dealer_name"),
            "branch": r.get("branch"),
            "created_date": r.get("created_at"),
            "completed_date": r.get("terminal_at") or r.get("completed_at") or r.get("updated_at"),
            "status": r.get("status"),
            "total_items": r.get("item_count") or r.get("total_items"),
            "total_qty": r.get("total_required_qty") or r.get("total_qty"),
            "total_value": r.get("total_order_value") or r.get("total_value"),
            "archive_month": archive_month,
            "storage_key": key,
        }
        if number:
            await db.order_archive_index.update_one(
                {"number": number, "archive_month": archive_month},
                {"$set": idx},
                upsert=True,
            )

    return {"status": "verified", "manifest": manifest, "record_count": len(rows), "source": probe}


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


def _terminal_on_date(row: dict, date_iso: str) -> bool:
    """True when the row reached a terminal state on the given IST calendar date."""
    completed = (
        _parse_dt(row.get("completed_at"))
        or _parse_dt(row.get("cancelled_at"))
        or _parse_dt(row.get("rejected_at"))
        or _parse_dt(row.get("updated_at"))
        or _parse_dt(row.get("created_at"))
    )
    if not completed:
        return False
    if completed.tzinfo is None:
        completed = completed.replace(tzinfo=timezone.utc)
    return completed.astimezone(IST).date().isoformat() == date_iso


async def archive_orders_for_date(db, archive_date: str, force: bool = False) -> Dict[str, Any]:
    """Daily Orders dump from live Order Desk (order_headers + order_items).

    - Active / partial / in-progress orders stay in MongoDB
    - Fully terminal orders archive on their real terminal/completion IST date
    - Zero eligible → NO ELIGIBLE ORDERS (not green VERIFIED)
    - Never uses legacy db.orders as the archive source
    """
    date_iso = date_key_to_iso(archive_date)
    existing = await am.find_verified(db, MODULE_ORDERS, archive_date=date_iso)
    if existing and not force:
        return {"status": "already_verified", "manifest": existing}
    existing_any = await am.find_any(db, MODULE_ORDERS, archive_date=date_iso)
    if (
        existing_any
        and existing_any.get("status") == am.STATUS_NO_ELIGIBLE
        and not force
    ):
        return {
            "status": "no_eligible",
            "manifest": existing_any,
            "record_count": 0,
            "display_status": "NO ELIGIBLE ORDERS",
        }

    probe = await _probe_order_desk_source(db)
    if not probe.get("ok"):
        # Source mapping / collection failure — distinct from genuine zero-eligible
        failed = existing_any or am.base_manifest(
            module=MODULE_ORDERS,
            archive_date=date_iso,
            source_collection="order_headers+order_items",
        )
        failed = await am.upsert_manifest(db, failed)
        failed = await am.mark_status(
            db,
            failed["archive_id"],
            am.STATUS_FAILED,
            error=f"Order Desk source error: {probe.get('error') or 'order_headers unavailable'}"[:1000],
            eligible_for_prune=False,
            source_collection="order_headers+order_items",
        )
        return {
            "status": "error",
            "error": failed.get("error"),
            "manifest": failed,
            "record_count": 0,
            "display_status": "FAILED",
        }

    rows, brands, dealers, branches = await _collect_terminal_order_desk_rows(
        db, date_iso=date_iso
    )

    if not rows:
        manifest = await _mark_no_eligible(
            db,
            module=MODULE_ORDERS,
            archive_date=date_iso,
            archive_month=None,
            source_collection="order_headers+order_items",
            message="NO ELIGIBLE ORDERS",
            existing=existing_any,
        )
        return {
            "status": "no_eligible",
            "manifest": manifest,
            "record_count": 0,
            "display_status": "NO ELIGIBLE ORDERS",
            "source": probe,
            # Prove legacy emptiness cannot drive success
            "legacy_orders_count": probe.get("legacy_orders"),
            "order_headers_count": probe.get("order_headers"),
        }

    storage = get_storage()
    key = storage.key("orders", date_iso, "completed-orders.jsonl.gz")
    data = _gzip_jsonl(rows)
    manifest = await _upload_verified(
        db,
        module=MODULE_ORDERS,
        archive_date=date_iso,
        archive_month=None,
        storage_key=key,
        data=data,
        source_collection="order_headers+order_items",
        record_count=len(rows),
        min_date=date_iso,
        max_date=date_iso,
        brands=brands,
        dealers=dealers,
        branches=branches,
        existing=existing_any,
    )
    return {
        "status": "verified",
        "manifest": manifest,
        "record_count": len(rows),
        "source": probe,
        "source_collection": "order_headers+order_items",
    }

async def archive_requests_for_date(db, archive_date: str, force: bool = False) -> Dict[str, Any]:
    """Daily Requests dump — terminal on archive_date only; active requests stay in Mongo."""
    date_iso = date_key_to_iso(archive_date)
    existing = await am.find_verified(db, MODULE_REQUESTS, archive_date=date_iso)
    if existing and not force:
        return {"status": "already_verified", "manifest": existing}

    brands, dealers, branches = set(), set(), set()
    rows = []
    cursor = db.order_requests.find({"status": {"$in": list(TERMINAL_REQUEST_STATUSES)}}, {"_id": 0})
    for r in await cursor.to_list(200000):
        if r.get("status") in OPEN_REQUEST_STATUSES:
            continue
        if not _terminal_on_date(r, date_iso):
            continue
        rows.append(r)
        if r.get("requesting_brand") or r.get("supplying_brand") or r.get("brand"):
            brands.add(str(r.get("requesting_brand") or r.get("supplying_brand") or r.get("brand")))
        if r.get("requesting_dealer") or r.get("supplying_dealer"):
            dealers.add(str(r.get("requesting_dealer") or r.get("supplying_dealer")))
        if r.get("requesting_branch") or r.get("supplying_branch"):
            branches.add(str(r.get("requesting_branch") or r.get("supplying_branch")))

    storage = get_storage()
    key = storage.key("requests", date_iso, "completed-requests.jsonl.gz")
    data = _gzip_jsonl(rows)
    existing_any = await am.find_any(db, MODULE_REQUESTS, archive_date=date_iso)
    manifest = await _upload_verified(
        db,
        module=MODULE_REQUESTS,
        archive_date=date_iso,
        archive_month=None,
        storage_key=key,
        data=data,
        source_collection="order_requests",
        record_count=len(rows),
        min_date=date_iso,
        max_date=date_iso,
        brands=brands,
        dealers=dealers,
        branches=branches,
        existing=existing_any,
    )
    return {"status": "verified", "manifest": manifest, "record_count": len(rows)}


async def archive_uploads_for_date(db, archive_date: str, force: bool = False) -> Dict[str, Any]:
    """Consolidated daily Uploaded Data Dump across all brands/dealers/branches."""
    date_iso = date_key_to_iso(archive_date)
    date_key = iso_to_date_key(date_iso)
    existing = await am.find_verified(db, MODULE_UPLOADS, archive_date=date_iso)
    if existing and not force:
        return {"status": "already_verified", "manifest": existing}

    brands, dealers, branches = set(), set(), set()
    rows = []
    # Prefer upload_items for the day; fall back to uploads metadata
    try:
        cursor = db.upload_items.find(
            {"$or": [{"date_key": {"$in": [date_key, date_iso]}}, {"upload_date": date_iso}, {"active_date_key": {"$in": [date_key, date_iso]}}]},
            {"_id": 0},
        )
        rows = await cursor.to_list(500000)
    except Exception:
        rows = []
    if not rows:
        try:
            cursor = db.uploads.find(
                {"$or": [{"date_key": {"$in": [date_key, date_iso]}}, {"upload_date": date_iso}]},
                {"_id": 0},
            )
            rows = await cursor.to_list(100000)
        except Exception:
            rows = []

    for r in rows:
        if r.get("brand_name") or r.get("brand"):
            brands.add(str(r.get("brand_name") or r.get("brand")))
        if r.get("dealer_name") or r.get("dealer"):
            dealers.add(str(r.get("dealer_name") or r.get("dealer")))
        if r.get("branch") or r.get("branch_name"):
            branches.add(str(r.get("branch") or r.get("branch_name")))

    storage = get_storage()
    key = storage.key("uploads", date_iso, "uploaded-data.jsonl.gz")
    data = _gzip_jsonl(rows)
    existing_any = await am.find_any(db, MODULE_UPLOADS, archive_date=date_iso)
    manifest = await _upload_verified(
        db,
        module=MODULE_UPLOADS,
        archive_date=date_iso,
        archive_month=None,
        storage_key=key,
        data=data,
        source_collection="upload_items",
        record_count=len(rows),
        min_date=date_iso,
        max_date=date_iso,
        brands=brands,
        dealers=dealers,
        branches=branches,
        existing=existing_any,
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
        extra_fields={
            "reader_ready": False,
            "prune_blocked_reason": "no_history_reader",
            "retention_policy": "mongo_hot_days",
        },
    )
    # Do not claim cleanup-ready: there is no verification-history S3 reader.
    if manifest and manifest.get("status") == am.STATUS_VERIFIED:
        manifest = await am.mark_status(
            db,
            manifest["archive_id"],
            am.STATUS_VERIFIED,
            eligible_for_prune=False,
            reader_ready=False,
            prune_blocked_reason="no_history_reader",
            retention_policy="mongo_hot_days",
        )
    return {
        "status": "verified",
        "manifest": manifest,
        "record_count": len(rows),
        "prune_blocked": True,
        "reader_ready": False,
    }


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
