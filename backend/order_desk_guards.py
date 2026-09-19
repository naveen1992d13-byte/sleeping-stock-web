"""Reusable Order Desk mutation guards and additive value helpers.

Does not rename required_qty / accepted_qty / remaining_qty / unit_value.
`value` and `total_value` are additive fields populated from the same
per-unit figure already stored as unit_value.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

from fastapi import HTTPException


FINISHED_ORDER_MESSAGE = "This order is finished and read-only."


def _f(value, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(str(value).replace(",", "").replace("₹", "").strip() or default)
    except (TypeError, ValueError):
        return default


def is_order_finished(order: Optional[Mapping[str, Any]]) -> bool:
    if not order:
        return False
    if order.get("finished_at"):
        return True
    status = str(order.get("status") or "").strip()
    if status in {"Finished", "finished"}:
        return True
    if status == "Completed" and (order.get("finished_by") or order.get("overall_status") == "Completed"):
        return True
    return False


def assert_order_not_finished(order: Optional[Mapping[str, Any]]) -> None:
    if is_order_finished(order):
        raise HTTPException(status_code=409, detail=FINISHED_ORDER_MESSAGE)


def order_line_unit_value(item: Optional[Mapping[str, Any]]) -> float:
    """Per-unit value. Prefer additive `value`, then legacy `unit_value`."""
    item = item or {}
    for key in ("value", "unit_value", "unit_value_at_request", "part_value"):
        if item.get(key) not in (None, ""):
            return _f(item.get(key))
    return 0.0


def order_line_total_value(item: Optional[Mapping[str, Any]]) -> float:
    """Line total. Prefer stored `total_value`, else value × requested_qty."""
    item = item or {}
    if item.get("total_value") not in (None, ""):
        return _f(item.get("total_value"))
    qty = _f(item.get("required_qty") if item.get("required_qty") not in (None, "") else item.get("requested_qty"))
    return round(order_line_unit_value(item) * qty, 4)


def apply_order_item_values(item: dict, qty: float = None, unit: float = None) -> dict:
    """Stamp additive value / total_value without dropping unit_value."""
    if unit is None:
        unit = order_line_unit_value(item)
    if qty is None:
        qty = _f(item.get("required_qty") if item.get("required_qty") not in (None, "") else item.get("quantity"))
    item["unit_value"] = unit
    item["value"] = unit
    item["total_value"] = round(unit * qty, 4)
    return item
