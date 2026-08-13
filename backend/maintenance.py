"""IST maintenance window helpers (23:00–04:00).

Normal users are blocked from login and mutations during the window.
Master Admin retains read-only access to Storage/archive monitoring.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

MAINTENANCE_MESSAGE = (
    "System maintenance in progress. Please try again after 4:00 AM."
)

# Paths Master may still read during maintenance (prefix match after /api)
MASTER_READONLY_PREFIXES = (
    "/storage/status",
    "/storage/monitor",
    "/storage/archives",
    "/storage/archive-runs",
    "/storage/external-links",
    "/auth/me",
)


def ist_now(now: Optional[datetime] = None) -> datetime:
    value = now or datetime.now(IST)
    if value.tzinfo is None:
        return value.replace(tzinfo=IST)
    return value.astimezone(IST)


def in_maintenance_window(now: Optional[datetime] = None) -> bool:
    """True for 23:00:00–23:59:59 and 00:00:00–03:59:59 IST."""
    local = ist_now(now)
    hour = local.hour
    return hour >= 23 or hour < 4


def maintenance_status(now: Optional[datetime] = None) -> dict:
    local = ist_now(now)
    active = in_maintenance_window(local)
    return {
        "maintenance_active": active,
        "timezone": "Asia/Kolkata",
        "window": "23:00–04:00 IST",
        "server_time_ist": local.isoformat(),
        "message": MAINTENANCE_MESSAGE if active else None,
        "master_readonly_allowed": True,
    }


def is_master_readonly_path(path: str) -> bool:
    p = (path or "").split("?")[0]
    if p.startswith("/api"):
        p = p[4:] or "/"
    if not p.startswith("/"):
        p = "/" + p
    return any(p == pref or p.startswith(pref + "/") for pref in MASTER_READONLY_PREFIXES)


def is_mutating_method(method: str) -> bool:
    return (method or "").upper() in {"POST", "PUT", "PATCH", "DELETE"}
