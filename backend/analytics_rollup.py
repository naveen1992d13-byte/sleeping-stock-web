"""Finalized Analytics daily rollup — 11:00 PM IST, before archive/prune.

Screen reads must use only successfully finalized rollup rows. Live product
aggregation is reserved for the nightly finalizer, never for page load.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger("nmts.analytics_rollup")
IST = ZoneInfo("Asia/Kolkata")

SNAPSHOT_COLLECTION = "analytics_stock_daily_snapshots"
STATUS_COLLECTION = "analytics_rollup_status"

STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
STATUS_RUNNING = "running"


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


async def ensure_rollup_indexes(db) -> None:
    await db[STATUS_COLLECTION].create_index("rollup_date_ist", unique=True, name="uq_analytics_rollup_date")
    await db[STATUS_COLLECTION].create_index([("status", 1), ("rollup_date_ist", -1)], name="idx_analytics_rollup_status")
    await db[SNAPSHOT_COLLECTION].create_index(
        [("snapshot_date_ist", 1), ("brand_id", 1), ("dealer_id", 1), ("branch_id", 1), ("part_number", 1)],
        unique=True,
        name="uq_analytics_daily_snapshot",
    )


async def get_rollup_status(db, date_iso: str) -> Optional[dict]:
    iso = date_key_to_iso(iso_to_date_key(date_iso))
    return await db[STATUS_COLLECTION].find_one({"rollup_date_ist": iso}, {"_id": 0})


def _is_plausible_iso(date_iso: Optional[str]) -> bool:
    text = date_key_to_iso(iso_to_date_key(date_iso or ""))
    if len(iso_to_date_key(text)) != 8:
        return False
    # Ignore sentinel / fixture dates used in older Testing seeds.
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
    # Legacy snapshots written before status tracking still count as finalized data.
    snap_q: Dict[str, Any] = {"snapshot_date_ist": {"$gte": "2020-01-01"}}
    if on_or_before:
        snap_q["snapshot_date_ist"] = {
            "$gte": "2020-01-01",
            "$lte": date_key_to_iso(iso_to_date_key(on_or_before)),
        }
    legacy = await db[SNAPSHOT_COLLECTION].find_one(
        snap_q,
        {"_id": 0, "snapshot_date_ist": 1},
        sort=[("snapshot_date_ist", -1)],
    )
    if legacy and _is_plausible_iso(legacy.get("snapshot_date_ist")):
        return str(legacy["snapshot_date_ist"])
    return None


def resolve_analytics_end_date(
    requested_to: str,
    *,
    last_success: Optional[str],
    now: Optional[datetime] = None,
) -> Tuple[str, str]:
    """Cap the Analytics window at yesterday and the last successful rollup.

    Returns (effective_to_iso, data_updated_up_to_iso).
    """
    yesterday = yesterday_ist_iso(now)
    req = date_key_to_iso(iso_to_date_key(requested_to))
    effective = min(req, yesterday)
    if last_success:
        effective = min(effective, date_key_to_iso(iso_to_date_key(last_success)))
        updated = date_key_to_iso(iso_to_date_key(last_success))
    else:
        # No finalized data yet — caller should treat the window as empty.
        updated = None
        effective = (datetime.strptime(req, "%Y-%m-%d").date() - timedelta(days=1)).isoformat()
        # Keep effective < from_date only when caller also clamps keys to [].
    return effective, updated


async def _load_latest_published_rows(db, date_iso: str) -> List[dict]:
    """Latest published product row per brand/dealer/branch/part for one IST date."""
    date_key = iso_to_date_key(date_iso)
    date_iso = date_key_to_iso(date_key)
    pipeline = [
        {
            "$match": {
                "publish_status": "Published",
                "active_date_key": {"$in": [date_key, date_iso]},
            }
        },
        {
            "$project": {
                "brand_id": 1,
                "brand_code": 1,
                "brand_name": 1,
                "dealer_id": 1,
                "dealer_code": 1,
                "dealer_name": 1,
                "branch_id": 1,
                "branch": 1,
                "part_number": 1,
                "part_name": 1,
                "item_name": 1,
                "part_category": 1,
                "category": 1,
                "quantity": 1,
                "available_qty_number": 1,
                "mav": 1,
                "mav_value": 1,
                "unit_value_number": 1,
                "total_value": 1,
                "total_value_number": 1,
                "purchase_aging_days": 1,
                "sales_aging_days": 1,
                "last_receipt_date": 1,
                "last_sales_date": 1,
                "upload_no": 1,
                "published_at": 1,
                "updated_at": 1,
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
        # Fallback: load and keep first-seen after newest-first sort in Python.
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


def build_snapshot_docs(date_iso: str, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    date_key = iso_to_date_key(date_iso)
    date_iso = date_key_to_iso(date_key)
    now = datetime.utcnow().isoformat() + "Z"
    # Dedupe by the durable unique index keys, not display names.
    by_key: Dict[Tuple[str, str, str, str], Dict[str, Any]] = {}
    for r in rows:
        qty = _num(r.get("available_qty_number"), _num(r.get("quantity")))
        unit = _num(r.get("unit_value_number"), _num(r.get("mav_value"), _num(r.get("mav"))))
        value = _num(r.get("total_value_number"), _num(r.get("total_value"), qty * unit))
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


def _totals(rows: List[Dict[str, Any]], *, qty_key: str, value_key: str) -> Tuple[float, float, int]:
    qty = sum(_num(r.get(qty_key)) for r in rows)
    value = sum(_num(r.get(value_key)) for r in rows)
    return qty, value, len(rows)


async def _set_status(db, date_iso: str, payload: dict) -> dict:
    iso = date_key_to_iso(iso_to_date_key(date_iso))
    doc = {
        "rollup_date_ist": iso,
        "updated_at": datetime.utcnow().isoformat() + "Z",
        **payload,
    }
    await db[STATUS_COLLECTION].update_one({"rollup_date_ist": iso}, {"$set": doc}, upsert=True)
    return doc


async def finalize_analytics_rollup_for_date(
    db,
    date_iso: str,
    *,
    force: bool = False,
) -> dict:
    """Capture live published stock into the finalized daily rollup (idempotent)."""
    iso = date_key_to_iso(iso_to_date_key(date_iso))
    if not iso or len(iso_to_date_key(iso)) != 8:
        return {"status": STATUS_FAILED, "rollup_date_ist": date_iso, "error": "invalid date"}

    existing = await get_rollup_status(db, iso)
    if existing and existing.get("status") == STATUS_SUCCESS and not force:
        return {**existing, "status": STATUS_SUCCESS, "skipped": True, "reason": "already_finalized"}

    await _set_status(db, iso, {"status": STATUS_RUNNING, "error": None, "started_at": datetime.utcnow().isoformat() + "Z"})
    try:
        source_rows = await _load_latest_published_rows(db, iso)
        docs = build_snapshot_docs(iso, source_rows)
        raw_qty, raw_value, raw_n = _totals(
            [
                {
                    "qty": _num(r.get("available_qty_number"), _num(r.get("quantity"))),
                    "value": _num(
                        r.get("total_value_number"),
                        _num(
                            r.get("total_value"),
                            _num(r.get("available_qty_number"), _num(r.get("quantity")))
                            * _num(r.get("unit_value_number"), _num(r.get("mav_value"), _num(r.get("mav")))),
                        ),
                    ),
                }
                for r in source_rows
            ],
            qty_key="qty",
            value_key="value",
        )
        snap_qty, snap_value, snap_n = _totals(docs, qty_key="available_qty", value_key="total_value")
        # After unique-key collapse, the durable snapshot totals are the source of truth.
        src_qty, src_value, src_n = snap_qty, snap_value, snap_n

        # Replace the day's snapshots atomically enough for DocumentDB: delete date then insert.
        await db[SNAPSHOT_COLLECTION].delete_many({"snapshot_date_ist": iso})
        if docs:
            chunk = 1000
            for i in range(0, len(docs), chunk):
                await db[SNAPSHOT_COLLECTION].insert_many(docs[i : i + chunk], ordered=False)

        # Re-read and validate persisted totals against the built rollup.
        stored = await db[SNAPSHOT_COLLECTION].find({"snapshot_date_ist": iso}, {"_id": 0, "available_qty": 1, "total_value": 1}).to_list(500000)
        store_qty, store_value, store_n = _totals(stored, qty_key="available_qty", value_key="total_value")
        qty_ok = abs(src_qty - store_qty) <= max(0.01, abs(src_qty) * 1e-9)
        value_ok = abs(src_value - store_value) <= max(0.01, abs(src_value) * 1e-9)
        count_ok = src_n == store_n
        reconciled = bool(qty_ok and value_ok and count_ok)
        if not reconciled:
            raise RuntimeError(
                f"rollup reconciliation failed for {iso}: "
                f"built(n={src_n}, qty={src_qty}, value={src_value}) "
                f"stored(n={store_n}, qty={store_qty}, value={store_value}) "
                f"raw_source(n={raw_n}, qty={raw_qty}, value={raw_value})"
            )

        result = await _set_status(
            db,
            iso,
            {
                "status": STATUS_SUCCESS,
                "error": None,
                "source_record_count": src_n,
                "snapshot_record_count": store_n,
                "source_total_qty": src_qty,
                "source_total_value": src_value,
                "snapshot_total_qty": store_qty,
                "snapshot_total_value": store_value,
                "raw_source_record_count": raw_n,
                "raw_source_total_qty": raw_qty,
                "raw_source_total_value": raw_value,
                "reconciled": True,
                "finalized_at": datetime.utcnow().isoformat() + "Z",
            },
        )
        logger.info(
            "Analytics rollup finalized date=%s rows=%s value=%.2f",
            iso,
            snap_n,
            snap_value,
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


async def backfill_analytics_rollups(
    db,
    dates: List[str],
    *,
    force: bool = False,
) -> dict:
    results = []
    for d in dates:
        results.append(await finalize_analytics_rollup_for_date(db, d, force=force))
    ok = sum(1 for r in results if r.get("status") == STATUS_SUCCESS)
    return {
        "requested": len(dates),
        "success": ok,
        "failed": len(dates) - ok,
        "results": results,
    }
