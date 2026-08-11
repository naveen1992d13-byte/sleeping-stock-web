"""Excel / ZIP export permission helpers.

Approved rule:
- Master Admin (role=master) and Admin (role=admin) may download/export Excel/ZIP
- Normal User (role=user) may view/search/filter/copy/upload but NOT export Excel/ZIP
- Sample/upload templates remain allowed (upload functionality)
"""

from __future__ import annotations

from fastapi import HTTPException


EXPORT_FORBIDDEN_DETAIL = "Excel/ZIP export is restricted to Admin and Master Admin"


def can_export_excel(user) -> bool:
    role = getattr(user, "role", None) or (user.get("role") if isinstance(user, dict) else None)
    return role in {"master", "admin"}


def require_excel_export(user) -> None:
    if not can_export_excel(user):
        raise HTTPException(status_code=403, detail=EXPORT_FORBIDDEN_DETAIL)


def is_export_path(path: str) -> bool:
    """Heuristic used by tests / middleware audits."""
    p = (path or "").lower()
    export_markers = (
        "/export",
        "/download",
        "/raw-file",
        "/excel",
        ".xlsx",
        ".xls",
        ".zip",
    )
    # Allow upload templates / blank templates
    allow_markers = (
        "/sample-template",
        "/order-desk/template",
        "/templates/download",
        "/upload/sample-template",
        "/orders/sample-template",
        "/app-versions/",  # APK, not Excel
        "/notice-board/attachments/",  # PDF
        "/queries/attachments/",  # mixed attachments — not Excel export of data
    )
    if any(m in p for m in allow_markers):
        return False
    return any(m in p for m in export_markers)
