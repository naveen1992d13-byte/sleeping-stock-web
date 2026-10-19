"""Finalized Analytics daily rollup — 11:00 PM IST, before archive/prune.

One IST date is finalized from that day's Published Product Hub rows, one
brand/dealer/branch at a time (bounded memory, throttled for the shared
DocumentDB). Writes:
  - analytics_daily_aggs: one row per date/brand/dealer/branch/part type with
    both aging bases x 5 buckets and missing-aging totals (screen source)
  - analytics_stock_daily_snapshots: per-part rows (Auto Perpetual fallback)

Aging is measured as of the snapshot date. Request paths never call this
module's builders and never read S3; the S3 archive fallback lives only in
scripts/analytics_snapshot.py.

Screen APIs must read pre-aggregated daily metrics — never the 100k+ part rows.
"""
from __future__ import annotations

import asyncio
import logging
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger("nmts.analytics_rollup")
IST = ZoneInfo("Asia/Kolkata")

SNAPSHOT_COLLECTION = "analytics_stock_daily_snapshots"
STATUS_COLLECTION = "analytics_rollup_status"
DAILY_AGG_COLLECTION = "analytics_daily_aggs"

STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
STATUS_RUNNING = "running"
STATUS_NO_SOURCE = "no_source"
STATUS_LOCKED = "locked"

# v2: aging from source dates as of the snapshot date, missing-aging totals,
# build_id stale-row cleanup. v1 rows are shown as "rebuild needed".
ROLLUP_VERSION = 2

BRANCH_PAUSE_SECONDS = 0.25
DATE_PAUSE_SECONDS = 2.0
WRITE_BATCH = 500
RUNNING_LEASE_SECONDS = 90 * 60

AGING_BUCKETS = [
    "0–90 Days",
    "91–180 Days",
    "181–270 Days",
    "271–361 Days",
    ">361 Days",
]
AGING_BASES = ("purchase", "sales")

# Upload v1 stores *_ageing_days (British spelling); v2 stores only the dates.
_STORED_DAYS_FIELDS = {
    "purchase": ("purchase_aging_days", "purchase_ageing_days"),
    "sales": ("sales_aging_days", "sales_ageing_days"),
}
_SOURCE_DATE_FIELDS = {
    "purchase": ("last_receipt_date", "last_purchase_date", "receipt_date"),
    "sales": ("last_sales_date", "last_sale_date", "sales_date"),
}
_DATE_FORMATS = ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d.%m.%Y", "%d-%b-%Y", "%d %b %Y")
_EXCEL_EPOCH = date(1899, 12, 30)

SOURCE_PROJECTION = {
    "_id": 0,
    "brand_id": 1, "brand_code": 1, "brand_name": 1,
    "dealer_id": 1, "dealer_code": 1, "dealer_name": 1,
    "branch_id": 1, "branch": 1, "part_number": 1, "part_name": 1, "item_name": 1,
    "part_category": 1, "category": 1,
    "quantity": 1, "available_qty_number": 1,
    "mav": 1, "mav_value": 1, "unit_value_number": 1,
    "total_value": 1, "total_value_number": 1,
    "purchase_aging_days": 1, "sales_aging_days": 1,
    "purchase_ageing_days": 1, "sales_ageing_days": 1,
    "last_receipt_date": 1, "last_sales_date": 1,
    "upload_no": 1, "published_at": 1, "updated_at": 1,
}

BranchKey = Tuple[Any, Any, Any]  # raw (brand_name, dealer_name, branch) values
RowsForBranch = Callable[[BranchKey], AsyncIterator[dict]]

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


def parse_source_date(value: Any) -> Optional[date]:
    """Parse an upload date cell (dd-mm-YYYY from uploads, ISO, Excel serial)."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text or text == "-":
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    if len(text) >= 10:
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d").date()
        except ValueError:
            pass
    try:
        serial = float(text)
    except ValueError:
        return None
    if 20000 <= serial <= 80000:  # Excel serial day number (1954..2119)
        return _EXCEL_EPOCH + timedelta(days=int(serial))
    return None


def _stored_days(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", str(value).replace(",", ""))
    if not match:
        return None
    try:
        return max(0, int(float(match.group(0))))
    except (TypeError, ValueError):
        return None


def aging_days_as_of(row: Dict[str, Any], basis: str, snapshot_date: date) -> Optional[int]:
    """Aging days on the snapshot date; None when the row has no usable aging."""
    for field in _SOURCE_DATE_FIELDS[basis]:
        parsed = parse_source_date(row.get(field))
        if parsed:
            return max(0, (snapshot_date - parsed).days)
    for field in _STORED_DAYS_FIELDS[basis]:
        days = _stored_days(row.get(field))
        if days is not None:
            return days
    return None


def _snapshot_date(date_iso: str) -> date:
    return datetime.strptime(date_key_to_iso(iso_to_date_key(date_iso)), "%Y-%m-%d").date()


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


def rollup_version_of(status_row: Optional[dict]) -> int:
    try:
        return int((status_row or {}).get("rollup_version") or 1)
    except (TypeError, ValueError):
        return 1


def is_current_success(status_row: Optional[dict]) -> bool:
    return bool(status_row) and status_row.get("status") == STATUS_SUCCESS and rollup_version_of(status_row) >= ROLLUP_VERSION


async def last_successful_rollup_date(
    db,
    *,
    on_or_before: Optional[str] = None,
    min_version: Optional[int] = None,
) -> Optional[str]:
    q: Dict[str, Any] = {"status": STATUS_SUCCESS, "rollup_date_ist": {"$gte": "2020-01-01"}}
    if on_or_before:
        q["rollup_date_ist"] = {
            "$gte": "2020-01-01",
            "$lte": date_key_to_iso(iso_to_date_key(on_or_before)),
        }
    if min_version:
        q["rollup_version"] = {"$gte": int(min_version)}
    row = await db[STATUS_COLLECTION].find_one(q, {"_id": 0, "rollup_date_ist": 1}, sort=[("rollup_date_ist", -1)])
    if row and _is_plausible_iso(row.get("rollup_date_ist")):
        return str(row["rollup_date_ist"])
    if min_version:
        # Legacy collections carry no version; never treat them as current.
        return None
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


def _row_category(r: dict) -> str:
    return pc.normalize_part_category(str(r.get("category") or r.get("part_category") or "")) or "Others"


def snapshot_doc_from_row(
    date_iso: str,
    snapshot_date: date,
    r: Dict[str, Any],
    *,
    now: str,
    build_id: Optional[str] = None,
) -> Dict[str, Any]:
    qty, unit, value = _row_qty_unit_value(r)
    doc = {
        "snapshot_date_ist": date_iso,
        "brand_id": _text(r.get("brand_id") or r.get("brand_code")),
        "dealer_id": _text(r.get("dealer_id") or r.get("dealer_code") or r.get("dealer_name")),
        "branch_id": _text(r.get("branch_id") or r.get("branch") or r.get("branch_name")),
        "brand_name": r.get("brand_name"),
        "dealer_name": r.get("dealer_name"),
        "branch_name": r.get("branch") if "branch" in r else r.get("branch_name"),
        "part_number": _text(r.get("part_number")),
        "part_name": r.get("item_name") or r.get("part_name"),
        "category": r.get("part_category") or r.get("category") or "",
        "available_qty": qty,
        "unit_value": unit,
        "total_value": value,
        # Computed as of the snapshot date (None = genuinely missing aging).
        "purchase_aging_days": aging_days_as_of(r, "purchase", snapshot_date),
        "sales_aging_days": aging_days_as_of(r, "sales", snapshot_date),
        "last_receipt_date": r.get("last_receipt_date"),
        "last_sales_date": r.get("last_sales_date"),
        "upload_no": r.get("upload_no"),
        "updated_at": now,
        "finalized": True,
        "rollup_version": ROLLUP_VERSION,
    }
    if build_id:
        doc["build_id"] = build_id
    return doc


def build_snapshot_docs(date_iso: str, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    date_iso = date_key_to_iso(iso_to_date_key(date_iso))
    snap_date = _snapshot_date(date_iso)
    now = _utc_now_iso()
    by_key: Dict[Tuple[str, str, str, str], Dict[str, Any]] = {}
    for r in rows:
        doc = snapshot_doc_from_row(date_iso, snap_date, r, now=now)
        key = (doc["brand_id"], doc["dealer_id"], doc["branch_id"], doc["part_number"].casefold())
        by_key[key] = doc
    return list(by_key.values())


def _empty_slot() -> Dict[str, float]:
    return {"qty": 0.0, "value": 0.0, "items": 0.0}


def new_agg_group(date_iso: str, brand: str, dealer: str, branch: str, category: str, *, now: str) -> Dict[str, Any]:
    return {
        "rollup_date_ist": date_iso,
        "brand_name": brand,
        "dealer_name": dealer,
        "branch_name": branch,
        "category": category,
        "stock_qty": 0.0,
        "stock_value": 0.0,
        "line_count": 0,
        "aging_purchase": {b: _empty_slot() for b in AGING_BUCKETS},
        "aging_sales": {b: _empty_slot() for b in AGING_BUCKETS},
        # Missing aging is counted under >361 Days AND reported here.
        "missing_aging_purchase": _empty_slot(),
        "missing_aging_sales": _empty_slot(),
        "updated_at": now,
        "finalized": True,
        "rollup_version": ROLLUP_VERSION,
    }


def add_part_to_group(g: Dict[str, Any], qty: float, value: float, purchase_days, sales_days) -> None:
    g["stock_qty"] += qty
    g["stock_value"] += value
    g["line_count"] += 1
    for basis, days in (("purchase", purchase_days), ("sales", sales_days)):
        slot = g[f"aging_{basis}"][_analytics_bucket(days)]
        slot["qty"] += qty
        slot["value"] += value
        slot["items"] += 1.0
        if days is None:
            miss = g[f"missing_aging_{basis}"]
            miss["qty"] += qty
            miss["value"] += value
            miss["items"] += 1.0


def build_daily_agg_docs(date_iso: str, snapshot_docs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Pre-aggregate per brand/dealer/branch/category for fast screen reads."""
    date_iso = date_key_to_iso(iso_to_date_key(date_iso))
    snap_date = _snapshot_date(date_iso)
    now = _utc_now_iso()
    groups: Dict[Tuple[str, str, str, str], dict] = {}
    for r in snapshot_docs:
        cat = _row_category(r)
        key = (_text(r.get("brand_name")), _text(r.get("dealer_name")), _text(r.get("branch_name")), cat)
        g = groups.get(key)
        if not g:
            g = groups[key] = new_agg_group(date_iso, *key, now=now)
        add_part_to_group(
            g,
            _num(r.get("available_qty")),
            _num(r.get("total_value")),
            aging_days_as_of(r, "purchase", snap_date),
            aging_days_as_of(r, "sales", snap_date),
        )
    return list(groups.values())


def _totals(rows: List[Dict[str, Any]], *, qty_key: str, value_key: str) -> Tuple[float, float, int]:
    qty = sum(_num(r.get(qty_key)) for r in rows)
    value = sum(_num(r.get(value_key)) for r in rows)
    return qty, value, len(rows)


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= max(0.01, abs(a) * 1e-9)


async def _aiter_rows(cursor) -> AsyncIterator[dict]:
    """Iterate a Motor/overlay cursor without materialising it (fakes: to_list)."""
    if hasattr(cursor, "__aiter__"):
        async for row in cursor:
            yield row
        return
    for row in await cursor.to_list(None):
        yield row


async def _count(coll, query: dict) -> int:
    if hasattr(coll, "count_documents"):
        return int(await coll.count_documents(query))
    return len(await coll.find(query, {"_id": 0, "build_id": 1}).to_list(None))


def _date_match(iso: str) -> dict:
    return {"publish_status": "Published", "active_date_key": {"$in": [iso_to_date_key(iso), iso]}}


async def list_source_branches(db, date_iso: str) -> List[BranchKey]:
    """Distinct (brand, dealer, branch) with Published rows on that date (DB-side group)."""
    iso = date_key_to_iso(iso_to_date_key(date_iso))
    match = _date_match(iso)
    seen: Dict[Tuple[str, str, str], BranchKey] = {}
    try:
        pipeline = [
            {"$match": match},
            {"$group": {"_id": {"brand": "$brand_name", "dealer": "$dealer_name", "branch": "$branch"}, "n": {"$sum": 1}}},
        ]
        rows = await db.products.aggregate(pipeline, allowDiskUse=True).to_list(20000)
        for r in rows:
            ident = r.get("_id") or {}
            raw = (ident.get("brand"), ident.get("dealer"), ident.get("branch"))
            seen.setdefault(tuple(_text(x) for x in raw), raw)
    except Exception as exc:
        logger.warning("rollup branch group failed for %s, streaming fallback: %s", iso, exc)
        cursor = db.products.find(match, {"_id": 0, "brand_name": 1, "dealer_name": 1, "branch": 1})
        async for r in _aiter_rows(cursor):
            raw = (r.get("brand_name"), r.get("dealer_name"), r.get("branch"))
            seen.setdefault(tuple(_text(x) for x in raw), raw)
    return [seen[k] for k in sorted(seen)]


def db_rows_for_branch(db, date_iso: str) -> RowsForBranch:
    """Newest-first Published rows of one branch for that date (streamed)."""
    iso = date_key_to_iso(iso_to_date_key(date_iso))

    async def rows(branch_key: BranchKey) -> AsyncIterator[dict]:
        brand, dealer, branch = branch_key
        q = {**_date_match(iso), "brand_name": brand, "dealer_name": dealer, "branch": branch}
        cursor = db.products.find(q, SOURCE_PROJECTION).sort([("published_at", -1), ("updated_at", -1)])
        if hasattr(cursor, "batch_size"):
            cursor = cursor.batch_size(WRITE_BATCH)
        async for r in _aiter_rows(cursor):
            yield r

    return rows


async def _write_snapshot_batch(db, docs: List[dict]) -> int:
    if not docs:
        return 0
    try:
        await db[SNAPSHOT_COLLECTION].insert_many(docs, ordered=False)
        return len(docs)
    except Exception as exc:
        # Duplicate part keys under the unique index: keep the inserted rest.
        details = getattr(exc, "details", None) or {}
        errors = details.get("writeErrors") or []
        if errors and all(int(e.get("code") or 0) == 11000 for e in errors):
            logger.warning("rollup snapshot batch skipped %s duplicate part rows", len(errors))
            return len(docs) - len(errors)
        raise


def _lease_active(status_row: dict) -> bool:
    started = _text(status_row.get("started_at"))
    if not started:
        return False
    try:
        ts = datetime.fromisoformat(started.replace("Z", "+00:00"))
    except ValueError:
        return False
    return (datetime.now(timezone.utc) - ts).total_seconds() < RUNNING_LEASE_SECONDS


async def finalize_analytics_rollup_for_date(
    db,
    date_iso: str,
    *,
    force: bool = False,
    branches: Optional[List[BranchKey]] = None,
    rows_for_branch: Optional[RowsForBranch] = None,
    source_kind: str = "products_db",
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    branch_pause: float = BRANCH_PAUSE_SECONDS,
    progress: Optional[Callable[[str], Any]] = None,
) -> dict:
    """Capture one IST date into durable snapshots + pre-aggregated daily metrics.

    Idempotent: a v2 success is skipped unless force. Rows are rewritten per
    branch under a new build_id; rows of that date with any other build_id are
    removed only after every branch was written.
    """
    iso = date_key_to_iso(iso_to_date_key(date_iso))
    if not _is_plausible_iso(iso):
        return {"status": STATUS_FAILED, "rollup_date_ist": date_iso, "error": "invalid date"}

    existing = await get_rollup_status(db, iso)
    if is_current_success(existing) and not force:
        return {**existing, "status": STATUS_SUCCESS, "skipped": True, "reason": "already_finalized"}
    if existing and existing.get("status") == STATUS_RUNNING and _lease_active(existing) and not force:
        return {"status": STATUS_LOCKED, "rollup_date_ist": iso, "reason": "another run holds the lease"}

    say = progress or (lambda msg: logger.info(msg))
    if branches is None:
        branches = await list_source_branches(db, iso)
    if rows_for_branch is None:
        rows_for_branch = db_rows_for_branch(db, iso)

    if not branches:
        # No source rows (no uploads, or already pruned): never overwrite an
        # existing success and never write an empty "0" day.
        say(f"rollup {iso}: no source rows ({source_kind}); existing data kept")
        if existing and existing.get("status") == STATUS_SUCCESS:
            return {**existing, "status": STATUS_NO_SOURCE, "kept_existing": True, "rollup_date_ist": iso}
        return await _set_status(db, iso, {
            "status": STATUS_NO_SOURCE,
            "error": None,
            "source_kind": source_kind,
            "finalized_at": None,
            "rollup_version": ROLLUP_VERSION,
        })

    build_id = uuid.uuid4().hex
    await _set_status(db, iso, {
        "status": STATUS_RUNNING,
        "error": None,
        "started_at": _utc_now_iso(),
        "build_id": build_id,
    })
    try:
        snap_date = _snapshot_date(iso)
        now = _utc_now_iso()
        part_qty = part_value = 0.0
        part_n = written_n = agg_n = 0
        for idx, branch_key in enumerate(branches, start=1):
            brand, dealer, branch = branch_key
            latest: Dict[str, dict] = {}
            async for r in rows_for_branch(branch_key):
                part = _text(r.get("part_number"))
                if part and part.casefold() not in latest:
                    latest[part.casefold()] = r  # newest-first stream: first wins

            groups: Dict[str, dict] = {}
            await db[SNAPSHOT_COLLECTION].delete_many(
                {"snapshot_date_ist": iso, "brand_name": brand, "dealer_name": dealer, "branch_name": branch}
            )
            buf: List[dict] = []
            for r in latest.values():
                doc = snapshot_doc_from_row(iso, snap_date, r, now=now, build_id=build_id)
                cat = _row_category(doc)
                g = groups.get(cat)
                if g is None:
                    g = groups[cat] = new_agg_group(iso, _text(brand), _text(dealer), _text(branch), cat, now=now)
                    g["build_id"] = build_id
                add_part_to_group(g, doc["available_qty"], doc["total_value"],
                                  doc["purchase_aging_days"], doc["sales_aging_days"])
                part_qty += doc["available_qty"]
                part_value += doc["total_value"]
                part_n += 1
                buf.append(doc)
                if len(buf) >= WRITE_BATCH:
                    written_n += await _write_snapshot_batch(db, buf)
                    buf = []
            written_n += await _write_snapshot_batch(db, buf)
            for g in groups.values():
                await db[DAILY_AGG_COLLECTION].update_one(
                    {
                        "rollup_date_ist": iso,
                        "brand_name": g["brand_name"],
                        "dealer_name": g["dealer_name"],
                        "branch_name": g["branch_name"],
                        "category": g["category"],
                    },
                    {"$set": g},
                    upsert=True,
                )
            agg_n += len(groups)
            say(f"rollup {iso}: branch {idx}/{len(branches)} {_text(brand)}/{_text(dealer)}/{_text(branch)} parts={len(latest)}")
            latest.clear()
            await sleep(branch_pause)

        stale_snap = await db[SNAPSHOT_COLLECTION].delete_many({"snapshot_date_ist": iso, "build_id": {"$ne": build_id}})
        stale_agg = await db[DAILY_AGG_COLLECTION].delete_many({"rollup_date_ist": iso, "build_id": {"$ne": build_id}})

        store_n = await _count(db[SNAPSHOT_COLLECTION], {"snapshot_date_ist": iso, "build_id": build_id})
        stored_aggs = await db[DAILY_AGG_COLLECTION].find(
            {"rollup_date_ist": iso}, {"_id": 0, "stock_qty": 1, "stock_value": 1, "line_count": 1}
        ).to_list(50000)
        store_agg_qty = sum(_num(a.get("stock_qty")) for a in stored_aggs)
        store_agg_value = sum(_num(a.get("stock_value")) for a in stored_aggs)
        store_agg_lines = sum(int(a.get("line_count") or 0) for a in stored_aggs)

        reconciled = (
            store_n == written_n == part_n
            and _close(part_qty, store_agg_qty)
            and _close(part_value, store_agg_value)
            and store_agg_lines == part_n
            and len(stored_aggs) == agg_n
        )
        if not reconciled:
            raise RuntimeError(
                f"rollup reconciliation failed for {iso}: "
                f"parts(n={part_n}, written={written_n}, stored={store_n}, qty={part_qty}, value={part_value}) "
                f"aggs(n={agg_n}, stored={len(stored_aggs)}, lines={store_agg_lines}, "
                f"qty={store_agg_qty}, value={store_agg_value})"
            )

        result = await _set_status(
            db,
            iso,
            {
                "status": STATUS_SUCCESS,
                "error": None,
                "rollup_version": ROLLUP_VERSION,
                "build_id": build_id,
                "source_kind": source_kind,
                "branch_count": len(branches),
                "source_record_count": part_n,
                "snapshot_record_count": store_n,
                "daily_agg_record_count": len(stored_aggs),
                "source_total_qty": part_qty,
                "source_total_value": part_value,
                "snapshot_total_qty": part_qty,
                "snapshot_total_value": part_value,
                "daily_agg_total_qty": store_agg_qty,
                "daily_agg_total_value": store_agg_value,
                "stale_rows_removed": _deleted(stale_snap) + _deleted(stale_agg),
                "reconciled": True,
                "finalized_at": _utc_now_iso(),
            },
        )
        logger.info(
            "Analytics rollup finalized date=%s v=%s source=%s branches=%s parts=%s aggs=%s value=%.2f",
            iso, ROLLUP_VERSION, source_kind, len(branches), store_n, len(stored_aggs), store_agg_value,
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


def _deleted(result: Any) -> int:
    if isinstance(result, dict):
        return int(result.get("deleted") or 0)
    return int(getattr(result, "deleted_count", 0) or 0)


async def backfill_analytics_rollups(
    db,
    dates: List[str],
    *,
    force: bool = False,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    date_pause: float = DATE_PAUSE_SECONDS,
    progress: Optional[Callable[[str], Any]] = None,
) -> dict:
    results = []
    for i, d in enumerate(dates):
        if i:
            await sleep(date_pause)
        results.append(await finalize_analytics_rollup_for_date(db, d, force=force, sleep=sleep, progress=progress))
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
