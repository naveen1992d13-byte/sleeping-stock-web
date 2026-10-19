"""Analytics-only nightly rollup loop (23:00 IST) behind ANALYTICS_ROLLUP_SCHEDULER_ENABLED.

For hosts where the full archive scheduler is disabled (Testing forces
ARCHIVE_SCHEDULER_ENABLED=false). Default off, so Production keeps finalizing
through archive_scheduler only.

23:00–23:59 IST finalizes today's IST date; 00:00–03:59 catches up the
previous IST date after a restart. A date already finalized (current
rollup_version) is skipped. Failures retry every 10 minutes, at most 3 times.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta
from typing import Dict, Optional
from zoneinfo import ZoneInfo

try:
    from . import analytics_rollup as rollup
except ImportError:
    import analytics_rollup as rollup

logger = logging.getLogger("nmts.analytics_rollup_scheduler")
IST = ZoneInfo("Asia/Kolkata")

ENV_FLAG = "ANALYTICS_ROLLUP_SCHEDULER_ENABLED"
POLL_SECONDS = 60
RETRY_SECONDS = 10 * 60
MAX_ATTEMPTS = 3

_scheduler_task: Optional[asyncio.Task] = None


def scheduler_enabled() -> bool:
    return str(os.environ.get(ENV_FLAG, "false")).strip().lower() in {"1", "true", "yes", "on"}


def due_date(now: datetime) -> Optional[str]:
    """IST date to finalize at this moment, or None outside 23:00–03:59."""
    if now.hour >= 23:
        return now.date().isoformat()
    if now.hour < 4:
        return (now.date() - timedelta(days=1)).isoformat()
    return None


class _State:
    def __init__(self):
        self.done: set = set()
        self.attempts: Dict[str, int] = {}
        self.next_try: Dict[str, datetime] = {}


async def run_once(db, state: _State, now: Optional[datetime] = None) -> Optional[dict]:
    now = now or datetime.now(IST)
    target = due_date(now)
    if not target or target in state.done:
        return None
    if state.attempts.get(target, 0) >= MAX_ATTEMPTS:
        return None
    if state.next_try.get(target) and now < state.next_try[target]:
        return None

    existing = await rollup.get_rollup_status(db, target)
    if rollup.is_current_success(existing):
        state.done.add(target)
        logger.info("Analytics rollup %s already finalized (v%s) — skipped", target, rollup.ROLLUP_VERSION)
        return {"status": "skipped", "rollup_date_ist": target}

    state.attempts[target] = state.attempts.get(target, 0) + 1
    logger.info("Analytics rollup nightly run start date=%s attempt=%s", target, state.attempts[target])
    result = await rollup.finalize_analytics_rollup_for_date(db, target, force=False)
    status = str(result.get("status") or "")
    if status in {rollup.STATUS_SUCCESS, rollup.STATUS_NO_SOURCE}:
        state.done.add(target)
        logger.info(
            "Analytics rollup nightly run done date=%s status=%s parts=%s value=%s",
            target, status, result.get("snapshot_record_count"), result.get("daily_agg_total_value"),
        )
    else:
        state.next_try[target] = now + timedelta(seconds=RETRY_SECONDS)
        logger.error(
            "Analytics rollup nightly run not successful date=%s status=%s error=%s",
            target, status, result.get("error") or result.get("reason"),
        )
    return result


async def _scheduler_loop(db) -> None:
    logger.info("Analytics rollup scheduler started — nightly 23:00 IST (%s=true)", ENV_FLAG)
    state = _State()
    while True:
        try:
            await run_once(db, state)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Analytics rollup scheduler loop error: %s", exc)
        await asyncio.sleep(POLL_SECONDS)


def start_analytics_rollup_scheduler(db) -> bool:
    global _scheduler_task
    if not scheduler_enabled():
        logger.info("Analytics rollup scheduler disabled (%s is not true)", ENV_FLAG)
        return False
    if _scheduler_task and not _scheduler_task.done():
        return True
    _scheduler_task = asyncio.create_task(_scheduler_loop(db))
    return True


async def stop_analytics_rollup_scheduler() -> None:
    global _scheduler_task
    if _scheduler_task and not _scheduler_task.done():
        _scheduler_task.cancel()
        try:
            await _scheduler_task
        except asyncio.CancelledError:
            pass
    _scheduler_task = None
