"""Persistent request ringing cycles for mobile Pick/Reject (Testing + Production).

Ring TTL and confirm-window state live in Mongo so a process restart cannot
leave dealers ringing forever or skip renotify / max-cycle escalation.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set

from mobile_request_claim import CONFIRM_WINDOW_SECONDS, dismissed_dealer_keys, now_iso, now_utc, parse_iso

logger = logging.getLogger("nmts.mobile_request_cycle")

RING_TTL_SECONDS = 50
MAX_CYCLES = 3
COLLECTION = "mobile_request_cycles"

STATUS_RINGING = "ringing"
STATUS_PICKED = "picked"
STATUS_EXPIRED = "expired"
STATUS_EXHAUSTED = "exhausted"
STATUS_ALL_REJECTED = "all_rejected"
STATUS_COMPLETED = "completed"

ACTIVE_STATUSES = {STATUS_RINGING, STATUS_PICKED}


def ring_deadline_iso(from_dt: Optional[datetime] = None) -> str:
    base = from_dt or now_utc()
    return (base + timedelta(seconds=RING_TTL_SECONDS)).isoformat()


def _dealer_key(name: str) -> str:
    return (name or "").strip().casefold()


def group_snapshot_from_doc(group_doc: Optional[dict]) -> Dict[str, Any]:
    doc = group_doc or {}
    return {
        "id": doc.get("id") or doc.get("request_group_key") or "",
        "request_number": doc.get("request_number") or "",
        "attempt_id": doc.get("attempt_id") or "",
        "supplying_brand": doc.get("supplying_brand") or doc.get("requesting_brand") or doc.get("brand_name") or "",
        "supplying_dealer": doc.get("supplying_dealer") or doc.get("dealer_name") or "",
        "supplying_branch": doc.get("supplying_branch") or doc.get("branch") or "",
        "requesting_brand": doc.get("requesting_brand") or doc.get("supplying_brand") or doc.get("brand_name") or "",
        "requesting_dealer": doc.get("requesting_dealer") or "",
        "requesting_branch": doc.get("requesting_branch") or "",
        "total_items": doc.get("total_items"),
        "total_qty": doc.get("total_qty") or doc.get("total_quantity"),
        "total_quantity": doc.get("total_quantity") or doc.get("total_qty"),
        "items": doc.get("items") or [],
    }


async def get_cycle(db, request_group_key: str) -> Optional[dict]:
    if not request_group_key:
        return None
    return await db.mobile_request_cycles.find_one(
        {"request_group_key": request_group_key},
        {"_id": 0},
    )


async def excluded_dealer_keys(db, request_group_key: str) -> Set[str]:
    """Union of cycle exclusions + Reject action dealers."""
    keys: Set[str] = set()
    cycle = await get_cycle(db, request_group_key)
    if cycle:
        for field in ("excluded_dealers", "timed_out_dealers", "rejected_dealers"):
            for name in cycle.get(field) or []:
                key = _dealer_key(name)
                if key:
                    keys.add(key)
    try:
        keys |= await dismissed_dealer_keys(db, request_group_key)
    except Exception:
        pass
    return keys


async def start_or_reset_cycle(db, group_doc: dict, *, cycle_number: int = 1) -> dict:
    """Create cycle 1 (or overwrite) with a fresh ring deadline."""
    key = (group_doc or {}).get("id") or (group_doc or {}).get("request_group_key") or ""
    if not key:
        raise ValueError("missing_request_group_key")
    now = now_iso()
    existing = await get_cycle(db, key)
    doc = {
        "request_group_key": key,
        "cycle_number": int(cycle_number),
        "ring_deadline_at": ring_deadline_iso(),
        "status": STATUS_RINGING,
        "excluded_dealers": list((existing or {}).get("excluded_dealers") or []),
        "timed_out_dealers": list((existing or {}).get("timed_out_dealers") or []),
        "rejected_dealers": list((existing or {}).get("rejected_dealers") or []),
        "group_snapshot": group_snapshot_from_doc(group_doc),
        "created_at": (existing or {}).get("created_at") or now,
        "updated_at": now,
    }
    await db.mobile_request_cycles.update_one(
        {"request_group_key": key},
        {"$set": doc},
        upsert=True,
    )
    return doc


async def mark_cycle_picked(db, request_group_key: str, *, dealer_name: str = "") -> Optional[dict]:
    if not request_group_key:
        return None
    await db.mobile_request_cycles.update_one(
        {"request_group_key": request_group_key},
        {
            "$set": {
                "status": STATUS_PICKED,
                "picked_dealer": dealer_name or "",
                "updated_at": now_iso(),
            }
        },
    )
    return await get_cycle(db, request_group_key)


async def mark_cycle_completed(db, request_group_key: str) -> None:
    if not request_group_key:
        return
    await db.mobile_request_cycles.update_one(
        {"request_group_key": request_group_key},
        {"$set": {"status": STATUS_COMPLETED, "updated_at": now_iso()}},
    )


async def add_rejected_dealer(db, request_group_key: str, dealer_name: str) -> Optional[dict]:
    key = _dealer_key(dealer_name)
    if not request_group_key or not key:
        return await get_cycle(db, request_group_key)
    cycle = await get_cycle(db, request_group_key)
    if not cycle:
        return None
    rejected = list(cycle.get("rejected_dealers") or [])
    excluded = list(cycle.get("excluded_dealers") or [])
    # Store original casing if available; compare casefold.
    if not any(_dealer_key(x) == key for x in rejected):
        rejected.append(dealer_name.strip())
    if not any(_dealer_key(x) == key for x in excluded):
        excluded.append(dealer_name.strip())
    await db.mobile_request_cycles.update_one(
        {"request_group_key": request_group_key},
        {
            "$set": {
                "rejected_dealers": rejected,
                "excluded_dealers": excluded,
                "updated_at": now_iso(),
            }
        },
    )
    cycle["rejected_dealers"] = rejected
    cycle["excluded_dealers"] = excluded
    return cycle


async def add_timed_out_dealer(db, request_group_key: str, dealer_name: str) -> Optional[dict]:
    key = _dealer_key(dealer_name)
    if not request_group_key or not key:
        return await get_cycle(db, request_group_key)
    cycle = await get_cycle(db, request_group_key)
    if not cycle:
        return None
    timed = list(cycle.get("timed_out_dealers") or [])
    excluded = list(cycle.get("excluded_dealers") or [])
    if not any(_dealer_key(x) == key for x in timed):
        timed.append(dealer_name.strip())
    if not any(_dealer_key(x) == key for x in excluded):
        excluded.append(dealer_name.strip())
    await db.mobile_request_cycles.update_one(
        {"request_group_key": request_group_key},
        {
            "$set": {
                "timed_out_dealers": timed,
                "excluded_dealers": excluded,
                "updated_at": now_iso(),
            }
        },
    )
    cycle["timed_out_dealers"] = timed
    cycle["excluded_dealers"] = excluded
    return cycle


async def advance_cycle_after_miss(db, cycle: dict) -> Dict[str, Any]:
    """Ring TTL or confirm miss: start next cycle or mark exhausted.

    Returns {action: renotify|exhausted, cycle: doc}.
    """
    key = (cycle or {}).get("request_group_key") or ""
    number = int((cycle or {}).get("cycle_number") or 1)
    snapshot = (cycle or {}).get("group_snapshot") or {"id": key, "request_group_key": key}
    if number >= MAX_CYCLES:
        await db.mobile_request_cycles.update_one(
            {"request_group_key": key},
            {
                "$set": {
                    "status": STATUS_EXHAUSTED,
                    "updated_at": now_iso(),
                }
            },
        )
        refreshed = await get_cycle(db, key)
        return {"action": "exhausted", "cycle": refreshed}
    next_doc = await start_or_reset_cycle(db, {**snapshot, "id": key}, cycle_number=number + 1)
    return {"action": "renotify", "cycle": next_doc}


async def mark_cycle_all_rejected(db, request_group_key: str) -> Optional[dict]:
    if not request_group_key:
        return None
    await db.mobile_request_cycles.update_one(
        {"request_group_key": request_group_key},
        {"$set": {"status": STATUS_ALL_REJECTED, "updated_at": now_iso()}},
        upsert=False,
    )
    return await get_cycle(db, request_group_key)


async def list_due_ring_cycles(db, *, at: Optional[datetime] = None) -> List[dict]:
    """Cycles still ringing whose ring TTL has elapsed (no Pick yet)."""
    now = at or now_utc()
    rows = await db.mobile_request_cycles.find(
        {"status": STATUS_RINGING},
        {"_id": 0},
    ).to_list(2000)
    due = []
    for row in rows:
        deadline = parse_iso(row.get("ring_deadline_at"))
        if deadline and deadline <= now:
            due.append(row)
    return due


async def ensure_cycle_indexes(db) -> None:
    try:
        await db.mobile_request_cycles.create_index(
            [("request_group_key", 1)],
            unique=True,
            name="uq_mobile_request_cycle_key",
        )
        await db.mobile_request_cycles.create_index([("status", 1), ("ring_deadline_at", 1)])
    except Exception as exc:
        logger.warning("mobile_request_cycles index creation failed: %s", exc)


# Re-export confirm window for callers that import cycle constants together.
__all__ = [
    "RING_TTL_SECONDS",
    "CONFIRM_WINDOW_SECONDS",
    "MAX_CYCLES",
    "STATUS_RINGING",
    "STATUS_PICKED",
    "STATUS_EXPIRED",
    "STATUS_EXHAUSTED",
    "STATUS_ALL_REJECTED",
    "STATUS_COMPLETED",
    "start_or_reset_cycle",
    "get_cycle",
    "excluded_dealer_keys",
    "mark_cycle_picked",
    "mark_cycle_completed",
    "add_rejected_dealer",
    "add_timed_out_dealer",
    "advance_cycle_after_miss",
    "mark_cycle_all_rejected",
    "list_due_ring_cycles",
    "ensure_cycle_indexes",
    "group_snapshot_from_doc",
    "ring_deadline_iso",
]
