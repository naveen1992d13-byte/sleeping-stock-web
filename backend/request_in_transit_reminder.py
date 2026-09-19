"""In-transit (Dispatched) reminder flags — 24h / 48h / 72h.

Modeled on request_sla_scheduler.py. Sets highlight markers only.
Never auto-transitions status / fulfillment_stage and never deletes data.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import request_fulfillment as rf

logger = logging.getLogger("nmts.request_in_transit")

_scheduler_task: Optional[asyncio.Task] = None
POLL_SECONDS = 60


def due_in_transit_levels(header: dict, now: datetime = None) -> list:
    now = now or datetime.now(timezone.utc)
    if str(header.get("status") or "") != "Dispatched":
        return []
    dispatched_at = rf.parse_iso(header.get("dispatched_at") or header.get("dispatch_at"))
    if not dispatched_at:
        return []
    hours = (now - dispatched_at).total_seconds() / 3600.0
    flags = header.get("in_transit_flags") or {}
    due = []
    for level, threshold in (("24h", 24), ("48h", 48), ("72h", 72)):
        if flags.get(level):
            continue
        if hours >= threshold:
            due.append(level)
    return due


async def run_in_transit_cycle(db) -> dict:
    now = datetime.now(timezone.utc)
    flagged = 0
    headers = await db.request_headers.find(
        {"status": "Dispatched"},
        {"_id": 0},
    ).to_list(4000)
    for header in headers:
        try:
            due = due_in_transit_levels(header, now)
            if not due:
                continue
            sets = {f"in_transit_flags.{level}": now.isoformat() for level in due}
            sets["in_transit_level"] = due[-1]
            sets["updated_at"] = now.isoformat()
            await db.request_headers.update_one(
                {"id": header.get("id")},
                {"$set": sets},
            )
            await db.order_requests.update_many(
                {"request_number": header.get("request_number"), "status": "Dispatched"},
                {"$set": sets},
            )
            flagged += 1
        except Exception as exc:
            logger.warning(
                "in-transit flag failed for %s: %s",
                header.get("request_number"),
                str(exc)[:300],
            )
    return {"scanned": len(headers), "flagged": flagged}


async def _scheduler_loop(db):
    logger.info("Request in-transit reminder scheduler started")
    while True:
        try:
            result = await run_in_transit_cycle(db)
            if result.get("flagged"):
                logger.info("In-transit reminder cycle: %s", result)
        except asyncio.CancelledError:
            logger.info("Request in-transit reminder scheduler cancelled")
            raise
        except Exception as exc:
            logger.error("Request in-transit reminder loop error: %s", exc)
        await asyncio.sleep(POLL_SECONDS)


def start_in_transit_reminder_scheduler(db) -> None:
    global _scheduler_task
    if _scheduler_task and not _scheduler_task.done():
        return
    _scheduler_task = asyncio.create_task(_scheduler_loop(db))


async def stop_in_transit_reminder_scheduler() -> None:
    global _scheduler_task
    if _scheduler_task and not _scheduler_task.done():
        _scheduler_task.cancel()
        try:
            await _scheduler_task
        except asyncio.CancelledError:
            pass
    _scheduler_task = None
