"""Hybrid history reader — Mongo hot window + S3 cold archives, transparent to callers.

Cold-archive pagination streams gzip JSONL line-by-line and never materializes
the full day into a giant list when page/page_size are requested.
"""

from __future__ import annotations

import gzip
import json
import logging
from datetime import datetime, timedelta
from io import BytesIO
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple
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


def _match_search(row: Dict[str, Any], part_number=None, search=None) -> bool:
    if part_number:
        pn = str(part_number).strip().upper()
        if str(row.get("part_number") or "").strip().upper() != pn:
            return False
    if search:
        q = str(search).strip().lower()
        hay = f"{row.get('part_number') or ''} {row.get('item_name') or row.get('part_name') or ''}".lower()
        if q not in hay:
            return False
    return True


def _iter_jsonl_gz(data: bytes) -> Iterator[Dict[str, Any]]:
    """Yield rows from gzip JSONL without building a full list."""
    with gzip.GzipFile(fileobj=BytesIO(data), mode="rb") as gz:
        for line in gz:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line.decode("utf-8"))


def _load_jsonl_gz(data: bytes) -> List[Dict[str, Any]]:
    return list(_iter_jsonl_gz(data))


def stream_filter_page_from_archive_bytes(
    data: bytes,
    *,
    brand=None,
    dealer=None,
    branch=None,
    part_number=None,
    search=None,
    page: int = 1,
    page_size: int = 50,
    need_total: bool = True,
) -> Dict[str, Any]:
    """Stream gzip JSONL, filter, and collect only the requested page.

    Memory stays O(page_size) for returned rows (plus gzip buffer), not O(day).
    When need_total=True we still scan the stream once to compute total/has_more.
    """
    ps = max(1, min(int(page_size or 50), 500))
    pg = max(1, int(page or 1))
    start = (pg - 1) * ps
    end = start + ps

    matched = 0
    page_rows: List[Dict[str, Any]] = []
    has_more = False

    for row in _iter_jsonl_gz(data):
        if not _match_scope(row, brand, dealer, branch):
            continue
        if not _match_search(row, part_number, search):
            continue
        if start <= matched < end:
            page_rows.append(row)
        elif matched >= end:
            has_more = True
            if not need_total:
                break
        matched += 1

    total = matched if need_total else (start + len(page_rows) + (1 if has_more else 0))
    total_pages = (total + ps - 1) // ps if ps and need_total else None
    return {
        "rows": page_rows,
        "count": len(page_rows),
        "total": total,
        "has_more": has_more if not need_total else (pg * ps < total),
        "page": {
            "page": pg,
            "page_size": ps,
            "total": total,
            "total_pages": total_pages if total_pages is not None else (pg + (1 if has_more else 0)),
            "has_more": has_more if not need_total else (pg * ps < total),
        },
    }


async def _stream_s3_product_day_page(
    db,
    date_key: str,
    *,
    brand=None,
    dealer=None,
    branch=None,
    part_number=None,
    search=None,
    page: int = 1,
    page_size: int = 50,
) -> Optional[Dict[str, Any]]:
    date_iso = date_key_to_iso(date_key)
    manifest = await am.find_s3_readable(db, MODULE_PRODUCT_HISTORY, archive_date=date_iso)
    if not manifest:
        return None
    storage = get_storage()
    # PRUNED / historical reads must never trust local fallback
    if not storage.is_s3():
        logger.warning(
            "Refusing historical S3 read for %s — storage backend is not REAL S3 (manifest status=%s)",
            date_iso,
            manifest.get("status"),
        )
        return {
            "rows": [],
            "count": 0,
            "total": 0,
            "source": "s3_unavailable",
            "archive_unavailable": True,
            "message": "Archive temporarily unavailable. Please retry.",
            "manifest_status": manifest.get("status"),
        }
    backend = str(manifest.get("storage_backend") or "").lower()
    if backend not in {"s3", "real s3"}:
        return {
            "rows": [],
            "count": 0,
            "total": 0,
            "source": "s3_unavailable",
            "archive_unavailable": True,
            "message": "Archive temporarily unavailable. Please retry.",
            "manifest_status": manifest.get("status"),
        }
    try:
        data, _ctype = storage.download_bytes(manifest["storage_key"])
        head = storage.head(manifest["storage_key"])
        if head and str(head.get("storage_provider") or "").lower() == "local":
            logger.warning("Refusing local-fallback object for PRUNED/historical read %s", date_iso)
            return {
                "rows": [],
                "count": 0,
                "total": 0,
                "source": "s3_unavailable",
                "archive_unavailable": True,
                "message": "Archive temporarily unavailable. Please retry.",
                "manifest_status": manifest.get("status"),
            }
    except Exception as exc:
        logger.warning("Failed reading archive %s: %s", manifest.get("storage_key"), exc)
        return {
            "rows": [],
            "count": 0,
            "total": 0,
            "source": "s3_unavailable",
            "archive_unavailable": True,
            "message": "Archive temporarily unavailable. Please retry.",
            "manifest_status": manifest.get("status"),
            "error": str(exc)[:300],
        }
    result = stream_filter_page_from_archive_bytes(
        data,
        brand=brand,
        dealer=dealer,
        branch=branch,
        part_number=part_number,
        search=search,
        page=page,
        page_size=page_size,
        need_total=True,
    )
    result["source"] = "s3"
    result["archive_unavailable"] = False
    result["manifest_status"] = manifest.get("status")
    result["manifest_record_count"] = manifest.get("record_count")
    return result


async def _read_s3_product_day(db, date_key: str) -> List[Dict[str, Any]]:
    """Full-day load — used only for non-paginated callers (exports/summaries)."""
    date_iso = date_key_to_iso(date_key)
    manifest = await am.find_s3_readable(db, MODULE_PRODUCT_HISTORY, archive_date=date_iso)
    if not manifest:
        return []
    storage = get_storage()
    if not storage.is_s3():
        logger.warning(
            "Refusing historical S3 read for %s — not REAL S3 (status=%s)",
            date_iso,
            manifest.get("status"),
        )
        return []
    backend = str(manifest.get("storage_backend") or "").lower()
    if backend not in {"s3", "real s3"}:
        return []
    try:
        data, _ctype = storage.download_bytes(manifest["storage_key"])
        head = storage.head(manifest["storage_key"])
        if head and str(head.get("storage_provider") or "").lower() == "local":
            logger.warning("Refusing local-fallback object for historical read %s", date_iso)
            return []
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


async def _read_mongo_product_day_page(
    db,
    date_key: str,
    *,
    brand=None,
    dealer=None,
    branch=None,
    part_number=None,
    search=None,
    page: int = 1,
    page_size: int = 50,
) -> Dict[str, Any]:
    """Mongo path: filter in query where possible; paginate without loading 300k when searchable."""
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
    if part_number:
        query["part_number"] = part_number

    ps = max(1, min(int(page_size or 50), 500))
    pg = max(1, int(page or 1))
    skip = (pg - 1) * ps

    # Free-text search still needs a filtered scan, but we stream cursor and keep only page
    if search:
        q = str(search).strip().lower()
        matched = 0
        page_rows: List[Dict[str, Any]] = []
        cursor = db.products.find(query, {"_id": 0})
        async for row in _aiter_cursor(cursor):
            hay = f"{row.get('part_number') or ''} {row.get('item_name') or row.get('part_name') or ''}".lower()
            if q not in hay:
                continue
            if skip <= matched < skip + ps:
                page_rows.append(row)
            matched += 1
        total = matched
    else:
        try:
            total = await db.products.count_documents(query)
        except Exception:
            total = len(await db.products.find(query, {"_id": 0}).to_list(300000))
        page_rows = await db.products.find(query, {"_id": 0}).skip(skip).limit(ps).to_list(ps)

    return {
        "rows": page_rows,
        "count": len(page_rows),
        "total": total,
        "source": "mongo",
        "page": {
            "page": pg,
            "page_size": ps,
            "total": total,
            "total_pages": (total + ps - 1) // ps if ps else 0,
            "has_more": pg * ps < total,
        },
    }


async def _aiter_cursor(cursor):
    """Support both Motor cursors and FakeCursor (list-like)."""
    if hasattr(cursor, "to_list") and not hasattr(cursor, "__aiter__"):
        for row in await cursor.to_list(300000):
            yield row
        return
    async for row in cursor:
        yield row


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
    page: Optional[int] = None,
    page_size: Optional[int] = None,
    part_number: Optional[str] = None,
    search: Optional[str] = None,
    record_usage: bool = True,
) -> Dict[str, Any]:
    """Return product history rows from Mongo and/or S3 based on date hotness.

    When page/page_size are set for a single date, cold archives are streamed.
    Multi-day paginated reads fall back to per-day streaming then merge pages
    for the requested window (still without holding full multi-day lists when
    only one day is requested — the common Product Hub History View path).
    """
    if date_key:
        keys = [_normalize_date_key(date_key)]
    else:
        if not from_date or not to_date:
            today = datetime.now(IST).strftime("%Y%m%d")
            keys = [today]
        else:
            keys = _iter_date_keys(from_date, to_date)

    paginate = page is not None or page_size is not None

    # Optimized single-day paginated path (View UI).
    # Mongo wins whenever source rows still exist; S3 is used only after prune
    # (or when Mongo is empty and a readable archive exists).
    if paginate and len(keys) == 1:
        dk = keys[0]
        ps = max(1, min(int(page_size or 50), 500))
        pg = max(1, int(page or 1))
        sources: Dict[str, str] = {}
        result = await _read_mongo_product_day_page(
            db,
            dk,
            brand=brand,
            dealer=dealer,
            branch=branch,
            part_number=part_number,
            search=search,
            page=pg,
            page_size=ps,
        )
        if result["total"] > 0:
            sources[dk] = "mongo"
        else:
            streamed = await _stream_s3_product_day_page(
                db,
                dk,
                brand=brand,
                dealer=dealer,
                branch=branch,
                part_number=part_number,
                search=search,
                page=pg,
                page_size=ps,
            )
            if streamed and streamed.get("archive_unavailable"):
                # VERIFIED archive exists but cannot be read — never pretend empty.
                return {
                    "rows": [],
                    "count": 0,
                    "total": 0,
                    "sources": {dk: "s3_unavailable"},
                    "mongo_count": 0,
                    "s3_count": 0,
                    "archive_unavailable": True,
                    "message": streamed.get("message")
                    or "Archive temporarily unavailable. Please retry.",
                    "page": {
                        "page": pg,
                        "page_size": ps,
                        "total": 0,
                        "total_pages": 0,
                        "has_more": False,
                    },
                }
            if streamed:
                result = streamed
                sources[dk] = "s3"
            else:
                sources[dk] = "mongo"

        rows = result.get("rows") or []
        if record_usage and sources.get(dk) == "s3":
            try:
                import storage_usage as su

                await su.record_storage_usage(
                    db,
                    operation=su.OP_VIEW_READ,
                    bytes_count=sum(len(str(r)) for r in rows),
                    brand=brand or "",
                    dealer=dealer or "",
                    branch=branch or "",
                    module="product-history",
                    request_count=1,
                )
            except Exception:
                pass

        return {
            "rows": rows,
            "count": len(rows),
            "total": result.get("total") or 0,
            "sources": sources,
            "mongo_count": result.get("total") if sources.get(dk) == "mongo" else 0,
            "s3_count": result.get("total") if sources.get(dk) == "s3" else 0,
            "archive_unavailable": False,
            "page": result.get("page"),
        }

    # Non-paginated / multi-day path (exports, summaries) — existing behavior
    mongo_rows: List[Dict[str, Any]] = []
    s3_rows: List[Dict[str, Any]] = []
    sources = {}

    for dk in keys:
        rows = await _read_mongo_product_day(db, dk, brand, dealer, branch)
        if rows:
            mongo_rows.extend(rows)
            sources[dk] = "mongo"
            continue
        archived = await am.find_s3_readable(db, MODULE_PRODUCT_HISTORY, archive_date=date_key_to_iso(dk))
        if archived:
            rows = await _read_s3_product_day(db, dk)
            rows = [r for r in rows if _match_scope(r, brand, dealer, branch)]
            s3_rows.extend(rows)
            sources[dk] = "s3"
        else:
            sources[dk] = "mongo"

    combined = mongo_rows + s3_rows
    if part_number or search:
        combined = [r for r in combined if _match_search(r, part_number, search)]

    total = len(combined)
    page_meta = None
    if paginate:
        ps = max(1, min(int(page_size or 50), 500))
        pg = max(1, int(page or 1))
        start = (pg - 1) * ps
        end = start + ps
        page_rows = combined[start:end]
        page_meta = {
            "page": pg,
            "page_size": ps,
            "total": total,
            "total_pages": (total + ps - 1) // ps if ps else 0,
            "has_more": end < total,
        }
        combined = page_rows

    if record_usage and s3_rows:
        try:
            import storage_usage as su

            await su.record_storage_usage(
                db,
                operation=su.OP_VIEW_READ,
                bytes_count=sum(len(str(r)) for r in combined),
                brand=brand or "",
                dealer=dealer or "",
                branch=branch or "",
                module="product-history",
                request_count=1,
            )
        except Exception:
            pass

    return {
        "rows": combined,
        "count": len(combined),
        "total": total,
        "sources": sources,
        "mongo_count": len(mongo_rows),
        "s3_count": len(s3_rows),
        "page": page_meta,
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
    """Aggregate hybrid rows into the Product Hub History list shape.

    Prefer verified archive manifests for cold dates (record counts) to avoid
    decompressing every cold day when only summary cards are needed.
    """
    if date_key:
        keys = [_normalize_date_key(date_key)]
    elif from_date and to_date:
        keys = _iter_date_keys(from_date, to_date)
    else:
        keys = [datetime.now(IST).strftime("%Y%m%d")]

    buckets: Dict[Tuple[str, str, str, str], Dict[str, Any]] = {}

    for dk in keys:
        # Always use filtered read for scoped summaries (streams when cold+single)
        result = await read_product_history(
            db,
            date_key=dk,
            brand=brand,
            dealer=dealer,
            branch=branch,
            # No page → full day for summary aggregation of one day at a time
            # but we process one date_key per loop to bound peak memory to one day.
        )
        for r in result["rows"]:
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
    rows.sort(
        key=lambda x: (x.get("date_key") or "", x.get("brand") or "", x.get("dealer") or "", x.get("branch") or ""),
        reverse=True,
    )
    return rows
