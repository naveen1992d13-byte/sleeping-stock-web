"""Finalized Analytics daily rollup — 11:00 PM IST, before archive/prune.

Historical dates prefer that day's verified product-history archives (S3).
Live Mongo products are used only when no date-matched archive exists
(e.g. the business day being finalized at 11:00 PM before archive runs).

Screen APIs must read pre-aggregated daily metrics — never the 100k+ part rows.
"""
from __future__ import annotations

import gzip
import json
import logging
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger("nmts.analytics_rollup")
IST = ZoneInfo("Asia/Kolkata")

SNAPSHOT_COLLECTION = "analytics_stock_daily_snapshots"
STATUS_COLLECTION = "analytics_rollup_status"
DAILY_AGG_COLLECTION = "analytics_daily_aggs"

STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
STATUS_RUNNING = "running"

AGING_BUCKETS = [
    "0–90 Days",
    "91–180 Days",
    "181–270 Days",
    "271–361 Days",
    ">361 Days",
]

try:
    from . import part_category as pc
except ImportError:
    import part_category as pc


def _ist_now() -> datetime:
    return datetime.now(IST)


def yesterday_ist_iso(now: Optional[datetime] = None) -> str:
    now = now or _ist_now()
    return (now.date() - timedelta(days=1)).isoformat()


def today_ist_iso(now: Optional[datetime] = None) -> str:
    return (now or _ist_now()).date().isoformat()


def iso_to_date_key(iso: str) -> str:
    return str(iso or "").replace("-", "")[:8]


def date_key_to_iso(dk: str) -> str:
    text = str(dk or "").replace("-", "")[:8]
    if len(text) != 8:
        return str(dk or "")
    return f"{text[0:4]}-{text[4:6]}-{text[6:8]}"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def _analytics_bucket(days) -> str:
    try:
        if days is None or days == "":
            return AGING_BUCKETS[4]
        d = float(days)
    except (TypeError, ValueError):
        return AGING_BUCKETS[4]
    if d < 0:
        return AGING_BUCKETS[4]
    if d <= 90:
        return AGING_BUCKETS[0]
    if d <= 180:
        return AGING_BUCKETS[1]
    if d <= 270:
        return AGING_BUCKETS[2]
    if d <= 361:
        return AGING_BUCKETS[3]
    return AGING_BUCKETS[4]


def upload_no_date_key(upload_no: str) -> str:
    """Extract YYMMDD digits from upload numbers like PUHY260919004 / TS-PUHY261016001."""
    digits = re.sub(r"\D", "", str(upload_no or ""))
    if len(digits) < 6:
        return ""
    # brand-coded uploads usually end with YYMMDD + serial; take last 6 before serial if long
    if len(digits) >= 10:
        return digits[-10:-4]  # YYMMDD before 4-digit serial
    if len(digits) >= 6:
        return digits[:6]
    return ""


def upload_matches_date(upload_no: str, date_iso: str) -> bool:
    want = iso_to_date_key(date_iso)[2:]  # YYMMDD
    got = upload_no_date_key(upload_no)
    return bool(got) and got == want


async def ensure_rollup_indexes(db) -> None:
    await db[STATUS_COLLECTION].create_index("rollup_date_ist", unique=True, name="uq_analytics_rollup_date")
    await db[STATUS_COLLECTION].create_index([("status", 1), ("rollup_date_ist", -1)], name="idx_analytics_rollup_status")
    await db[SNAPSHOT_COLLECTION].create_index(
        [("snapshot_date_ist", 1), ("brand_id", 1), ("dealer_id", 1), ("branch_id", 1), ("part_number", 1)],
        unique=True,
        name="uq_analytics_daily_snapshot",
    )
    await db[DAILY_AGG_COLLECTION].create_index(
        [("rollup_date_ist", 1), ("brand_name", 1), ("dealer_name", 1), ("branch_name", 1), ("category", 1)],
        unique=True,
        name="uq_analytics_daily_agg",
    )
    await db[DAILY_AGG_COLLECTION].create_index([("rollup_date_ist", 1)], name="idx_analytics_daily_agg_date")


async def get_rollup_status(db, date_iso: str) -> Optional[dict]:
    iso = date_key_to_iso(iso_to_date_key(date_iso))
    return await db[STATUS_COLLECTION].find_one({"rollup_date_ist": iso}, {"_id": 0})


def _is_plausible_iso(date_iso: Optional[str]) -> bool:
    text = date_key_to_iso(iso_to_date_key(date_iso or ""))
    if len(iso_to_date_key(text)) != 8:
        return False
    if text < "2020-01-01":
        return False
    return True


async def last_successful_rollup_date(db, *, on_or_before: Optional[str] = None) -> Optional[str]:
    q: Dict[str, Any] = {"status": STATUS_SUCCESS, "rollup_date_ist": {"$gte": "2020-01-01"}}
    if on_or_before:
        q["rollup_date_ist"] = {
            "$gte": "2020-01-01",
            "$lte": date_key_to_iso(iso_to_date_key(on_or_before)),
        }
    row = await db[STATUS_COLLECTION].find_one(q, {"_id": 0, "rollup_date_ist": 1}, sort=[("rollup_date_ist", -1)])
    if row and _is_plausible_iso(row.get("rollup_date_ist")):
        return str(row["rollup_date_ist"])
    # Prefer daily aggs / snapshots only when status rows are missing.
    for coll, field in ((DAILY_AGG_COLLECTION, "rollup_date_ist"), (SNAPSHOT_COLLECTION, "snapshot_date_ist")):
        snap_q: Dict[str, Any] = {field: {"$gte": "2020-01-01"}}
        if on_or_before:
            snap_q[field] = {"$gte": "2020-01-01", "$lte": date_key_to_iso(iso_to_date_key(on_or_before))}
        legacy = await db[coll].find_one(snap_q, {"_id": 0, field: 1}, sort=[(field, -1)])
        if legacy and _is_plausible_iso(legacy.get(field)):
            return str(legacy[field])
    return None


def resolve_analytics_end_date(
    requested_to: str,
    *,
    last_success: Optional[str],
    now: Optional[datetime] = None,
) -> Tuple[str, Optional[str]]:
    yesterday = yesterday_ist_iso(now)
    req = date_key_to_iso(iso_to_date_key(requested_to))
    effective = min(req, yesterday)
    if last_success:
        effective = min(effective, date_key_to_iso(iso_to_date_key(last_success)))
        updated = date_key_to_iso(iso_to_date_key(last_success))
    else:
        updated = None
        effective = (datetime.strptime(req, "%Y-%m-%d").date() - timedelta(days=1)).isoformat()
    return effective, updated


async def _set_status(db, date_iso: str, payload: dict) -> dict:
    iso = date_key_to_iso(iso_to_date_key(date_iso))
    doc = {"rollup_date_ist": iso, "updated_at": _utc_now_iso(), **payload}
    await db[STATUS_COLLECTION].update_one({"rollup_date_ist": iso}, {"$set": doc}, upsert=True)
    return doc


def _row_qty_unit_value(r: dict) -> Tuple[float, float, float]:
    qty = _num(r.get("available_qty_number"), _num(r.get("quantity")))
    unit = _num(r.get("unit_value_number"), _num(r.get("mav_value"), _num(r.get("mav"))))
    value = _num(r.get("total_value_number"), _num(r.get("total_value"), qty * unit))
    return qty, unit, value


async def _load_rows_from_archives(db, date_iso: str) -> Tuple[List[dict], List[str]]:
    """Load that IST date's verified product-history archives (non-cancelled, date-matched)."""
    try:
        from s3_storage import get_storage
    except ImportError:
        from .s3_storage import get_storage  # type: ignore

    storage = get_storage()
    if not storage.is_s3():
        return [], []

    mans = await db.archive_manifests.find(
        {
            "module": "product-history",
            "archive_date": date_iso,
            "status": {"$in": ["VERIFIED", "PRUNED"]},
        },
        {"_id": 0},
    ).to_list(5000)
    chosen = []
    for m in mans:
        key = str(m.get("storage_key") or "")
        if not key or "/cancelled/" in key:
            continue
        # Prefer /current/ objects; allow flat keys that still encode the date.
        if "/current/" not in key and f"/{date_iso}/" not in key:
            continue
        fname = key.rsplit("/", 1)[-1]
        # Require upload date inside filename to match the archive date when present.
        if re.search(r"\d{6}", fname) and not upload_matches_date(fname, date_iso):
            # Still allow when filename has no upload-style token beyond the date path.
            if not any(tok.startswith("PU") or tok.startswith("TS-PU") for tok in fname.replace(".", "_").split("_")):
                pass
            else:
                continue
        chosen.append(m)

    if not chosen:
        return [], []

    rows: List[dict] = []
    used_keys: List[str] = []
    for m in chosen:
        key = m["storage_key"]
        try:
            data, _ctype = storage.download_bytes(key)
        except FileNotFoundError:
            logger.warning("analytics archive missing in storage date=%s key=%s", date_iso, key)
            continue
        except Exception as exc:
            logger.warning("analytics archive download failed date=%s key=%s err=%s", date_iso, key, exc)
            continue
        used_keys.append(key)
        try:
            text = gzip.decompress(data).decode()
        except Exception as exc:
            logger.warning("analytics archive decompress failed date=%s key=%s err=%s", date_iso, key, exc)
            continue
        for line in text.splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            # Drop rows whose upload_no belongs to another calendar day.
            uno = _text(r.get("upload_no"))
            if uno and not upload_matches_date(uno, date_iso):
                continue
            rows.append(r)
    return rows, used_keys


async def _load_latest_published_rows(db, date_iso: str) -> List[dict]:
    date_key = iso_to_date_key(date_iso)
    date_iso = date_key_to_iso(date_key)
    pipeline = [
        {"$match": {"publish_status": "Published", "active_date_key": {"$in": [date_key, date_iso]}}},
        {
            "$project": {
                "brand_id": 1, "brand_code": 1, "brand_name": 1,
                "dealer_id": 1, "dealer_code": 1, "dealer_name": 1,
                "branch_id": 1, "branch": 1, "part_number": 1, "part_name": 1, "item_name": 1,
                "part_category": 1, "category": 1,
                "quantity": 1, "available_qty_number": 1,
                "mav": 1, "mav_value": 1, "unit_value_number": 1,
                "total_value": 1, "total_value_number": 1,
                "purchase_aging_days": 1, "sales_aging_days": 1,
                "last_receipt_date": 1, "last_sales_date": 1,
                "upload_no": 1, "published_at": 1, "updated_at": 1,
            }
        },
        {"$sort": {"published_at": -1, "updated_at": -1}},
        {
            "$group": {
                "_id": {
                    "brand": {"$ifNull": ["$brand_name", ""]},
                    "dealer": {"$ifNull": ["$dealer_name", ""]},
                    "branch": {"$ifNull": ["$branch", ""]},
                    "part": {"$ifNull": ["$part_number", ""]},
                },
                "doc": {"$first": "$$ROOT"},
            }
        },
        {"$replaceRoot": {"newRoot": "$doc"}},
    ]
    try:
        return await db.products.aggregate(pipeline, allowDiskUse=True).to_list(500000)
    except Exception as exc:
        logger.warning("rollup aggregate failed for %s: %s", date_iso, exc)
        rows = await db.products.find(
            {"publish_status": "Published", "active_date_key": {"$in": [date_key, date_iso]}},
            {"_id": 0},
        ).sort([("published_at", -1), ("updated_at", -1)]).to_list(500000)
        latest: Dict[Tuple[str, str, str, str], dict] = {}
        for r in rows:
            pk = (
                _text(r.get("brand_name")).casefold(),
                _text(r.get("dealer_name")).casefold(),
                _text(r.get("branch")).casefold(),
                _text(r.get("part_number")).casefold(),
            )
            if pk not in latest:
                latest[pk] = r
        return list(latest.values())


async def load_source_rows_for_date(db, date_iso: str) -> Tuple[List[dict], str, List[str]]:
    """Return (rows, source_kind, archive_keys).

    source_kind is 'archive' when date-matched verified archives were used,
    otherwise 'live_products' for that active_date_key only.
    """
    iso = date_key_to_iso(iso_to_date_key(date_iso))
    arch_rows, keys = await _load_rows_from_archives(db, iso)
    if arch_rows:
        # Deduplicate archive rows newest-first when duplicates exist.
        arch_rows.sort(key=lambda r: str(r.get("published_at") or r.get("updated_at") or ""), reverse=True)
        latest: Dict[Tuple[str, str, str, str], dict] = {}
        for r in arch_rows:
            pk = (
                _text(r.get("brand_name")).casefold(),
                _text(r.get("dealer_name")).casefold(),
                _text(r.get("branch")).casefold(),
                _text(r.get("part_number")).casefold(),
            )
            if pk not in latest:
                latest[pk] = r
        return list(latest.values()), "archive", keys
    live = await _load_latest_published_rows(db, iso)
    return live, "live_products", []


def build_snapshot_docs(date_iso: str, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    date_iso = date_key_to_iso(iso_to_date_key(date_iso))
    now = _utc_now_iso()
    by_key: Dict[Tuple[str, str, str, str], Dict[str, Any]] = {}
    for r in rows:
        qty, unit, value = _row_qty_unit_value(r)
        brand_id = _text(r.get("brand_id") or r.get("brand_code"))
        dealer_id = _text(r.get("dealer_id") or r.get("dealer_code") or r.get("dealer_name"))
        branch_id = _text(r.get("branch_id") or r.get("branch"))
        part_number = _text(r.get("part_number"))
        key = (brand_id, dealer_id, branch_id, part_number.casefold())
        by_key[key] = {
            "snapshot_date_ist": date_iso,
            "brand_id": brand_id,
            "dealer_id": dealer_id,
            "branch_id": branch_id,
            "brand_name": r.get("brand_name"),
            "dealer_name": r.get("dealer_name"),
            "branch_name": r.get("branch"),
            "part_number": part_number,
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
            "updated_at": now,
            "finalized": True,
        }
    return list(by_key.values())


def build_daily_agg_docs(date_iso: str, snapshot_docs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Pre-aggregate per brand/dealer/branch/category for fast screen reads."""
    date_iso = date_key_to_iso(iso_to_date_key(date_iso))
    now = _utc_now_iso()
    groups: Dict[Tuple[str, str, str, str], dict] = {}
    for r in snapshot_docs:
        cat = pc.normalize_part_category(str(r.get("category") or r.get("part_category") or "")) or "Others"
        key = (
            _text(r.get("brand_name")),
            _text(r.get("dealer_name")),
            _text(r.get("branch_name")),
            cat,
        )
        g = groups.get(key)
        if not g:
            g = {
                "rollup_date_ist": date_iso,
                "brand_name": key[0],
                "dealer_name": key[1],
                "branch_name": key[2],
                "category": cat,
                "stock_qty": 0.0,
                "stock_value": 0.0,
                "line_count": 0,
                "aging_purchase": {b: {"qty": 0.0, "value": 0.0, "items": 0.0} for b in AGING_BUCKETS},
                "aging_sales": {b: {"qty": 0.0, "value": 0.0, "items": 0.0} for b in AGING_BUCKETS},
                "updated_at": now,
                "finalized": True,
            }
            groups[key] = g
        qty = _num(r.get("available_qty"))
        value = _num(r.get("total_value"))
        g["stock_qty"] += qty
        g["stock_value"] += value
        g["line_count"] += 1
        pb = _analytics_bucket(r.get("purchase_aging_days"))
        sb = _analytics_bucket(r.get("sales_aging_days"))
        g["aging_purchase"][pb]["qty"] += qty
        g["aging_purchase"][pb]["value"] += value
        g["aging_purchase"][pb]["items"] += 1.0
        g["aging_sales"][sb]["qty"] += qty
        g["aging_sales"][sb]["value"] += value
        g["aging_sales"][sb]["items"] += 1.0
    return list(groups.values())


def _totals(rows: List[Dict[str, Any]], *, qty_key: str, value_key: str) -> Tuple[float, float, int]:
    qty = sum(_num(r.get(qty_key)) for r in rows)
    value = sum(_num(r.get(value_key)) for r in rows)
    return qty, value, len(rows)


async def finalize_analytics_rollup_for_date(
    db,
    date_iso: str,
    *,
    force: bool = False,
) -> dict:
    """Capture one IST date into durable snapshots + pre-aggregated daily metrics."""
    iso = date_key_to_iso(iso_to_date_key(date_iso))
    if not _is_plausible_iso(iso):
        return {"status": STATUS_FAILED, "rollup_date_ist": date_iso, "error": "invalid date"}

    existing = await get_rollup_status(db, iso)
    if existing and existing.get("status") == STATUS_SUCCESS and not force:
        return {**existing, "status": STATUS_SUCCESS, "skipped": True, "reason": "already_finalized"}

    await _set_status(db, iso, {"status": STATUS_RUNNING, "error": None, "started_at": _utc_now_iso()})
    try:
        source_rows, source_kind, archive_keys = await load_source_rows_for_date(db, iso)
        docs = build_snapshot_docs(iso, source_rows)
        aggs = build_daily_agg_docs(iso, docs)
        snap_qty, snap_value, snap_n = _totals(docs, qty_key="available_qty", value_key="total_value")
        agg_qty = sum(_num(a.get("stock_qty")) for a in aggs)
        agg_value = sum(_num(a.get("stock_value")) for a in aggs)
        agg_lines = sum(int(a.get("line_count") or 0) for a in aggs)

        await db[SNAPSHOT_COLLECTION].delete_many({"snapshot_date_ist": iso})
        await db[DAILY_AGG_COLLECTION].delete_many({"rollup_date_ist": iso})
        if docs:
            for i in range(0, len(docs), 1000):
                await db[SNAPSHOT_COLLECTION].insert_many(docs[i : i + 1000], ordered=False)
        if aggs:
            await db[DAILY_AGG_COLLECTION].insert_many(aggs, ordered=False)

        stored = await db[SNAPSHOT_COLLECTION].find(
            {"snapshot_date_ist": iso}, {"_id": 0, "available_qty": 1, "total_value": 1}
        ).to_list(500000)
        store_qty, store_value, store_n = _totals(stored, qty_key="available_qty", value_key="total_value")
        stored_aggs = await db[DAILY_AGG_COLLECTION].find(
            {"rollup_date_ist": iso}, {"_id": 0, "stock_qty": 1, "stock_value": 1, "line_count": 1}
        ).to_list(50000)
        store_agg_qty = sum(_num(a.get("stock_qty")) for a in stored_aggs)
        store_agg_value = sum(_num(a.get("stock_value")) for a in stored_aggs)
        store_agg_lines = sum(int(a.get("line_count") or 0) for a in stored_aggs)

        reconciled = (
            abs(snap_qty - store_qty) <= max(0.01, abs(snap_qty) * 1e-9)
            and abs(snap_value - store_value) <= max(0.01, abs(snap_value) * 1e-9)
            and snap_n == store_n
            and abs(agg_qty - store_agg_qty) <= max(0.01, abs(agg_qty) * 1e-9)
            and abs(agg_value - store_agg_value) <= max(0.01, abs(agg_value) * 1e-9)
            and agg_lines == store_agg_lines == store_n
        )
        if not reconciled:
            raise RuntimeError(
                f"rollup reconciliation failed for {iso}: "
                f"snap(n={snap_n}, qty={snap_qty}, value={snap_value}) "
                f"stored(n={store_n}, qty={store_qty}, value={store_value}) "
                f"agg(n={agg_lines}, qty={agg_qty}, value={agg_value}) "
                f"stored_agg(n={store_agg_lines}, qty={store_agg_qty}, value={store_agg_value})"
            )

        result = await _set_status(
            db,
            iso,
            {
                "status": STATUS_SUCCESS,
                "error": None,
                "source_kind": source_kind,
                "archive_keys": archive_keys[:50],
                "archive_key_count": len(archive_keys),
                "source_record_count": snap_n,
                "snapshot_record_count": store_n,
                "daily_agg_record_count": len(stored_aggs),
                "source_total_qty": snap_qty,
                "source_total_value": snap_value,
                "snapshot_total_qty": store_qty,
                "snapshot_total_value": store_value,
                "daily_agg_total_qty": store_agg_qty,
                "daily_agg_total_value": store_agg_value,
                "reconciled": True,
                "finalized_at": _utc_now_iso(),
            },
        )
        logger.info(
            "Analytics rollup finalized date=%s source=%s rows=%s aggs=%s value=%.2f",
            iso,
            source_kind,
            store_n,
            len(stored_aggs),
            store_value,
        )
        return result
    except Exception as exc:
        logger.exception("Analytics rollup failed for %s", iso)
        return await _set_status(
            db,
            iso,
            {
                "status": STATUS_FAILED,
                "error": f"{type(exc).__name__}: {str(exc)[:500]}",
                "finalized_at": None,
                "reconciled": False,
            },
        )


async def backfill_analytics_rollups(db, dates: List[str], *, force: bool = False) -> dict:
    results = []
    for d in dates:
        results.append(await finalize_analytics_rollup_for_date(db, d, force=force))
    ok = sum(1 for r in results if r.get("status") == STATUS_SUCCESS)
    return {"requested": len(dates), "success": ok, "failed": len(dates) - ok, "results": results}


async def query_daily_aggs(
    db,
    date_keys: List[str],
    scope: Optional[dict] = None,
    category: Optional[str] = None,
) -> List[dict]:
    """Screen-load helper: tiny pre-aggregated rows only."""
    if not date_keys:
        return []
    isos = []
    for dk in date_keys:
        isos.append(date_key_to_iso(iso_to_date_key(dk)))
    q: Dict[str, Any] = {"rollup_date_ist": {"$in": sorted(set(isos))}}
    scope = scope or {}
    if scope.get("brand"):
        q["brand_name"] = scope["brand"]
    if scope.get("dealer"):
        q["dealer_name"] = scope["dealer"]
    if scope.get("branch"):
        q["branch_name"] = scope["branch"]
    if category and not pc.is_all_part_type(category):
        q["category"] = pc.normalize_part_category(str(category))
    return await db[DAILY_AGG_COLLECTION].find(q, {"_id": 0}).to_list(100000)
