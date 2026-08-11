"""Timezone-aware archive scheduler with MongoDB idempotency locks.

Daily Product History archive: 23:45 Asia/Kolkata
Monthly completed Orders/Requests: 1st day 01:30 Asia/Kolkata
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

import archive_manifest as am
import history_archive as ha
from s3_storage import archive_scheduler_enabled, verification_mongo_hot_days

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

_scheduler_task: Optional[asyncio.Task] = None
_OWNER = f"archive-scheduler-{uuid.uuid4().hex[:8]}"


def _ist_now() -> datetime:
    return datetime.now(IST)


async def run_daily_product_archive(db, archive_date: Optional[str] = None) -> dict:
    """Archive yesterday (or explicit date) product history + analytics snapshots."""
    now = _ist_now()
    if not archive_date:
        # At 23:45 we archive the *completed* day — which is "today" once the day
        # is essentially finished. Spec: archive the completed day's Product History
        # every day at 23:45. Use today's IST date.
        archive_date = now.date().isoformat()
    lock_key = f"daily-product-history:{archive_date}"
    if not await am.acquire_job_lock(db, lock_key, _OWNER, ttl_seconds=7200):
        return {"status": "locked", "archive_date": archive_date}
    try:
        result = await ha.archive_product_history_for_date(db, archive_date)
        # Also archive verifications older than hot window for that day if beyond retention
        try:
            hot = verification_mongo_hot_days()
            cutoff = (now.date() - timedelta(days=hot)).isoformat()
            if archive_date <= cutoff:
                await ha.archive_verifications_for_date(db, archive_date)
        except Exception as exc:
            logger.warning("Verification archive side-job failed: %s", exc)
        return result
    finally:
        await am.release_job_lock(db, lock_key, _OWNER)


async def run_monthly_completed_archives(db, archive_month: Optional[str] = None) -> dict:
    if not archive_month:
        archive_month, _, _ = ha._prev_calendar_month()
    lock_key = f"monthly-completed:{archive_month}"
    if not await am.acquire_job_lock(db, lock_key, _OWNER, ttl_seconds=10800):
        return {"status": "locked", "archive_month": archive_month}
    try:
        orders = await ha.archive_completed_orders_month(db, archive_month)
        requests = await ha.archive_completed_requests_month(db, archive_month)
        return {"status": "ok", "archive_month": archive_month, "orders": orders, "requests": requests}
    finally:
        await am.release_job_lock(db, lock_key, _OWNER)


async def _scheduler_loop(db) -> None:
    logger.info("Archive scheduler started (owner=%s)", _OWNER)
    last_daily = None
    last_monthly = None
    while True:
        try:
            if not archive_scheduler_enabled():
                await asyncio.sleep(30)
                continue
            now = _ist_now()
            daily_stamp = now.strftime("%Y-%m-%d")
            monthly_stamp = now.strftime("%Y-%m")

            # Daily at 23:45 IST
            if now.hour == 23 and now.minute >= 45 and last_daily != daily_stamp:
                logger.info("Triggering daily product archive for %s", daily_stamp)
                try:
                    await run_daily_product_archive(db, daily_stamp)
                    last_daily = daily_stamp
                except Exception as exc:
                    logger.error("Daily archive failed: %s", exc)

            # Monthly on 1st at 01:30 IST
            if now.day == 1 and now.hour == 1 and now.minute >= 30 and last_monthly != monthly_stamp:
                logger.info("Triggering monthly completed archives for previous month")
                try:
                    await run_monthly_completed_archives(db)
                    last_monthly = monthly_stamp
                except Exception as exc:
                    logger.error("Monthly archive failed: %s", exc)
        except asyncio.CancelledError:
            logger.info("Archive scheduler cancelled")
            raise
        except Exception as exc:
            logger.error("Archive scheduler loop error: %s", exc)
        await asyncio.sleep(20)


def start_archive_scheduler(db) -> None:
    global _scheduler_task
    if not archive_scheduler_enabled():
        logger.info("Archive scheduler disabled via ARCHIVE_SCHEDULER_ENABLED")
        return
    if _scheduler_task and not _scheduler_task.done():
        return
    _scheduler_task = asyncio.create_task(_scheduler_loop(db))


async def stop_archive_scheduler() -> None:
    global _scheduler_task
    if _scheduler_task and not _scheduler_task.done():
        _scheduler_task.cancel()
        try:
            await _scheduler_task
        except asyncio.CancelledError:
            pass
    _scheduler_task = None
