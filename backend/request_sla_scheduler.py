"""Request Center SLA scheduler — automatic timeout + mobile reminder pushes.

Reuses existing cancel-timeout / response_deadline fields. Does not change
SLA minute buckets (30 / 45 / 60). Reminders fire at first third, second
third, and one minute before the unchanged deadline.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Optional

import order_desk_workflow as odw

logger = logging.getLogger("nmts.request_sla")

_scheduler_task: Optional[asyncio.Task] = None
POLL_SECONDS = 60

ApplyTimeout = Callable[[dict], Awaitable[None]]
SendReminder = Callable[[dict, str], Awaitable[None]]


def _parse_iso(value):
    return odw._parse_iso(value)


def reminder_due_kinds(header: dict, now: datetime = None) -> list:
    """Return reminder kinds that are due now and not yet marked sent."""
    now = now or datetime.now(timezone.utc)
    timer = odw.evaluate_group_timer(header, now)
    if timer.get('response_status') != 'awaiting':
        return []
    sent = header.get('mobile_push_sent') or {}
    r1 = header.get('reminder_at')
    r2 = header.get('urgent_reminder_at')
    r3 = header.get('reminder_3_at')
    if not r3:
        sent_at = _parse_iso(header.get('request_sent_at') or header.get('created_at'))
        minutes = int(header.get('response_time_minutes') or timer.get('response_time_minutes') or 0)
        if sent_at and minutes:
            offsets = odw.reminder_offsets_minutes(minutes)
            r1 = r1 or (sent_at + timedelta(minutes=offsets[0])).isoformat()
            r2 = r2 or (sent_at + timedelta(minutes=offsets[1])).isoformat()
            r3 = (sent_at + timedelta(minutes=offsets[2])).isoformat()
    due = []
    for kind, when in (('reminder_1', r1), ('reminder_2', r2), ('reminder_3', r3)):
        if sent.get(kind) or sent.get(kind + '_at'):
            continue
        dt = _parse_iso(when)
        if dt and dt <= now:
            due.append(kind)
    return due


def is_timeout_due(header: dict, now: datetime = None) -> bool:
    now = now or datetime.now(timezone.utc)
    if (header.get('status') or '') != 'Requested':
        return False
    if header.get('timeout_cancelled'):
        return False
    timer = odw.evaluate_group_timer(header, now)
    return bool(timer.get('cancel_allowed'))


async def claim_push(db, header: dict, kind: str) -> bool:
    """Atomically claim one push kind for a request group. False if already sent."""
    key = header.get('id') or header.get('request_number')
    if not key:
        return False
    try:
        await db.mobile_request_push_claims.insert_one({
            'request_group_key': key,
            'kind': kind,
            'request_number': header.get('request_number'),
            'created_at': datetime.now(timezone.utc).isoformat(),
        })
        return True
    except Exception:
        return False


async def run_sla_cycle(db, apply_timeout: ApplyTimeout, send_reminder: SendReminder) -> dict:
    now = datetime.now(timezone.utc)
    timed_out = 0
    reminded = 0
    headers = await db.request_headers.find(
        {'status': 'Requested', 'timeout_cancelled': {'$ne': True}},
        {'_id': 0},
    ).to_list(2000)
    for header in headers:
        try:
            if is_timeout_due(header, now):
                await apply_timeout(header)
                timed_out += 1
                continue
            for kind in reminder_due_kinds(header, now):
                if await claim_push(db, header, kind):
                    await send_reminder(header, kind)
                    await db.request_headers.update_one(
                        {'id': header.get('id')},
                        {'$set': {f'mobile_push_sent.{kind}': now.isoformat(), 'updated_at': now.isoformat()}},
                    )
                    reminded += 1
        except Exception as exc:
            logger.warning(
                "SLA cycle failed for %s: %s",
                header.get('request_number'),
                str(exc)[:300],
            )
    return {'scanned': len(headers), 'timed_out': timed_out, 'reminded': reminded}


async def _scheduler_loop(db, apply_timeout: ApplyTimeout, send_reminder: SendReminder):
    logger.info("Request SLA scheduler started")
    while True:
        try:
            result = await run_sla_cycle(db, apply_timeout, send_reminder)
            if result.get('timed_out') or result.get('reminded'):
                logger.info("Request SLA cycle: %s", result)
        except asyncio.CancelledError:
            logger.info("Request SLA scheduler cancelled")
            raise
        except Exception as exc:
            logger.error("Request SLA scheduler loop error: %s", exc)
        await asyncio.sleep(POLL_SECONDS)


def start_request_sla_scheduler(db, apply_timeout: ApplyTimeout, send_reminder: SendReminder) -> None:
    global _scheduler_task
    if _scheduler_task and not _scheduler_task.done():
        return
    _scheduler_task = asyncio.create_task(_scheduler_loop(db, apply_timeout, send_reminder))


async def stop_request_sla_scheduler() -> None:
    global _scheduler_task
    if _scheduler_task and not _scheduler_task.done():
        _scheduler_task.cancel()
        try:
            await _scheduler_task
        except asyncio.CancelledError:
            pass
    _scheduler_task = None
