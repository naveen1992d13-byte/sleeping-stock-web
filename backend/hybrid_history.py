"""Hybrid history reader — Mongo hot window + S3 cold archives, transparent to callers."""

from __future__ import annotations

import gzip
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

import archive_manifest as am
from history_archive import MODULE_PRODUCT_HISTORY, date_key_to_iso, iso_to_date_key
from s3_storage import get_storage, product_mongo_hot_days

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


def _normalize_date_key(value: str) -> str:
    return iso_to_date_key(value)


def _iter_date_keys(from_date: str, to_date: str) -> List[str]:
    start = _normalize_date_key(from_date)
    end = _normalize_date_key(to_date)
    if len(start) != 8 or len(end) != 8:
        return []
    cur = datetime(int(start[0:4]), int(start[4:6]), int(start[6:8]), tzinfo=IST)
    last = datetime(int(end[0:4]), int(end[4:6]), int(end[6:8]), tzinfo=IST)
    out = []
    while cur <= last:
        out.append(cur.strftime("%Y%m%d"))
        cur += timedelta(days=1)
    return out


def _hot_cutoff_date_key(hot_days: Optional[int] = None) -> str:
    days = hot_days if hot_days is not None else product_mongo_hot_days()
    cutoff = datetime.now(IST).date() - timedelta(days=max(0, days - 1))
    return cutoff.strftime("%Y%m%d")


def is_mongo_hot(date_key: str, hot_days: Optional[int] = None) -> bool:
    dk = _normalize_date_key(date_key)
    return dk >= _hot_cutoff_date_key(hot_days)


def _match_scope(row: Dict[str, Any], brand=None, dealer=None, branch=None) -> bool:
    if brand and str(row.get("brand_name") or row.get("brand") or "") != str(brand):
        return False
    if dealer and str(row.get("dealer_name") or row.get("dealer") or "") != str(dealer):
        return False
    branch_val = row.get("branch") or row.get("branch_name")
    if branch and str(branch_val or "") != str(branch):
        return False
    return True


def _load_jsonl_gz(data: bytes) -> List[Dict[str, Any]]:
    rows = []
    with gzip.GzipFile(fileobj=__import__("io").BytesIO(data), mode="rb") as gz:
        for line in gz:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line.decode("utf-8")))
    return rows


async def _read_s3_product_day(db, date_key: str) -> List[Dict[str, Any]]:
    date_iso = date_key_to_iso(date_key)
    manifest = await am.find_verified(db, MODULE_PRODUCT_HISTORY, archive_date=date_iso)
    if not manifest:
        return []
    storage = get_storage()
    try:
        data, _ = storage.download_bytes(manifest["storage_key"])
    except Exception as exc:
        logger.warning("Failed reading archive %s: %s", manifest.get("storage_key"), exc)
        return []
    return _load_jsonl_gz(data)


async def _read_mongo_product_day(db, date_key: str, brand=None, dealer=None, branch=None) -> List[Dict[str, Any]]:
    dk = _normalize_date_key(date_key)
    date_iso = date_key_to_iso(dk)
    query: Dict[str, Any] = {
        "publish_status": "Published",
        "active_date_key": {"$in": [dk, date_iso]},
    }
    if brand:
        query["brand_name"] = brand
    if dealer:
        query["dealer_name"] = dealer
    if branch:
        query["branch"] = branch
    return await db.products.find(query, {"_id": 0}).to_list(300000)


async def read_product_history(
    db,
    *,
    date_key: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    brand: Optional[str] = None,
    dealer: Optional[str] = None,
    branch: Optional[str] = None,
    hot_days: Optional[int] = None,
) -> Dict[str, Any]:
    """Return product history rows from Mongo and/or S3 based on date hotness.

    Caller does not need to know storage location.
    """
    if date_key:
        keys = [_normalize_date_key(date_key)]
    else:
        if not from_date or not to_date:
            # Default: today only
            today = datetime.now(IST).strftime("%Y%m%d")
            keys = [today]
        else:
            keys = _iter_date_keys(from_date, to_date)

    mongo_rows: List[Dict[str, Any]] = []
    s3_rows: List[Dict[str, Any]] = []
    sources: Dict[str, str] = {}

    for dk in keys:
        if is_mongo_hot(dk, hot_days):
            rows = await _read_mongo_product_day(db, dk, brand, dealer, branch)
            # If Mongo empty but archive exists (edge), fall through to S3
            if rows:
                mongo_rows.extend(rows)
                sources[dk] = "mongo"
                continue
            archived = await am.find_verified(db, MODULE_PRODUCT_HISTORY, archive_date=date_key_to_iso(dk))
            if archived:
                rows = await _read_s3_product_day(db, dk)
                rows = [r for r in rows if _match_scope(r, brand, dealer, branch)]
                s3_rows.extend(rows)
                sources[dk] = "s3"
            else:
                sources[dk] = "mongo"
        else:
            rows = await _read_s3_product_day(db, dk)
            if rows:
                rows = [r for r in rows if _match_scope(r, brand, dealer, branch)]
                s3_rows.extend(rows)
                sources[dk] = "s3"
            else:
                # Cold date without archive — still try Mongo (prune never ran)
                rows = await _read_mongo_product_day(db, dk, brand, dealer, branch)
                mongo_rows.extend(rows)
                sources[dk] = "mongo_fallback"

    combined = mongo_rows + s3_rows
    return {
        "rows": combined,
        "count": len(combined),
        "sources": sources,
        "mongo_count": len(mongo_rows),
        "s3_count": len(s3_rows),
    }


async def summarize_product_history(
    db,
    *,
    date_key: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    brand: Optional[str] = None,
    dealer: Optional[str] = None,
    branch: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Aggregate hybrid rows into the Product Hub History list shape."""
    result = await read_product_history(
        db,
        date_key=date_key,
        from_date=from_date,
        to_date=to_date,
        brand=brand,
        dealer=dealer,
        branch=branch,
    )
    buckets: Dict[Tuple[str, str, str, str], Dict[str, Any]] = {}
    for r in result["rows"]:
        dk = _normalize_date_key(str(r.get("active_date_key") or ""))
        b = r.get("brand_name") or ""
        d = r.get("dealer_name") or ""
        br = r.get("branch") or ""
        key = (dk, b, d, br)
        slot = buckets.setdefault(
            key,
            {
                "date_key": dk,
                "brand": b,
                "dealer": d,
                "branch": br,
                "records": 0,
                "total_available_qty": 0.0,
                "total_value": 0.0,
                "last_published_at": r.get("published_at"),
            },
        )
        slot["records"] += 1
        slot["total_available_qty"] += float(r.get("available_qty_number", r.get("quantity", 0)) or 0)
        slot["total_value"] += float(r.get("total_value_number", r.get("total_value", 0)) or 0)
        pub = r.get("published_at")
        if pub and (not slot["last_published_at"] or str(pub) > str(slot["last_published_at"])):
            slot["last_published_at"] = pub
    rows = list(buckets.values())
    rows.sort(key=lambda x: (x.get("date_key") or "", x.get("brand") or "", x.get("dealer") or "", x.get("branch") or ""), reverse=True)
    return rows
