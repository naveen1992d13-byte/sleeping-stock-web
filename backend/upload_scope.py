"""Upload Center Brand → Dealer → Branch guard.

Shared by the product/order upload endpoints so a missing or "All …"
placeholder cannot reach file parsing. Display-layer only for the
DashboardLayout selector — this module never changes stored field names.
"""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException


def is_placeholder_scope(value: Optional[str]) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    lowered = text.lower()
    return (
        lowered.startswith("all ")
        or lowered in {"all", "n/a", "na", "none"}
    )


def missing_upload_scope_fields(brand: Optional[str], dealer: Optional[str], branch: Optional[str]) -> list:
    missing = []
    if is_placeholder_scope(brand):
        missing.append("Brand")
    if is_placeholder_scope(dealer):
        missing.append("Dealer")
    if is_placeholder_scope(branch):
        missing.append("Branch")
    return missing


def upload_scope_error_message(brand: Optional[str], dealer: Optional[str], branch: Optional[str]) -> Optional[str]:
    missing = missing_upload_scope_fields(brand, dealer, branch)
    if not missing:
        return None
    if len(missing) == 1:
        return f"Please select {missing[0]}."
    return " ".join(f"Please select {name}." for name in missing)


def require_specific_upload_scope(brand: Optional[str] = None, dealer: Optional[str] = None, branch: Optional[str] = None) -> None:
    """Raise HTTP 400 before any file processing when Brand/Dealer/Branch is missing."""
    message = upload_scope_error_message(brand, dealer, branch)
    if message:
        raise HTTPException(status_code=400, detail=message)
