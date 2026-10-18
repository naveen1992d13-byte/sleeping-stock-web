"""Auto Perpetual session cards, IST-today detection, and re-audit helpers.

These helpers do not change assignment generation, 50/25/25 selection,
session ID minting, or daily allocation rules. They only classify already
stored assignment rows for the Mobile session list.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from auto_perpetual import ist_date_key

TODAY_SESSION_NOT_CREATED = "Today's session is not created yet"


def is_completed_assignment(row: Optional[dict]) -> bool:
    status = str((row or {}).get("status") or "").strip().lower()
    if status in ("completed", "verified"):
        return True
    if (row or {}).get("quantity_finalized") is True:
        return True
    return False


def classify_auto_session_card(
    *,
    allocation_date: Optional[str],
    pending_count: int,
    today: Optional[str] = None,
) -> dict:
    """Today vs previous-session card labels.

    Previous pending work is EXPIRED (re-audit available). It is never
    classified as today's session.
    """
    day = today or ist_date_key()
    alloc = str(allocation_date or "")
    is_today = bool(alloc) and alloc == day
    if is_today:
        return {
            "is_today": True,
            "card_status": "TODAY",
            "reaudit_available": False,
            "read_only": False,
            "editable": True,
        }
    if int(pending_count or 0) > 0:
        return {
            "is_today": False,
            "card_status": "EXPIRED",
            "reaudit_available": True,
            "read_only": True,
            "editable": False,
        }
    return {
        "is_today": False,
        "card_status": "COMPLETED",
        "reaudit_available": False,
        "read_only": True,
        "editable": False,
    }


def _group_key(row: dict) -> tuple:
    alloc = str(row.get("allocation_date") or "")
    sid = str(row.get("session_id") or "") or alloc
    return (sid, alloc)


def summarize_auto_sessions(
    rows: Iterable[dict],
    *,
    today: Optional[str] = None,
) -> Dict[str, Any]:
    """Build collapsed session cards from assignment rows.

    Today is the server IST calendar date. A previous pending session is
    never returned as today_created / TODAY.
    """
    day = today or ist_date_key()
    groups: Dict[tuple, dict] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        key = _group_key(row)
        group = groups.get(key)
        if group is None:
            group = {
                "session_id": row.get("session_id") or None,
                "allocation_date": str(row.get("allocation_date") or ""),
                "assigned_count": 0,
                "verified_count": 0,
                "pending_count": 0,
                "audit_id": row.get("audit_id") or row.get("suggestion_number"),
                "suggestion_number": row.get("suggestion_number"),
            }
            groups[key] = group
        if not group.get("session_id") and row.get("session_id"):
            group["session_id"] = row.get("session_id")
        group["assigned_count"] += 1
        if is_completed_assignment(row):
            group["verified_count"] += 1
        else:
            group["pending_count"] += 1

    today_row = None
    previous: List[dict] = []
    for group in groups.values():
        cls = classify_auto_session_card(
            allocation_date=group["allocation_date"],
            pending_count=group["pending_count"],
            today=day,
        )
        assigned = int(group["assigned_count"] or 0)
        verified = int(group["verified_count"] or 0)
        progress = int(round((verified / assigned) * 100)) if assigned else 0
        item = {**group, **cls, "progress_pct": progress}
        if item["is_today"]:
            today_row = item
        else:
            previous.append(item)
    previous.sort(key=lambda row: str(row.get("allocation_date") or ""), reverse=True)
    ordered = ([today_row] if today_row else []) + previous
    return {
        "today": day,
        "today_created": bool(today_row),
        "message": None if today_row else TODAY_SESSION_NOT_CREATED,
        "sessions": ordered,
    }


def filter_session_tasks(
    rows: Iterable[dict],
    *,
    mode: str = "view",
    include_zero_live: bool = True,
    live_qty_by_part: Optional[dict] = None,
) -> List[dict]:
    """view = all parts; reaudit = pending only. Zero live qty is never dropped."""
    wanted = str(mode or "view").strip().lower()
    if wanted not in ("view", "reaudit"):
        wanted = "view"
    out: List[dict] = []
    live = live_qty_by_part or {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        pending = not is_completed_assignment(row)
        if wanted == "reaudit" and not pending:
            continue
        part = str(row.get("part_number") or "").strip().upper()
        live_qty = live.get(part)
        if live_qty is None:
            live_qty = live.get(row.get("part_number"))
        # Explicitly keep live-qty-zero rows. Never auto-close them.
        if include_zero_live is False and live_qty is not None:
            try:
                if float(live_qty) == 0:
                    continue
            except (TypeError, ValueError):
                pass
        out.append(row)
    return out


def session_task_flags(
    *,
    allocation_date: Optional[str],
    mode: str,
    today: Optional[str] = None,
) -> dict:
    """UI/API flags for a session's parts list."""
    day = today or ist_date_key()
    alloc = str(allocation_date or "")
    is_today = bool(alloc) and alloc == day
    wanted = str(mode or "view").strip().lower()
    reaudit = wanted == "reaudit" and not is_today
    editable = is_today or reaudit
    return {
        "is_today": is_today,
        "reaudit": reaudit,
        "editable": editable,
        "read_only": not editable,
        "mode": "reaudit" if reaudit else "view",
    }
