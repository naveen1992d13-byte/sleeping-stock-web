"""Persistent poller for mobile request ring TTL + confirm-window misses.

Reads deadlines from Mongo so restarts resume correctly. Safe in testing and
production — never sends via the generic Expo path under PUSH_TEST_MODE; request
lifecycle pushes go through the approved chokepoint in mobile_push.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

logger = logging.getLogger("nmts.mobile_request_cycle_scheduler")

_scheduler_task: Optional[asyncio.Task] = None
POLL_SECONDS = 8


async def _load_group_doc(db, cycle: dict) -> dict:
    snapshot = dict(cycle.get("group_snapshot") or {})
    key = cycle.get("request_group_key") or snapshot.get("id") or ""
    snapshot.setdefault("id", key)
    snapshot.setdefault("request_group_key", key)
    header = None
    try:
        if snapshot.get("request_number"):
            header = await db.request_headers.find_one(
                {"request_number": snapshot["request_number"]},
                {"_id": 0},
            )
        if not header and key:
            header = await db.request_headers.find_one({"id": key}, {"_id": 0})
    except Exception:
        header = None
    if header:
        merged = {**header, **snapshot}
        merged["id"] = key or header.get("id") or snapshot.get("id")
        return merged
    return snapshot


async def _active_lock(db, request_group_key: str):
    if not request_group_key:
        return None
    return await db.mobile_request_group_locks.find_one(
        {"request_group_key": request_group_key},
        {"_id": 0},
    )


async def process_ring_ttl_expiry(db, cycle: dict) -> dict:
    """Ring TTL elapsed with no Pick — renotify remaining or exhaust."""
    from mobile_request_claim import now_iso
    import mobile_push
    import mobile_request_cycle as cycles

    key = cycle.get("request_group_key") or ""
    lock = await _active_lock(db, key)
    if lock and str(lock.get("lock_status") or "").lower() == "picked":
        await cycles.mark_cycle_picked(db, key, dealer_name=lock.get("dealer_name") or "")
        return {"action": "picked", "request_group_key": key}

    await db.mobile_request_cycles.update_one(
        {"request_group_key": key, "status": cycles.STATUS_RINGING},
        {"$set": {"status": cycles.STATUS_EXPIRED, "updated_at": now_iso()}},
    )
    # advance_cycle_after_miss reads cycle_number / exclusions from the prior doc.
    prior = await cycles.get_cycle(db, key) or cycle
    prior["cycle_number"] = int(prior.get("cycle_number") or cycle.get("cycle_number") or 1)
    result = await cycles.advance_cycle_after_miss(db, prior)
    group_doc = await _load_group_doc(db, result.get("cycle") or prior)
    if result.get("action") == "renotify":
        await mobile_push.renotify_after_confirm_timeout(db, group_doc)
        return {"action": "renotify", "request_group_key": key, "cycle": result.get("cycle")}
    await mobile_push.notify_requester_request_outcome_push(
        db,
        group_doc,
        outcome="max_cycles_exhausted",
        detail=f"No dealer confirmed within {cycles.MAX_CYCLES} ring cycles",
    )
    return {"action": "exhausted", "request_group_key": key}


async def process_confirm_timeout(db, lock: dict) -> dict:
    """Confirm window miss — release claim, exclude dealer, renotify."""
    from mobile_request_claim import is_confirm_expired, release_expired_claim
    import mobile_push
    import mobile_request_cycle as cycles

    if not is_confirm_expired(lock):
        return {"action": "skip"}
    released = await release_expired_claim(db, lock)
    if not released:
        return {"action": "skip"}
    key = released.get("request_group_key") or ""
    dealer = released.get("dealer_name") or ""
    await cycles.add_timed_out_dealer(db, key, dealer)
    cycle = await cycles.get_cycle(db, key)
    if not cycle:
        # Create a cycle shell so renotify / max-cycle tracking still works.
        group_doc = {
            "id": key,
            "request_number": key,
            "supplying_brand": released.get("brand_name"),
            "supplying_dealer": dealer,
            "supplying_branch": released.get("branch"),
        }
        cycle = await cycles.start_or_reset_cycle(db, group_doc, cycle_number=1)
        await cycles.add_timed_out_dealer(db, key, dealer)
        cycle = await cycles.get_cycle(db, key)
    result = await cycles.advance_cycle_after_miss(db, cycle or {"request_group_key": key, "cycle_number": 1})
    group_doc = await _load_group_doc(db, result.get("cycle") or cycle or {"request_group_key": key})
    if result.get("action") == "renotify":
        await mobile_push.renotify_after_confirm_timeout(
            db, group_doc, exclude_device_id=released.get("device_id") or "",
        )
        return {"action": "renotify", "request_group_key": key, "excluded_dealer": dealer}
    await mobile_push.notify_requester_request_outcome_push(
        db,
        group_doc,
        outcome="max_cycles_exhausted",
        detail=f"No dealer confirmed within {cycles.MAX_CYCLES} ring cycles",
    )
    return {"action": "exhausted", "request_group_key": key, "excluded_dealer": dealer}


async def process_all_rejected_if_needed(db, request_group_key: str, group_doc: dict, *, brand: str) -> Optional[dict]:
    """If every currently eligible dealer has rejected, notify requester."""
    import mobile_push
    import mobile_request_cycle as cycles

    if not request_group_key or not brand:
        return None
    excluded = await cycles.excluded_dealer_keys(db, request_group_key)
    devices = await db.mobile_devices.find(
        {
            "status": "active",
            "session_active": True,
            "push_enabled": True,
            "push_token": {"$exists": True, "$ne": ""},
        },
        {"_id": 0, "dealer_name": 1, "branch": 1, "brand_name": 1, "data_origin": 1, "status": 1, "session_active": 1, "push_enabled": 1, "push_token": 1},
    ).to_list(500)

    def _same(a, b):
        return (a or "").strip().casefold() == (b or "").strip().casefold()

    req_dealer = (group_doc.get("requesting_dealer") or "").strip().casefold()
    req_branch = (group_doc.get("requesting_branch") or "").strip().casefold()
    remaining = []
    for dev in devices:
        if not _same(dev.get("brand_name"), brand):
            continue
        dealer = (dev.get("dealer_name") or "").strip().casefold()
        branch = (dev.get("branch") or "").strip().casefold()
        if req_dealer and req_branch and dealer == req_dealer and branch == req_branch:
            continue
        if dealer in excluded:
            continue
        remaining.append(dev)
    if remaining:
        return None
    await cycles.mark_cycle_all_rejected(db, request_group_key)
    await mobile_push.notify_requester_request_outcome_push(
        db,
        group_doc,
        outcome="all_rejected",
        detail="All eligible dealers rejected this request",
    )
    return {"action": "all_rejected", "request_group_key": request_group_key}


async def run_cycle_scheduler_once(db) -> dict:
    """One poll tick — used by the loop and by tests (restart-safe via DB)."""
    from mobile_request_claim import is_confirm_expired
    import mobile_request_cycle as cycles

    ring_due = 0
    confirm_due = 0
    results = []

    for cycle in await cycles.list_due_ring_cycles(db):
        try:
            out = await process_ring_ttl_expiry(db, cycle)
            results.append(out)
            ring_due += 1
        except Exception as exc:
            logger.warning("ring TTL processing failed for %s: %s", cycle.get("request_group_key"), exc)

    try:
        locks = await db.mobile_request_group_locks.find(
            {"lock_status": "picked", "confirm_deadline_at": {"$exists": True}},
            {"_id": 0},
        ).to_list(2000)
    except Exception:
        locks = []
    for lock in locks:
        try:
            if not is_confirm_expired(lock):
                continue
            out = await process_confirm_timeout(db, lock)
            if out.get("action") != "skip":
                results.append(out)
                confirm_due += 1
        except Exception as exc:
            logger.warning("confirm timeout processing failed for %s: %s", lock.get("request_group_key"), exc)

    return {"ring_due": ring_due, "confirm_due": confirm_due, "results": results}


async def _scheduler_loop(db):
    logger.info("Mobile request cycle scheduler started (poll=%ss)", POLL_SECONDS)
    while True:
        try:
            result = await run_cycle_scheduler_once(db)
            if result.get("ring_due") or result.get("confirm_due"):
                logger.info("Mobile request cycle tick: %s", {k: result[k] for k in ("ring_due", "confirm_due")})
        except asyncio.CancelledError:
            logger.info("Mobile request cycle scheduler cancelled")
            raise
        except Exception as exc:
            logger.error("Mobile request cycle scheduler loop error: %s", exc)
        await asyncio.sleep(POLL_SECONDS)


def start_request_cycle_scheduler(db) -> None:
    global _scheduler_task
    if _scheduler_task and not _scheduler_task.done():
        return
    _scheduler_task = asyncio.create_task(_scheduler_loop(db))


async def stop_request_cycle_scheduler() -> None:
    global _scheduler_task
    if _scheduler_task and not _scheduler_task.done():
        _scheduler_task.cancel()
        try:
            await _scheduler_task
        except asyncio.CancelledError:
            pass
    _scheduler_task = None
