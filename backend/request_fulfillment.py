"""Derived Request Center presentation fields.

`fulfillment_stage` and `part_status` are computed from existing `status`
plus additive `picking_finished_at` / `dispatch_document`. They are never
stored as a replacement for `status`, `accepted_qty`, or `remaining_qty`.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Optional

CLOSED_STATUSES = {
    "Rejected",
    "Cancelled",
    "No Response",
    "Timeout",
    "Expired",
    "Cancellation Requested",
}

STAGE_SENT = "sent"
STAGE_PICKING = "picking"
STAGE_PICKING_FINISHED = "picking_finished"
STAGE_IN_TRANSIT = "in_transit"
STAGE_FINISHED = "finished"

STAGE_RANK = {
    STAGE_SENT: 1,
    STAGE_PICKING: 2,
    STAGE_PICKING_FINISHED: 3,
    STAGE_IN_TRANSIT: 4,
    STAGE_FINISHED: 5,
}

DISPATCH_INCOMPLETE_MESSAGE = (
    "Please complete the document details and upload the document before dispatch."
)


def _status(row: Optional[Mapping[str, Any]]) -> str:
    return str((row or {}).get("status") or "").strip()


def fulfillment_stage(row: Optional[Mapping[str, Any]]) -> Optional[str]:
    """Derived presentation stage. None means exclude from the 4 stage tabs."""
    status = _status(row)
    if not status or status in CLOSED_STATUSES:
        return None
    if status == "Requested":
        return STAGE_SENT
    if status in ("Approved", "Partially Approved"):
        if (row or {}).get("picking_finished_at"):
            return STAGE_PICKING_FINISHED
        return STAGE_PICKING
    if status == "Dispatched":
        return STAGE_IN_TRANSIT
    if status in ("Received", "Completed"):
        return STAGE_FINISHED
    return None


def part_status(row: Optional[Mapping[str, Any]]) -> str:
    """Per-part display status. Additive to stored `status`."""
    status = _status(row)
    if status == "Requested":
        return "Pending"
    if status == "Rejected":
        return "Not Available"
    if status in ("Cancelled", "No Response", "Timeout", "Expired"):
        return status or "Pending"
    if status in ("Approved", "Partially Approved"):
        if (row or {}).get("picking_finished_at"):
            try:
                accepted = float((row or {}).get("accepted_qty") or (row or {}).get("approved_qty") or 0)
                requested = float((row or {}).get("requested_qty") or 0)
            except (TypeError, ValueError):
                accepted, requested = 0.0, 0.0
            if requested and accepted < requested:
                return "Partial"
            return "Picked"
        return "Picking"
    if status == "Dispatched":
        return "Dispatched"
    if status in ("Received", "Completed"):
        return "Finished"
    return status or "Pending"


def group_fulfillment_stage(items: list) -> Optional[str]:
    """Earliest incomplete stage among non-closed items (the bottleneck)."""
    stages = [fulfillment_stage(item) for item in (items or [])]
    present = [s for s in stages if s]
    if not present:
        return None
    return min(present, key=lambda s: STAGE_RANK.get(s, 99))


def _text(value) -> str:
    return str(value or "").strip()


def dispatch_document_complete(record: Optional[Mapping[str, Any]]) -> bool:
    doc = (record or {}).get("dispatch_document") or {}
    if not isinstance(doc, dict):
        return False
    if not _text(doc.get("document_no")):
        return False
    if not _text(doc.get("document_date")):
        return False
    raw_value = doc.get("document_value")
    if raw_value in (None, ""):
        return False
    try:
        float(str(raw_value).replace(",", "").replace("₹", "").strip())
    except (TypeError, ValueError):
        return False
    return bool(_text(doc.get("storage_key")))


def apply_presentation(row: dict) -> dict:
    """Attach derived fields onto a request item without mutating stored status."""
    row = dict(row or {})
    row["fulfillment_stage"] = fulfillment_stage(row)
    row["part_status"] = part_status(row)
    try:
        requested = float(row.get("requested_qty") or 0)
        accepted = float(row.get("accepted_qty") if row.get("accepted_qty") not in (None, "") else row.get("approved_qty") or 0)
    except (TypeError, ValueError):
        requested, accepted = 0.0, 0.0
    row["balance_qty"] = max(0.0, requested - accepted)
    if row.get("value") in (None, "") and row.get("value_at_request") not in (None, ""):
        row["value"] = row.get("value_at_request")
    if row.get("total_value") in (None, ""):
        try:
            unit = float(row.get("unit_value_at_request") or row.get("unit_value") or row.get("part_value") or 0)
            qty = float(row.get("requested_qty") or 0)
            stored = row.get("value_at_request")
            row["total_value"] = float(stored) if stored not in (None, "") else unit * qty
        except (TypeError, ValueError):
            row["total_value"] = 0
    return row


def parse_iso(value) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def in_transit_marker(row: Optional[Mapping[str, Any]], now: Optional[datetime] = None) -> dict:
    """Flag-only 24/48/72h markers for Dispatched requests. Never mutates status."""
    now = now or datetime.now(timezone.utc)
    dispatched_at = parse_iso((row or {}).get("dispatched_at") or (row or {}).get("dispatch_at"))
    flags = dict((row or {}).get("in_transit_flags") or {})
    result = {
        "in_transit_hours": 0.0,
        "in_transit_level": None,
        "in_transit_24h": bool(flags.get("24h")),
        "in_transit_48h": bool(flags.get("48h")),
        "in_transit_72h": bool(flags.get("72h")),
    }
    if fulfillment_stage(row) != STAGE_IN_TRANSIT or not dispatched_at:
        return result
    hours = max(0.0, (now - dispatched_at).total_seconds() / 3600.0)
    result["in_transit_hours"] = round(hours, 2)
    if hours >= 72:
        result["in_transit_level"] = "72h"
        result["in_transit_72h"] = True
    elif hours >= 48:
        result["in_transit_level"] = "48h"
        result["in_transit_48h"] = True
    elif hours >= 24:
        result["in_transit_level"] = "24h"
        result["in_transit_24h"] = True
    return result
