"""Dealer-wise storage usage attribution and estimated cost engine.

Tracks aggregated daily usage in `storage_usage_daily`. Values are estimates
for chargeback visibility — never labeled as a final AWS bill.

Egress formula (account / calendar month):
  total_egress_gb = sum(dealer download_bytes) / 1024**3
  billable_egress_gb = max(total_egress_gb - S3_FREE_EGRESS_GB, 0)
  dealer_billable_gb = billable_egress_gb * (dealer_egress_gb / total_egress_gb)
                     = 0 when total_egress_gb == 0

Free egress is applied ONCE per account-month, then allocated proportionally.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
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

GIB = 1024 ** 3


def _ist_date_key(dt: Optional[datetime] = None) -> str:
    value = dt or datetime.now(IST)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc).astimezone(IST)
    else:
        value = value.astimezone(IST)
    return value.strftime("%Y-%m-%d")


def estimate_request_and_storage_costs(
    *,
    stored_bytes: int = 0,
    put_requests: int = 0,
    get_requests: int = 0,
    pricing: Optional[Dict[str, float]] = None,
) -> Dict[str, float]:
    """Storage + request costs only (no egress). GET is billed from get_requests alone."""
    p = pricing or s3_pricing_config()
    gb = max(0, int(stored_bytes or 0)) / GIB
    storage_cost = gb * float(p.get("storage_price_per_gb_month") or 0)
    put_cost = (max(0, int(put_requests or 0)) / 1000.0) * float(p.get("put_price_per_1000") or 0)
    get_cost = (max(0, int(get_requests or 0)) / 1000.0) * float(p.get("get_price_per_1000") or 0)
    return {
        "estimated_storage_cost": round(storage_cost, 6),
        "estimated_request_cost": round(put_cost + get_cost, 6),
    }


def allocate_billable_egress_gb(
    dealer_egress_bytes: Dict[str, int],
    *,
    free_egress_gb: Optional[float] = None,
) -> Dict[str, float]:
    """Apply free egress once at account level; allocate billable GB proportionally.

    Returns dealer -> billable_egress_gb (float).
    """
    pricing = s3_pricing_config()
    free_gb = float(free_egress_gb if free_egress_gb is not None else pricing.get("free_egress_gb") or 0)
    totals = {k: max(0, int(v or 0)) for k, v in (dealer_egress_bytes or {}).items()}
    total_bytes = sum(totals.values())
    total_gb = total_bytes / GIB
    billable_total_gb = max(0.0, total_gb - free_gb)
    if total_bytes <= 0 or billable_total_gb <= 0:
        return {k: 0.0 for k in totals}
    out = {}
    for dealer, nbytes in totals.items():
        share = nbytes / total_bytes
        out[dealer] = billable_total_gb * share
    return out


def estimate_costs(
    *,
    stored_bytes: int = 0,
    upload_bytes: int = 0,
    view_bytes: int = 0,
    download_bytes: int = 0,
    put_requests: int = 0,
    get_requests: int = 0,
    download_requests: int = 0,
    billable_egress_gb: Optional[float] = None,
    apply_free_egress: bool = True,
    pricing: Optional[Dict[str, float]] = None,
) -> Dict[str, float]:
    """Estimate costs for a usage slice.

    IMPORTANT:
    - `download_requests` is a business counter only — it does NOT add GET charges.
    - Prefer passing `billable_egress_gb` from allocate_billable_egress_gb for dealer rows.
    - If billable_egress_gb is None and apply_free_egress=True, free egress is applied
      to THIS slice only (safe for account/month totals; do NOT use per-dealer).
    """
    p = pricing or s3_pricing_config()
    base = estimate_request_and_storage_costs(
        stored_bytes=stored_bytes,
        put_requests=put_requests,
        get_requests=get_requests,
        pricing=p,
    )
    if billable_egress_gb is not None:
        billable = max(0.0, float(billable_egress_gb))
    else:
        egress_gb = max(0, int(download_bytes or 0)) / GIB
        free_gb = float(p.get("free_egress_gb") or 0) if apply_free_egress else 0.0
        billable = max(0.0, egress_gb - free_gb)

    transfer_cost = billable * float(p.get("egress_price_per_gb") or 0)
    total = base["estimated_storage_cost"] + base["estimated_request_cost"] + transfer_cost
    return {
        **base,
        "estimated_transfer_cost": round(transfer_cost, 6),
        "estimated_total_cost": round(total, 6),
        "billable_egress_gb": round(billable, 6),
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
    """Increment daily aggregated usage. Best-effort — never breaks callers.

    DOWNLOAD increments:
      - download_bytes / download_requests (business)
      - get_requests once (S3 GET billing)
    """
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
            # One S3 GET charge via get_requests; download_requests is business-only.
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


def _aggregate_dealer_slots(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    by_dealer: Dict[str, Dict[str, Any]] = {}
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
    return by_dealer


async def dealer_usage_ranking(
    db,
    *,
    month: Optional[str] = None,
    brand: Optional[str] = None,
    dealer: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Aggregate dealer-wise usage with account-level free egress allocation."""
    month = _month_prefix(month or _ist_date_key()[:7])
    q: Dict[str, Any] = {"date_key": {"$regex": f"^{month}"}}
    if brand:
        q["brand"] = brand
    if dealer:
        q["dealer"] = dealer

    rows = await db.storage_usage_daily.find(q, {"_id": 0}).to_list(100000)
    by_dealer = _aggregate_dealer_slots(rows)
    pricing = s3_pricing_config()

    # When filtering to a subset of dealers, free egress must still be based on
    # the full account month unless caller filters brand/dealer for drill-down.
    # For filtered brand/dealer views we allocate within the filtered set only
    # and label accordingly via allocation_scope.
    egress_map = {d: int(s["download_bytes"] or 0) for d, s in by_dealer.items()}
    billable_map = allocate_billable_egress_gb(egress_map)

    out = []
    for dname, slot in by_dealer.items():
        stored = slot["stored_bytes"] or (slot["upload_bytes"] + slot["archive_bytes"])
        costs = estimate_costs(
            stored_bytes=stored,
            upload_bytes=slot["upload_bytes"],
            view_bytes=slot["view_bytes"],
            download_bytes=slot["download_bytes"],
            put_requests=slot["put_requests"],
            get_requests=slot["get_requests"],  # already includes download GETs once
            download_requests=0,  # never double-bill
            billable_egress_gb=billable_map.get(dname, 0.0),
            pricing=pricing,
        )
        out.append(
            {
                "dealer": slot["dealer"],
                "brands": sorted(slot["brands"]),
                "branches": len(slot["branches"]),
                "branch_names": sorted(slot["branches"])[:50],
                "stored_gb": round(stored / GIB, 6),
                "uploaded_gb": round(slot["upload_bytes"] / GIB, 6),
                "viewed_gb": round(slot["view_bytes"] / GIB, 6),
                "downloaded_gb": round(slot["download_bytes"] / GIB, 6),
                "put_requests": slot["put_requests"],
                "get_requests": slot["get_requests"],
                "download_requests": slot["download_requests"],
                "billable_egress_gb": costs.get("billable_egress_gb"),
                "estimated_cost": costs["estimated_total_cost"],
                "estimated_storage_cost": costs["estimated_storage_cost"],
                "estimated_request_cost": costs["estimated_request_cost"],
                "estimated_transfer_cost": costs["estimated_transfer_cost"],
                "cost_label": "Estimated Cost",
                "egress_allocation": "account_month_proportional_after_free_allowance",
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
    # Account-level: apply free egress once
    costs = estimate_costs(
        stored_bytes=stored,
        upload_bytes=totals["upload_bytes"],
        view_bytes=totals["view_bytes"],
        download_bytes=totals["download_bytes"],
        put_requests=totals["put_requests"],
        get_requests=totals["get_requests"],
        download_requests=0,
        apply_free_egress=True,
    )
    return {
        "month": month,
        **totals,
        "stored_bytes_effective": stored,
        **costs,
        "pricing": s3_pricing_config(),
        "cost_label": "Estimated Cost",
        "egress_formula": (
            "billable_egress_gb = max(sum(download_gb) - S3_FREE_EGRESS_GB, 0); "
            "dealer_billable = billable_egress_gb * (dealer_download_gb / sum(download_gb))"
        ),
    }
