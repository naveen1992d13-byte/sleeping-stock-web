"""Timezone-aware archive scheduler — nightly same-business-day archive.

Maintenance window: 23:00–04:00 Asia/Kolkata.
At run start (23:00 IST), freeze archive_date = that IST calendar date for the
entire coordinated run, even if work continues after midnight.

Catch-up / retry of incomplete modules continues until 04:00 IST.
Monthly completed Orders/Requests safety-net remains on the 1st at 01:30 IST.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

import archive_manifest as am
import archive_runs as ar
import history_archive as ha
import maintenance as maint
from s3_storage import archive_scheduler_enabled, verification_mongo_hot_days

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

_scheduler_task: Optional[asyncio.Task] = None
_OWNER = f"archive-scheduler-{uuid.uuid4().hex[:8]}"

REQUIRED_DAILY_DATASETS = ("uploads", "product-history", "orders", "requests")

# Large nightly runs may exceed 3h — allow up to full maintenance window.
JOB_LOCK_TTL_SECONDS = 5 * 60 * 60


def _ist_now() -> datetime:
    return datetime.now(IST)


def previous_calendar_day_iso(now: Optional[datetime] = None) -> str:
    """Legacy helper retained for tests/callers that still need yesterday."""
    now = now or _ist_now()
    return (now.date() - timedelta(days=1)).isoformat()


def same_business_day_iso(now: Optional[datetime] = None) -> str:
    """IST calendar date for the nightly same-day archive."""
    return ar.ist_calendar_date_iso(now)


def in_nightly_archive_window(now: Optional[datetime] = None) -> bool:
    """23:00 inclusive through 03:59 IST (ends at 04:00)."""
    return maint.in_maintenance_window(now)


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


async def run_daily_coordinated_archive(
    db,
    archive_date: Optional[str] = None,
    *,
    run_id: Optional[str] = None,
    freeze_date: bool = True,
) -> dict:
    """Coordinated daily batch for the frozen same-business-day archive_date.

    Idempotent per dataset: already VERIFIED / NO_ELIGIBLE rows are not re-uploaded.
    Cycle status is COMPLETE only when uploads, product-history, orders, and requests
    are each VERIFIED (or genuine NO ELIGIBLE). Partial failure does not stamp success.
    """
    now = _ist_now()
    if not archive_date:
        # Default for manual/API callers: same business day (not previous day).
        archive_date = same_business_day_iso(now)

    run = None
    if freeze_date:
        run = await ar.start_or_resume_run(db, archive_date=archive_date)
        # CRITICAL: never recompute — use frozen date from ledger
        archive_date = run["archive_date"]
        run_id = run["run_id"]

    lock_key = f"daily-coordinated:{archive_date}"
    if not await am.acquire_job_lock(db, lock_key, _OWNER, ttl_seconds=JOB_LOCK_TTL_SECONDS):
        return {"status": "locked", "archive_date": archive_date, "run_id": run_id}

    results: dict = {
        "archive_date": archive_date,
        "run_id": run_id,
        "datasets": {},
    }
    try:
        async def _run_module(name: str, coro):
            try:
                outcome = await coro
                results["datasets"][name] = outcome
                ok = _dataset_result_ok(outcome)
                if run_id:
                    await ar.mark_module(
                        db,
                        run_id,
                        name,
                        status=ar.STATUS_VERIFIED if ok else ar.STATUS_FAILED,
                        result={
                            "status": (outcome or {}).get("status"),
                            "record_count": (outcome or {}).get("record_count"),
                        },
                        error=None if ok else str((outcome or {}).get("error") or (outcome or {}).get("status") or "failed")[:500],
                        increment_retry=not ok,
                    )
            except Exception as exc:
                logger.error("Daily %s archive failed for %s: %s", name, archive_date, exc)
                results["datasets"][name] = {"status": "error", "error": str(exc)[:500]}
                if run_id:
                    await ar.mark_module(
                        db,
                        run_id,
                        name,
                        status=ar.STATUS_FAILED,
                        result=None,
                        error=str(exc)[:500],
                        increment_retry=True,
                    )

        await _run_module("uploads", ha.archive_uploads_for_date(db, archive_date))

        # Product history (+ optional side jobs; prune stays gated by ARCHIVE_PRUNE_ENABLED)
        try:
            ph = await ha.archive_product_history_for_date(db, archive_date)
            results["datasets"]["product-history"] = ph
            try:
                hot = verification_mongo_hot_days()
                cutoff = (now.date() - timedelta(days=hot)).isoformat()
                if archive_date <= cutoff:
                    results["datasets"]["verifications"] = await ha.archive_verifications_for_date(
                        db, archive_date
                    )
            except Exception as exc:
                logger.warning("Verification archive side-job failed: %s", exc)

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

            ok = _dataset_result_ok(ph)
            if run_id:
                await ar.mark_module(
                    db,
                    run_id,
                    "product-history",
                    status=ar.STATUS_VERIFIED if ok else ar.STATUS_FAILED,
                    result={"status": ph.get("status"), "record_count": ph.get("record_count")},
                    error=None if ok else str(ph.get("error") or ph.get("status") or "failed")[:500],
                    increment_retry=not ok,
                )
        except Exception as exc:
            logger.error("Daily product-history archive failed for %s: %s", archive_date, exc)
            results["datasets"]["product-history"] = {"status": "error", "error": str(exc)[:500]}
            if run_id:
                await ar.mark_module(
                    db,
                    run_id,
                    "product-history",
                    status=ar.STATUS_FAILED,
                    error=str(exc)[:500],
                    increment_retry=True,
                )

        await _run_module("orders", ha.archive_orders_for_date(db, archive_date))
        await _run_module("requests", ha.archive_requests_for_date(db, archive_date))

        cycle = evaluate_daily_cycle(results)
        results["cycle"] = cycle
        results["status"] = "ok" if cycle["complete"] else "incomplete"
        if run_id:
            await ar.finalize_run(
                db,
                run_id,
                complete=bool(cycle["complete"]),
                last_error=None
                if cycle["complete"]
                else f"failed={cycle.get('failed_datasets')} missing={cycle.get('missing_datasets')}",
            )
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
    if not await am.acquire_job_lock(db, lock_key, _OWNER, ttl_seconds=JOB_LOCK_TTL_SECONDS):
        return {"status": "locked", "archive_month": archive_month}
    try:
        orders = await ha.archive_completed_orders_month(db, archive_month)
        requests = await ha.archive_completed_requests_month(db, archive_month)
        return {"status": "ok", "archive_month": archive_month, "orders": orders, "requests": requests}
    finally:
        await am.release_job_lock(db, lock_key, _OWNER)


async def _scheduler_loop(db) -> None:
    logger.info("Archive scheduler started (owner=%s) — same-day window 23:00–04:00 IST", _OWNER)
    last_monthly = None
    active_archive_date: Optional[str] = None

    while True:
        try:
            if not archive_scheduler_enabled():
                await asyncio.sleep(30)
                continue
            now = _ist_now()
            monthly_stamp = now.strftime("%Y-%m")

            # Nightly same-business-day archive + catch-up inside maintenance window.
            if in_nightly_archive_window(now):
                # Freeze date at first entry into tonight's window (23:00–23:59).
                # After midnight (00:00–03:59), continue the SAME frozen date.
                if now.hour >= 23:
                    candidate = same_business_day_iso(now)
                    if active_archive_date != candidate:
                        active_archive_date = candidate
                        logger.info(
                            "Freezing nightly archive_date=%s run start IST=%s",
                            active_archive_date,
                            now.isoformat(),
                        )
                elif not active_archive_date:
                    # Process restart after midnight: resume prior IST calendar day.
                    active_archive_date = (now.date() - timedelta(days=1)).isoformat()
                    logger.info(
                        "Resuming nightly archive after restart; frozen archive_date=%s",
                        active_archive_date,
                    )

                run = await ar.find_run_for_date(db, active_archive_date)
                if run and run.get("overall_status") == ar.STATUS_VERIFIED:
                    pass  # done for tonight
                else:
                    modules_ok = 0
                    for mod in REQUIRED_DAILY_DATASETS:
                        row = await am.find_acceptable_daily(db, mod, active_archive_date)
                        if row:
                            modules_ok += 1
                    if modules_ok >= 4 and run:
                        await ar.finalize_run(db, run["run_id"], complete=True)
                    elif modules_ok < 4:
                        logger.info(
                            "Triggering/catch-up same-day archive for frozen date %s (%s/4 ready)",
                            active_archive_date,
                            modules_ok,
                        )
                        try:
                            outcome = await run_daily_coordinated_archive(
                                db, active_archive_date, freeze_date=True
                            )
                            cycle = outcome.get("cycle") or evaluate_daily_cycle(outcome)
                            if cycle.get("complete"):
                                logger.info(
                                    "Daily coordinated archive COMPLETE for %s run_id=%s",
                                    active_archive_date,
                                    outcome.get("run_id"),
                                )
                            else:
                                logger.error(
                                    "Daily coordinated archive INCOMPLETE for %s — failed=%s missing=%s",
                                    active_archive_date,
                                    cycle.get("failed_datasets"),
                                    cycle.get("missing_datasets"),
                                )
                        except Exception as exc:
                            logger.error("Daily coordinated archive failed: %s", exc)
            else:
                # Outside window — clear in-memory freeze so next night starts fresh.
                active_archive_date = None

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
