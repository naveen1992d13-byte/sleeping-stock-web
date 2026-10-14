"""Auto Perpetual blind-count helpers.

Keep restricted quantity fields off task payloads until the first physical
count is saved. Edit history is append-only and never summed.
"""
from typing import Any, Dict, Iterable, List, Optional

BLIND_RESTRICTED_KEYS = frozenset(
    {
        "system_qty",
        "system_quantity",
        "on_hand_qty",
        "on_hand_quantity",
        "expected_qty",
        "expected_quantity",
        "shortage_qty",
        "excess_qty",
        "shortage_value",
        "excess_value",
        "difference",
        "difference_qty",
        "quantity_status",
        "verification_status",
        "qty_result",
        "final_result",
        "mav",
        "line_value",
        "available_qty",
        "available_quantity",
        "matched",
        "match_status",
        "physical_quantity",
        "original_physical_quantity",
        "quantity_edit_history",
        "edit_count",
        "quantity_edit_status",
    }
)

TASK_IDENTITY_KEYS = (
    "id",
    "part_number",
    "part_name",
    "loc",
    "system_location",
    "session_id",
    "allocation_date",
    "coverage_kind",
    "suggestion_type",
    "task_id",
    "batch_no",
    "suggestion_id",
    "suggestion_number",
    "audit_id",
    "status",
    "mobile_user_id",
    "mobile_user_name",
)


def format_qty(value: Any) -> str:
    if value is None or value == "":
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number.is_integer():
        return str(int(number))
    return ("%f" % number).rstrip("0").rstrip(".")


def quantities_equal(left: Any, right: Any) -> bool:
    try:
        return abs(float(left) - float(right)) < 1e-9
    except (TypeError, ValueError):
        return left == right


def compact_edit_status(history: Optional[Iterable[dict]]) -> str:
    """Website Audit View label: Not Edited, or Edited [v1] [v2]."""
    values: List[str] = []
    for entry in history or []:
        if not isinstance(entry, dict):
            continue
        if "new_quantity" not in entry:
            continue
        formatted = format_qty(entry.get("new_quantity"))
        if formatted != "":
            values.append(formatted)
    if not values:
        return "Not Edited"
    return "Edited " + " ".join(f"[{value}]" for value in values)


def count_is_saved(row: Optional[dict] = None, history: Optional[dict] = None) -> bool:
    source = history or {}
    assignment = row or {}
    if source.get("original_physical_quantity") is not None:
        return True
    if source.get("physical_quantity") is not None:
        return True
    if assignment.get("count_saved") is True:
        return True
    if assignment.get("original_physical_quantity") is not None:
        return True
    if assignment.get("physical_quantity") is not None:
        return True
    return False


def is_quantity_finalized(row: Optional[dict] = None, history: Optional[dict] = None) -> bool:
    source = history or {}
    assignment = row or {}
    if source.get("quantity_finalized") is True:
        return True
    status = str(assignment.get("status") or source.get("status") or "").lower()
    return status in ("completed", "verified")


def auto_count_phase(value: Optional[str]) -> str:
    """save | finalize. Omitted/unknown from pre-change APKs means finalize."""
    phase = str(value or "").strip().lower()
    if phase == "save":
        return "save"
    return "finalize"


def strip_blind_fields(doc: Optional[dict]) -> dict:
    return {key: value for key, value in (doc or {}).items() if key not in BLIND_RESTRICTED_KEYS}


def public_auto_task(
    row: dict,
    *,
    loc: Any,
    session_id: Any,
    part_name: str,
    system_qty: Any = None,
    history: Optional[dict] = None,
) -> dict:
    """Task payload for Mobile Auto Perpetual.

    Restricted quantity fields are omitted until the first physical count is saved.
    """
    hist = history or {}
    saved = count_is_saved(row, hist)
    finalized = is_quantity_finalized(row, hist)
    edit_history = list(hist.get("quantity_edit_history") or row.get("quantity_edit_history") or [])
    out: Dict[str, Any] = {
        "id": row.get("id"),
        "part_number": row.get("part_number"),
        "part_name": part_name,
        "loc": loc,
        "system_location": loc,
        "session_id": session_id or row.get("session_id"),
        "allocation_date": row.get("allocation_date"),
        "coverage_kind": row.get("coverage_kind"),
        "suggestion_type": row.get("suggestion_type"),
        "task_id": row.get("task_id"),
        "batch_no": row.get("batch_no"),
        "suggestion_id": row.get("suggestion_id"),
        "suggestion_number": row.get("suggestion_number"),
        "audit_id": row.get("audit_id") or row.get("suggestion_number"),
        "status": row.get("status"),
        "mobile_user_id": row.get("mobile_user_id"),
        "count_saved": saved,
        "quantity_finalized": finalized,
        "edit_status_label": compact_edit_status(edit_history),
    }
    if not saved:
        return out

    physical = hist.get("physical_quantity")
    if physical is None:
        physical = row.get("physical_quantity")
    original = hist.get("original_physical_quantity")
    if original is None:
        original = row.get("original_physical_quantity")
    revealed_system = hist.get("system_quantity")
    if revealed_system is None:
        revealed_system = system_qty
    out.update(
        {
            "system_qty": revealed_system,
            "physical_quantity": physical,
            "original_physical_quantity": original,
            "quantity_edit_history": edit_history,
            "edit_count": int(hist.get("edit_count") or row.get("edit_count") or 0),
            "quantity_edit_status": hist.get("quantity_edit_status") or row.get("quantity_edit_status") or "",
            "difference": hist.get("difference"),
            "shortage_qty": hist.get("shortage_qty"),
            "excess_qty": hist.get("excess_qty"),
            "quantity_status": hist.get("quantity_status"),
            "verification_status": hist.get("verification_status"),
        }
    )
    return out


def build_edit_entry(
    *,
    previous_quantity: Any,
    new_quantity: Any,
    correction_reason: str,
    user_name: str,
    mobile_user_id: str,
    device_id: str,
    edited_at: Any,
    status: str,
) -> dict:
    return {
        "previous_quantity": previous_quantity,
        "new_quantity": new_quantity,
        "correction_reason": correction_reason,
        "user_name": user_name,
        "mobile_user_id": mobile_user_id,
        "device_id": device_id,
        "edited_at": edited_at,
        "status": status,
    }


def apply_quantity_edit(
    existing: dict,
    *,
    new_qty: float,
    correction_reason: str,
    user_name: str,
    mobile_user_id: str,
    device_id: str,
    edited_at: Any,
) -> dict:
    """Append an immutable history row when the latest quantity actually changes.

    Same-value saves return the existing record unchanged (same_value=True).
    Never totals historical quantities.
    """
    prev_qty = float(existing.get("physical_quantity") or 0)
    if quantities_equal(prev_qty, new_qty):
        return {
            "changed": False,
            "same_value": True,
            "original_physical_quantity": existing.get("original_physical_quantity", prev_qty),
            "physical_quantity": prev_qty,
            "quantity_edit_history": list(existing.get("quantity_edit_history") or []),
            "edit_count": int(existing.get("edit_count") or 0),
            "quantity_edit_status": existing.get("quantity_edit_status") or "",
        }

    original = existing.get("original_physical_quantity")
    if original is None:
        original = prev_qty
    history = list(existing.get("quantity_edit_history") or [])
    edit_count = int(existing.get("edit_count") or 0) + 1
    edit_status = "Edited" if edit_count == 1 else "Re-edited"
    history.append(
        build_edit_entry(
            previous_quantity=prev_qty,
            new_quantity=new_qty,
            correction_reason=correction_reason,
            user_name=user_name,
            mobile_user_id=mobile_user_id,
            device_id=device_id,
            edited_at=edited_at,
            status=edit_status,
        )
    )
    return {
        "changed": True,
        "same_value": False,
        "original_physical_quantity": original,
        "physical_quantity": new_qty,
        "quantity_edit_history": history,
        "edit_count": edit_count,
        "quantity_edit_status": edit_status,
    }
