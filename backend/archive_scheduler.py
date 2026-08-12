"""Timezone-aware archive scheduler — one coordinated daily batch at 23:45 IST.

Daily coordinated archive (23:45 Asia/Kolkata) for the previous calendar day:
  - uploads dump
  - product-history dump
  - orders terminal dump (by completion date)
  - requests terminal dump (by completion date)

Monthly completed Orders/Requests retained as a safety net on the 1st at 01:30 IST.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

import archive_manifest as am
import history_archive as ha
from s3_storage import archive_scheduler_enabled, verification_mongo_hot_days

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

_scheduler_task: Optional[asyncio.Task] = None
_OWNER = f"archive-scheduler-{uuid.uuid4().hex[:8]}"

REQUIRED_DAILY_DATASETS = ("uploads", "product-history", "orders", "requests")


def _ist_now() -> datetime:
    return datetime.now(IST)


def previous_calendar_day_iso(now: Optional[datetime] = None) -> str:
    now = now or _ist_now()
    return (now.date() - timedelta(days=1)).isoformat()


def _dataset_result_ok(result: Optional[Dict[str, Any]]) -> bool:
    """Acceptable terminal daily results: VERIFIED / already verified / genuine NO ELIGIBLE."""
    if not isinstance(result, dict):
        return False
    status = str(result.get("status") or "").lower()
    if status in {"verified", "already_verified", "no_eligible"}:
        return True
    manifest = result.get("manifest") or {}
    return am.is_acceptable_daily_result(manifest.get("status"))


def evaluate_daily_cycle(results: Dict[str, Any]) -> Dict[str, Any]:
    """Daily cycle is COMPLETE only when every required dataset is acceptable."""
    datasets = results.get("datasets") or {}
    missing = [name for name in REQUIRED_DAILY_DATASETS if name not in datasets]
    failed = [
        name
        for name in REQUIRED_DAILY_DATASETS
        if name in datasets and not _dataset_result_ok(datasets.get(name))
    ]
    complete = not missing and not failed
    return {
        "complete": complete,
        "status": "COMPLETE" if complete else "INCOMPLETE / FAILED",
        "failed_datasets": failed,
        "missing_datasets": missing,
    }


async def run_daily_coordinated_archive(db, archive_date: Optional[str] = None) -> dict:
    """One coordinated daily batch for all archive datasets (previous IST day).

    Idempotent per dataset: already VERIFIED / NO_ELIGIBLE rows are not re-uploaded.
    Cycle status is COMPLETE only when uploads, product-history, orders, and requests
    are each VERIFIED (or genuine NO ELIGIBLE). Partial failure does not stamp success.
    """
    now = _ist_now()
    if not archive_date:
        archive_date = previous_calendar_day_iso(now)
    lock_key = f"daily-coordinated:{archive_date}"
    if not await am.acquire_job_lock(db, lock_key, _OWNER, ttl_seconds=10800):
        return {"status": "locked", "archive_date": archive_date}

    results: dict = {"archive_date": archive_date, "datasets": {}}
    try:
        # A. Uploads dump
        try:
            results["datasets"]["uploads"] = await ha.archive_uploads_for_date(db, archive_date)
        except Exception as exc:
            logger.error("Daily uploads archive failed for %s: %s", archive_date, exc)
            results["datasets"]["uploads"] = {"status": "error", "error": str(exc)[:500]}

        # B. Product History dump
        try:
            ph = await ha.archive_product_history_for_date(db, archive_date)
            results["datasets"]["product-history"] = ph
            # Side-job: verifications outside hot window
            try:
                hot = verification_mongo_hot_days()
                cutoff = (now.date() - timedelta(days=hot)).isoformat()
                if archive_date <= cutoff:
                    results["datasets"]["verifications"] = await ha.archive_verifications_for_date(
                        db, archive_date
                    )
            except Exception as exc:
                logger.warning("Verification archive side-job failed: %s", exc)

            # Optional prune only when REAL S3 verified
            prune_result = None
            try:
                if ph.get("status") in {"verified", "already_verified"}:
                    prune_result = await ha.prune_product_history_date(db, archive_date)
            except Exception as exc:
                logger.warning("Post-archive prune skipped/failed for %s: %s", archive_date, exc)
                prune_result = {"status": "error", "error": str(exc)[:500]}
            if isinstance(ph, dict):
                ph = {**ph, "prune": prune_result}
                results["datasets"]["product-history"] = ph
        except Exception as exc:
            logger.error("Daily product-history archive failed for %s: %s", archive_date, exc)
            results["datasets"]["product-history"] = {"status": "error", "error": str(exc)[:500]}

        # C. Orders dump (terminal on that date)
        try:
            results["datasets"]["orders"] = await ha.archive_orders_for_date(db, archive_date)
        except Exception as exc:
            logger.error("Daily orders archive failed for %s: %s", archive_date, exc)
            results["datasets"]["orders"] = {"status": "error", "error": str(exc)[:500]}

        # D. Requests dump (terminal on that date)
        try:
            results["datasets"]["requests"] = await ha.archive_requests_for_date(db, archive_date)
        except Exception as exc:
            logger.error("Daily requests archive failed for %s: %s", archive_date, exc)
            results["datasets"]["requests"] = {"status": "error", "error": str(exc)[:500]}

        cycle = evaluate_daily_cycle(results)
        results["cycle"] = cycle
        results["status"] = "ok" if cycle["complete"] else "incomplete"
        return results
    finally:
        await am.release_job_lock(db, lock_key, _OWNER)


# Backward-compatible alias used by older callers / tests
async def run_daily_product_archive(db, archive_date: Optional[str] = None) -> dict:
    return await run_daily_coordinated_archive(db, archive_date)


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
            daily_stamp = previous_calendar_day_iso(now)
            monthly_stamp = now.strftime("%Y-%m")

            # Daily coordinated batch at 23:45 IST — archives the previous calendar day.
            # Window: 23:45–23:59 so a single stamp is used per day only after COMPLETE success.
            if now.hour == 23 and now.minute >= 45 and last_daily != daily_stamp:
                modules = [
                    getattr(ha, "MODULE_UPLOADS", "uploads"),
                    ha.MODULE_PRODUCT_HISTORY,
                    ha.MODULE_ORDERS,
                    ha.MODULE_REQUESTS,
                ]
                acceptable = 0
                for mod in modules:
                    row = await am.find_acceptable_daily(db, mod, daily_stamp)
                    if row:
                        acceptable += 1
                if acceptable >= 4:
                    logger.info(
                        "Daily coordinated archive for %s already complete (%s/4) — skipping",
                        daily_stamp,
                        acceptable,
                    )
                    last_daily = daily_stamp
                else:
                    logger.info("Triggering daily coordinated archive for previous day %s", daily_stamp)
                    try:
                        outcome = await run_daily_coordinated_archive(db, daily_stamp)
                        cycle = outcome.get("cycle") or evaluate_daily_cycle(outcome)
                        if cycle.get("complete"):
                            last_daily = daily_stamp
                            logger.info("Daily coordinated archive COMPLETE for %s", daily_stamp)
                        else:
                            # Do NOT stamp last_daily — allow same-night retry
                            logger.error(
                                "Daily coordinated archive INCOMPLETE for %s — failed=%s missing=%s",
                                daily_stamp,
                                cycle.get("failed_datasets"),
                                cycle.get("missing_datasets"),
                            )
                    except Exception as exc:
                        logger.error("Daily coordinated archive failed: %s", exc)

            # Monthly safety-net on 1st at 01:30 IST (previous calendar month)
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
