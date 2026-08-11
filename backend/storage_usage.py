"""Dealer-wise storage usage attribution and estimated cost engine.

Tracks aggregated daily usage in `storage_usage_daily`. Values are estimates
for chargeback visibility — never labeled as a final AWS bill.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from s3_storage import s3_pricing_config

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

OP_UPLOAD = "UPLOAD"
OP_ARCHIVE_WRITE = "ARCHIVE_WRITE"
OP_VIEW_READ = "VIEW_READ"
OP_DOWNLOAD = "DOWNLOAD"
OP_REPORT_GENERATION = "REPORT_GENERATION"
OP_OTHER_STORAGE_READ = "OTHER_STORAGE_READ"


def _ist_date_key(dt: Optional[datetime] = None) -> str:
    value = dt or datetime.now(IST)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc).astimezone(IST)
    else:
        value = value.astimezone(IST)
    return value.strftime("%Y-%m-%d")


def estimate_costs(
    *,
    stored_bytes: int = 0,
    upload_bytes: int = 0,
    view_bytes: int = 0,
    download_bytes: int = 0,
    put_requests: int = 0,
    get_requests: int = 0,
    download_requests: int = 0,
    pricing: Optional[Dict[str, float]] = None,
) -> Dict[str, float]:
    """Estimate storage / request / transfer cost in USD for a usage slice."""
    p = pricing or s3_pricing_config()
    gb = max(0, int(stored_bytes or 0)) / (1024 ** 3)
    egress_bytes = max(0, int(download_bytes or 0))
    egress_gb = egress_bytes / (1024 ** 3)
    free_gb = float(p.get("free_egress_gb") or 0)
    billable_egress = max(0.0, egress_gb - free_gb)

    storage_cost = gb * float(p.get("storage_price_per_gb_month") or 0)
    put_cost = (max(0, int(put_requests or 0)) / 1000.0) * float(p.get("put_price_per_1000") or 0)
    get_cost = (
        (max(0, int(get_requests or 0) + int(download_requests or 0)) / 1000.0)
        * float(p.get("get_price_per_1000") or 0)
    )
    transfer_cost = billable_egress * float(p.get("egress_price_per_gb") or 0)
    total = storage_cost + put_cost + get_cost + transfer_cost
    return {
        "estimated_storage_cost": round(storage_cost, 6),
        "estimated_request_cost": round(put_cost + get_cost, 6),
        "estimated_transfer_cost": round(transfer_cost, 6),
        "estimated_total_cost": round(total, 6),
    }


async def ensure_usage_indexes(db) -> None:
    await db.storage_usage_daily.create_index(
        [
            ("date_key", 1),
            ("brand", 1),
            ("dealer", 1),
            ("branch", 1),
            ("module", 1),
        ],
        unique=True,
        name="uq_storage_usage_daily_dims",
    )
    await db.storage_usage_daily.create_index([("date_key", 1), ("dealer", 1)])
    await db.storage_usage_daily.create_index([("date_key", 1), ("brand", 1)])


async def record_storage_usage(
    db,
    *,
    operation: str,
    bytes_count: int = 0,
    brand: str = "",
    dealer: str = "",
    branch: str = "",
    module: str = "",
    user_id: str = "",
    request_count: int = 1,
    date_key: Optional[str] = None,
    stored_bytes_delta: int = 0,
) -> None:
    """Increment daily aggregated usage. Best-effort — never breaks callers."""
    if db is None:
        return
    try:
        dk = date_key or _ist_date_key()
        brand = str(brand or "")
        dealer = str(dealer or "")
        branch = str(branch or "")
        module = str(module or "other")
        op = str(operation or OP_OTHER_STORAGE_READ).upper()
        nbytes = max(0, int(bytes_count or 0))
        nreq = max(0, int(request_count or 0))
        stored_delta = max(0, int(stored_bytes_delta or 0))

        inc: Dict[str, Any] = {}
        if op == OP_UPLOAD:
            inc["upload_bytes"] = nbytes
            inc["put_requests"] = nreq
            inc["stored_bytes"] = stored_delta or nbytes
        elif op == OP_ARCHIVE_WRITE:
            inc["archive_bytes"] = nbytes
            inc["put_requests"] = nreq
            inc["stored_bytes"] = stored_delta or nbytes
        elif op == OP_DOWNLOAD:
            inc["download_bytes"] = nbytes
            inc["download_requests"] = nreq
            inc["get_requests"] = nreq
        elif op in {OP_VIEW_READ, OP_REPORT_GENERATION, OP_OTHER_STORAGE_READ}:
            inc["view_bytes"] = nbytes
            inc["get_requests"] = nreq
        else:
            inc["view_bytes"] = nbytes
            inc["get_requests"] = nreq

        await db.storage_usage_daily.update_one(
            {
                "date_key": dk,
                "brand": brand,
                "dealer": dealer,
                "branch": branch,
                "module": module,
            },
            {
                "$inc": inc,
                "$set": {"updated_at": datetime.now(timezone.utc).isoformat()},
                "$setOnInsert": {
                    "date_key": dk,
                    "brand": brand,
                    "dealer": dealer,
                    "branch": branch,
                    "module": module,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
            },
            upsert=True,
        )
        # Optional lightweight event breadcrumb (capped usefulness; summaries are primary)
        if user_id:
            try:
                await db.storage_usage_events.insert_one(
                    {
                        "date_key": dk,
                        "brand": brand,
                        "dealer": dealer,
                        "branch": branch,
                        "module": module,
                        "operation": op,
                        "bytes": nbytes,
                        "user_id": user_id,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                )
            except Exception:
                pass
    except Exception as exc:
        logger.debug("storage usage record skipped: %s", exc)


def _month_prefix(month: str) -> str:
    return (month or "")[:7]


async def dealer_usage_ranking(
    db,
    *,
    month: Optional[str] = None,
    brand: Optional[str] = None,
    dealer: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Aggregate dealer-wise usage for a calendar month (IST date_key YYYY-MM-*)."""
    month = _month_prefix(month or _ist_date_key()[:7])
    q: Dict[str, Any] = {"date_key": {"$regex": f"^{month}"}}
    if brand:
        q["brand"] = brand
    if dealer:
        q["dealer"] = dealer

    rows = await db.storage_usage_daily.find(q, {"_id": 0}).to_list(100000)
    by_dealer: Dict[str, Dict[str, Any]] = {}
    pricing = s3_pricing_config()

    for r in rows:
        dname = str(r.get("dealer") or "(unknown)")
        slot = by_dealer.setdefault(
            dname,
            {
                "dealer": dname,
                "brands": set(),
                "branches": set(),
                "stored_bytes": 0,
                "upload_bytes": 0,
                "archive_bytes": 0,
                "view_bytes": 0,
                "download_bytes": 0,
                "put_requests": 0,
                "get_requests": 0,
                "download_requests": 0,
            },
        )
        if r.get("brand"):
            slot["brands"].add(str(r["brand"]))
        if r.get("branch"):
            slot["branches"].add(str(r["branch"]))
        for k in (
            "stored_bytes",
            "upload_bytes",
            "archive_bytes",
            "view_bytes",
            "download_bytes",
            "put_requests",
            "get_requests",
            "download_requests",
        ):
            slot[k] += int(r.get(k) or 0)

    out = []
    for slot in by_dealer.values():
        # Prefer stored_bytes; fall back to upload+archive for estimate base
        stored = slot["stored_bytes"] or (slot["upload_bytes"] + slot["archive_bytes"])
        costs = estimate_costs(
            stored_bytes=stored,
            upload_bytes=slot["upload_bytes"],
            view_bytes=slot["view_bytes"],
            download_bytes=slot["download_bytes"],
            put_requests=slot["put_requests"],
            get_requests=slot["get_requests"],
            download_requests=slot["download_requests"],
            pricing=pricing,
        )
        out.append(
            {
                "dealer": slot["dealer"],
                "brands": sorted(slot["brands"]),
                "branches": len(slot["branches"]),
                "branch_names": sorted(slot["branches"])[:50],
                "stored_gb": round(stored / (1024 ** 3), 6),
                "uploaded_gb": round(slot["upload_bytes"] / (1024 ** 3), 6),
                "viewed_gb": round(slot["view_bytes"] / (1024 ** 3), 6),
                "downloaded_gb": round(slot["download_bytes"] / (1024 ** 3), 6),
                "put_requests": slot["put_requests"],
                "get_requests": slot["get_requests"] + slot["download_requests"],
                "estimated_cost": costs["estimated_total_cost"],
                "estimated_storage_cost": costs["estimated_storage_cost"],
                "estimated_request_cost": costs["estimated_request_cost"],
                "estimated_transfer_cost": costs["estimated_transfer_cost"],
            }
        )
    out.sort(key=lambda x: x["estimated_cost"], reverse=True)
    return out


async def month_usage_totals(db, month: Optional[str] = None) -> Dict[str, Any]:
    month = _month_prefix(month or _ist_date_key()[:7])
    rows = await db.storage_usage_daily.find({"date_key": {"$regex": f"^{month}"}}, {"_id": 0}).to_list(100000)
    totals = {
        "upload_bytes": 0,
        "archive_bytes": 0,
        "view_bytes": 0,
        "download_bytes": 0,
        "stored_bytes": 0,
        "put_requests": 0,
        "get_requests": 0,
        "download_requests": 0,
    }
    for r in rows:
        for k in totals:
            totals[k] += int(r.get(k) or 0)
    stored = totals["stored_bytes"] or (totals["upload_bytes"] + totals["archive_bytes"])
    costs = estimate_costs(
        stored_bytes=stored,
        upload_bytes=totals["upload_bytes"],
        view_bytes=totals["view_bytes"],
        download_bytes=totals["download_bytes"],
        put_requests=totals["put_requests"],
        get_requests=totals["get_requests"],
        download_requests=totals["download_requests"],
    )
    return {
        "month": month,
        **totals,
        "stored_bytes_effective": stored,
        **costs,
        "pricing": s3_pricing_config(),
        "cost_label": "Estimated Cost",
    }
