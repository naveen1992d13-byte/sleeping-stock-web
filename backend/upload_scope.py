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


def canonical_allowed_branch(selected: Optional[str], allowed_branches) -> Optional[str]:
    """Return the stored branch name when `selected` matches an allowed branch."""
    text = str(selected or "").strip()
    if is_placeholder_scope(text):
        return None
    for name in allowed_branches or []:
        candidate = str(name or "").strip()
        if candidate and candidate.casefold() == text.casefold():
            return candidate
    return None


def resolve_actor_upload_branch(
    *,
    role: Optional[str],
    assigned_branch: Optional[str],
    selected_branch: Optional[str],
    allowed_branches=(),
) -> str:
    """Branch written by an upload.

    Admin uploads use the currently selected branch only when it is one of that
    admin's allowed branches. The assigned/default branch is not a fallback
    for a different selection. Users stay on their assigned branch.
    """
    role_key = str(role or "").strip().lower()
    assigned = str(assigned_branch or "").strip()
    if role_key == "master":
        selected = str(selected_branch or "").strip()
        if is_placeholder_scope(selected):
            raise HTTPException(status_code=400, detail=upload_scope_error_message("Brand", "Dealer", selected_branch) or "Please select Branch.")
        return selected
    if role_key == "admin":
        match = canonical_allowed_branch(selected_branch, allowed_branches)
        if not match:
            raise HTTPException(status_code=403, detail="Selected branch is outside your allowed branches")
        return match
    if is_placeholder_scope(assigned):
        raise HTTPException(status_code=403, detail="No branch is assigned to this user")
    return assigned
