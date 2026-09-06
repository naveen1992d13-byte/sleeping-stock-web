"""Write-once S3 key layout for event-driven archive.

Visible current vs cancelled separation, unique per entity id:

  {env}/uploads/{date}/current/{upload_id}/original.xlsx
  {env}/uploads/{date}/cancelled/{upload_id}/original.xlsx
  {env}/product-hub/{date}/current/{upload_id}/products.jsonl.gz
  {env}/product-hub/{date}/cancelled/{upload_id}/products.jsonl.gz
  {env}/orders/{date}/current/{order_id}/package.jsonl.gz
  {env}/orders/{date}/cancelled/{order_id}/package.jsonl.gz
  {env}/requests/{date}/current/{request_id}/package.jsonl.gz
  {env}/requests/{date}/cancelled/{request_id}/package.jsonl.gz

Legacy keys (still readable as fallback):
  {env}/uploads/{date}/product-hub/{upload_no}_{filename}
  {env}/product-history/{date}/products.jsonl.gz
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from s3_storage import storage_env

MODULE_UPLOADS = "uploads"
MODULE_PRODUCT_HUB = "product-hub"
MODULE_ORDERS = "orders"
MODULE_REQUESTS = "requests"

# Legacy nightly dump (Aug 15 style). Keep as read fallback only.
MODULE_PRODUCT_HISTORY_LEGACY = "product-history"

LIFECYCLE_CURRENT = "current"
LIFECYCLE_CANCELLED = "cancelled"

TERMINAL_CANCELLED_STATUSES = frozenset({
    "Cancelled",
    "Cancelled – No Response",
    "Cancelled - No Response",
    "Rejected",
    "No Further Stock",
    "No Further Stock Available",
})

TERMINAL_CURRENT_STATUSES = frozenset({
    "Completed",
})

ORDER_ITEM_TERMINAL_STATUSES = frozenset({
    "Completed",
    "Cancelled",
    "Cancelled – No Response",
    "Cancelled - No Response",
    "No Further Stock",
    "No Further Stock Available",
    "Rejected",
})

REQUEST_TERMINAL_STATUSES = frozenset({
    "Completed",
    "Rejected",
    "Cancelled",
})


def _ymd(value) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "").strip()
    if len(text) == 8 and text.isdigit():
        return f"{text[0:4]}-{text[4:6]}-{text[6:8]}"
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    return text


def _prefix() -> str:
    return storage_env()


def _entity_folder(module: str, archive_date, lifecycle: str, entity_id: str, filename: str) -> str:
    env = _prefix()
    day = _ymd(archive_date)
    eid = str(entity_id or "").strip()
    life = LIFECYCLE_CANCELLED if lifecycle == LIFECYCLE_CANCELLED else LIFECYCLE_CURRENT
    return f"{env}/{module}/{day}/{life}/{eid}/{filename}"


def upload_original_key(archive_date, upload_id: str, *, cancelled: bool = False, filename: str = "original.xlsx") -> str:
    return _entity_folder(
        MODULE_UPLOADS,
        archive_date,
        LIFECYCLE_CANCELLED if cancelled else LIFECYCLE_CURRENT,
        upload_id,
        filename,
    )


def product_hub_products_key(archive_date, upload_id: str, *, cancelled: bool = False) -> str:
    return _entity_folder(
        MODULE_PRODUCT_HUB,
        archive_date,
        LIFECYCLE_CANCELLED if cancelled else LIFECYCLE_CURRENT,
        upload_id,
        "products.jsonl.gz",
    )


def product_hub_companion_key(archive_date, upload_id: str, name: str, *, cancelled: bool = False) -> str:
    return _entity_folder(
        MODULE_PRODUCT_HUB,
        archive_date,
        LIFECYCLE_CANCELLED if cancelled else LIFECYCLE_CURRENT,
        upload_id,
        name,
    )


def order_package_key(archive_date, order_id: str, *, cancelled: bool = False) -> str:
    return _entity_folder(
        MODULE_ORDERS,
        archive_date,
        LIFECYCLE_CANCELLED if cancelled else LIFECYCLE_CURRENT,
        order_id,
        "package.jsonl.gz",
    )


def request_package_key(archive_date, request_id: str, *, cancelled: bool = False) -> str:
    return _entity_folder(
        MODULE_REQUESTS,
        archive_date,
        LIFECYCLE_CANCELLED if cancelled else LIFECYCLE_CURRENT,
        request_id,
        "package.jsonl.gz",
    )


def lifecycle_from_status(status: Optional[str]) -> str:
    text = str(status or "").strip()
    if text in TERMINAL_CANCELLED_STATUSES:
        return LIFECYCLE_CANCELLED
    return LIFECYCLE_CURRENT


def is_order_item_terminal(status: Optional[str]) -> bool:
    return str(status or "").strip() in ORDER_ITEM_TERMINAL_STATUSES


def is_request_terminal(status: Optional[str]) -> bool:
    return str(status or "").strip() in REQUEST_TERMINAL_STATUSES


def order_is_fully_terminal(items: list) -> bool:
    if not items:
        return False
    return all(is_order_item_terminal(it.get("status")) for it in items)
