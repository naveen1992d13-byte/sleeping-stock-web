"""Race-safe Pick claim + configurable confirmation window for mobile request alerts.

Used by Testing and Production mobile APIs. Backward compatible: locks without
confirm_deadline_at behave as before until the next Pick. Existing locks that
already store confirm_window_seconds keep that stored value.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

# Default confirm window after Pick: 50 minutes (was hard-coded 50 seconds).
DEFAULT_CONFIRM_WINDOW_MINUTES = 50
CONFIRM_WINDOW_SECONDS = DEFAULT_CONFIRM_WINDOW_MINUTES * 60
CONFIRM_WINDOW_SETTINGS_ID = "mobile_confirm_window_minutes"
ALREADY_PICKED_MESSAGE = "This request was already picked by another dealer."


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().isoformat()


def minutes_to_confirm_seconds(minutes: Any) -> int:
    """Clamp configured minutes to a sane positive window (1..24h)."""
    try:
        mins = int(minutes)
    except (TypeError, ValueError):
        mins = DEFAULT_CONFIRM_WINDOW_MINUTES
    if mins < 1:
        mins = DEFAULT_CONFIRM_WINDOW_MINUTES
    if mins > 24 * 60:
        mins = 24 * 60
    return mins * 60


async def resolve_confirm_window_seconds(db) -> int:
    """Read mobile_settings.mobile_confirm_window_minutes; default 50 minutes."""
    if db is None:
        return CONFIRM_WINDOW_SECONDS
    settings = getattr(db, "mobile_settings", None)
    if settings is None:
        return CONFIRM_WINDOW_SECONDS
    try:
        row = await settings.find_one({"_id": CONFIRM_WINDOW_SETTINGS_ID})
    except Exception:
        return CONFIRM_WINDOW_SECONDS
    if not row:
        return CONFIRM_WINDOW_SECONDS
    if row.get("minutes") is not None:
        return minutes_to_confirm_seconds(row.get("minutes"))
    if row.get("interval_minutes") is not None:
        return minutes_to_confirm_seconds(row.get("interval_minutes"))
    return CONFIRM_WINDOW_SECONDS


def confirm_deadline_iso(
    from_dt: Optional[datetime] = None,
    *,
    window_seconds: Optional[int] = None,
) -> str:
    base = from_dt or now_utc()
    seconds = int(window_seconds) if window_seconds is not None else CONFIRM_WINDOW_SECONDS
    if seconds < 1:
        seconds = CONFIRM_WINDOW_SECONDS
    return (base + timedelta(seconds=seconds)).isoformat()


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
