"""Race-safe Pick claim + 50s confirmation window for mobile request alerts.

Used by Testing and Production mobile APIs. Backward compatible: locks without
confirm_deadline_at behave as before until the next Pick.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

CONFIRM_WINDOW_SECONDS = 50
ALREADY_PICKED_MESSAGE = "This request was already picked by another dealer."


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().isoformat()


def confirm_deadline_iso(from_dt: Optional[datetime] = None) -> str:
    base = from_dt or now_utc()
    return (base + timedelta(seconds=CONFIRM_WINDOW_SECONDS)).isoformat()


def parse_iso(value) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def is_confirm_expired(lock: Optional[dict], *, at: Optional[datetime] = None) -> bool:
    if not lock:
        return False
    if str(lock.get("lock_status") or "").lower() != "picked":
        return False
    deadline = parse_iso(lock.get("confirm_deadline_at"))
    if not deadline:
        return False
    return (at or now_utc()) >= deadline


async def release_expired_claim(db, lock: dict) -> Optional[dict]:
    """Atomically release a timed-out Pick so remaining dealers can compete again."""
    if not lock or not is_confirm_expired(lock):
        return None
    key = lock.get("request_group_key")
    if not key:
        return None
    released = await db.mobile_request_group_locks.find_one_and_delete(
        {
            "request_group_key": key,
            "lock_status": "picked",
            "confirm_deadline_at": lock.get("confirm_deadline_at"),
        }
    )
    if not released:
        return None
    await db.mobile_request_claim_events.insert_one(
        {
            "request_group_key": key,
            "event": "confirm_timeout_release",
            "device_id": lock.get("device_id"),
            "mobile_user_id": lock.get("mobile_user_id"),
            "dealer_name": lock.get("dealer_name"),
            "branch": lock.get("branch"),
            "released_at": now_iso(),
            "confirm_deadline_at": lock.get("confirm_deadline_at"),
        }
    )
    return released


async def dismissed_dealer_keys(db, request_group_key: str) -> set:
    """Dealers that Rejected this request (dealer-level opt-out)."""
    rows = await db.mobile_notification_actions.find(
        {
            "request_id": request_group_key,
            "action": "reject",
        },
        {"_id": 0, "dealer_name": 1},
    ).to_list(500)
    return {(r.get("dealer_name") or "").strip().casefold() for r in rows if r.get("dealer_name")}


def already_picked_detail(lock: Optional[dict]) -> Dict[str, Any]:
    name = ""
    if lock:
        name = (
            lock.get("device_user_name")
            or lock.get("accepted_by_name")
            or lock.get("device_name")
            or ""
        ).strip()
    return {
        "code": "ALREADY_PICKED",
        "message": ALREADY_PICKED_MESSAGE,
        "picked_by_name": name or "another dealer",
    }
