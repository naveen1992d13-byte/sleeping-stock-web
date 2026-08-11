"""Timezone-aware archive scheduler with MongoDB idempotency locks.

Daily Product History archive: 00:15 Asia/Kolkata — previous calendar day
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


def previous_calendar_day_iso(now: Optional[datetime] = None) -> str:
    now = now or _ist_now()
    return (now.date() - timedelta(days=1)).isoformat()


async def run_daily_product_archive(db, archive_date: Optional[str] = None) -> dict:
    """Archive the previous IST calendar day (or an explicit date).

    After verified archive, optionally prune that historical date when
    ARCHIVE_PRUNE_ENABLED=true AND storage backend is REAL S3.
    """
    now = _ist_now()
    if not archive_date:
        archive_date = previous_calendar_day_iso(now)
    lock_key = f"daily-product-history:{archive_date}"
    if not await am.acquire_job_lock(db, lock_key, _OWNER, ttl_seconds=7200):
        return {"status": "locked", "archive_date": archive_date}
    try:
        result = await ha.archive_product_history_for_date(db, archive_date)
        # Side-job: archive verifications only when outside verification hot window
        try:
            hot = verification_mongo_hot_days()
            cutoff = (now.date() - timedelta(days=hot)).isoformat()
            if archive_date <= cutoff:
                await ha.archive_verifications_for_date(db, archive_date)
        except Exception as exc:
            logger.warning("Verification archive side-job failed: %s", exc)

        # Prune only the archived date when policy allows (real S3 + flag)
        prune_result = None
        try:
            if result.get("status") in {"verified", "already_verified"}:
                prune_result = await ha.prune_product_history_date(db, archive_date)
        except Exception as exc:
            logger.warning("Post-archive prune skipped/failed for %s: %s", archive_date, exc)
            prune_result = {"status": "error", "error": str(exc)[:500]}
        if isinstance(result, dict):
            result = {**result, "prune": prune_result}
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
            # Stamp by the *previous* day being archived so a late run still
            # only fires once per IST calendar day.
            daily_stamp = previous_calendar_day_iso(now)
            monthly_stamp = now.strftime("%Y-%m")

            # Daily at 00:15 IST — archive previous calendar day
            if now.hour == 0 and now.minute >= 15 and last_daily != daily_stamp:
                logger.info("Triggering daily product archive for previous day %s", daily_stamp)
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
