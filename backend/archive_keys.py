"""Write-once S3 key layout for event-driven archive.

Upload Center and Product History have no UUID folder. Filename is unique:

  {env}/uploads/{date}/current|cancelled/Brand_Dealer_Branch_Ref_UploadCenter.xlsx
  {env}/product-history/{date}/current|cancelled/Brand_Dealer_Branch_Ref_ProductHistory.jsonl.gz

Orders/Requests keep an entity folder (workflow unchanged):

  {env}/orders/{date}/current|cancelled/{order_id}/Brand_Dealer_Branch_Ref_Order.jsonl.gz
  {env}/requests/{date}/current|cancelled/{request_id}/Brand_Dealer_Branch_Ref_Request.jsonl.gz

Date lives only in the folder path — never in the filename.

Legacy keys (still readable as fallback):
  {env}/uploads/{date}/product-hub/{upload_no}_{filename}
  {env}/product-history/{date}/products.jsonl.gz
  {env}/product-hub/{date}/current|{cancelled}/{upload_id}/products.jsonl.gz
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from s3_storage import storage_env

MODULE_UPLOADS = "uploads"
MODULE_PRODUCT_HISTORY = "product-history"
# Publish archives live under Product History. Keep the old name as an alias
# so leftover manifests/tests still resolve.
MODULE_PRODUCT_HUB = MODULE_PRODUCT_HISTORY
MODULE_ORDERS = "orders"
MODULE_REQUESTS = "requests"

MODULE_LABEL_UPLOAD = "UploadCenter"
MODULE_LABEL_PRODUCT_HISTORY = "ProductHistory"
MODULE_LABEL_ORDER = "Order"
MODULE_LABEL_REQUEST = "Request"

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


def sanitize_name_token(value) -> str:
    """Filename-safe token. Spaces → underscore; drop other punctuation."""
    raw = str(value or "").strip()
    chars = []
    for ch in raw:
        if ch.isalnum() or ch in {"-", "_"}:
            chars.append(ch)
        elif ch.isspace():
            chars.append("_")
    collapsed = "_".join(part for part in "".join(chars).split("_") if part)
    return collapsed or "Unknown"


def dealer_filename_token(dealer, brand="") -> str:
    """Prefer short dealer token: 'FPL Hyundai' + brand Hyundai → FPL."""
    dealer_text = str(dealer or "").strip()
    brand_text = str(brand or "").strip()
    if dealer_text and brand_text:
        lower_d, lower_b = dealer_text.lower(), brand_text.lower()
        if lower_d.endswith(lower_b) and lower_d != lower_b:
            dealer_text = dealer_text[: len(dealer_text) - len(brand_text)].strip(" -_")
    return sanitize_name_token(dealer_text)


def archive_filename(brand, dealer, branch, reference_id, module_label: str, suffix: str) -> str:
    """Brand_Dealer_Branch_ReferenceID_Module.ext — date is never in the name."""
    parts = [
        sanitize_name_token(brand),
        dealer_filename_token(dealer, brand),
        sanitize_name_token(branch),
        sanitize_name_token(reference_id),
        str(module_label or "Archive").strip() or "Archive",
    ]
    name = "_".join(p for p in parts if p)
    ext = str(suffix or "").lstrip(".")
    return f"{name}.{ext}" if ext else name


def _entity_folder(module: str, archive_date, lifecycle: str, entity_id: str, filename: str) -> str:
    env = _prefix()
    day = _ymd(archive_date)
    eid = str(entity_id or "").strip()
    life = LIFECYCLE_CANCELLED if lifecycle == LIFECYCLE_CANCELLED else LIFECYCLE_CURRENT
    fname = str(filename or "archive.bin").split("/")[-1]
    return f"{env}/{module}/{day}/{life}/{eid}/{fname}"


def _lifecycle_file_key(module: str, archive_date, lifecycle: str, filename: str) -> str:
    """{env}/{module}/{date}/{current|cancelled}/{filename} — no UUID folder."""
    env = _prefix()
    day = _ymd(archive_date)
    life = LIFECYCLE_CANCELLED if lifecycle == LIFECYCLE_CANCELLED else LIFECYCLE_CURRENT
    fname = str(filename or "archive.bin").split("/")[-1]
    return f"{env}/{module}/{day}/{life}/{fname}"


def cancelled_key_from_current(storage_key: str) -> str:
    """Same filename/entity folder, lifecycle current → cancelled. No-op if already cancelled."""
    key = str(storage_key or "")
    if "/cancelled/" in key:
        return key
    if "/current/" in key:
        return key.replace("/current/", "/cancelled/", 1)
    return key


def current_key_from_cancelled(storage_key: str) -> str:
    key = str(storage_key or "")
    if "/current/" in key:
        return key
    if "/cancelled/" in key:
        return key.replace("/cancelled/", "/current/", 1)
    return key


def upload_original_key(
    archive_date,
    upload_id: str,
    *,
    cancelled: bool = False,
    brand: str = "",
    dealer: str = "",
    branch: str = "",
    upload_no: str = "",
    filename: Optional[str] = None,
) -> str:
    fname = filename or archive_filename(
        brand, dealer, branch, upload_no or upload_id, MODULE_LABEL_UPLOAD, "xlsx"
    )
    return _lifecycle_file_key(
        MODULE_UPLOADS,
        archive_date,
        LIFECYCLE_CANCELLED if cancelled else LIFECYCLE_CURRENT,
        fname,
    )


def product_history_products_key(
    archive_date,
    upload_id: str,
    *,
    cancelled: bool = False,
    brand: str = "",
    dealer: str = "",
    branch: str = "",
    upload_no: str = "",
    filename: Optional[str] = None,
) -> str:
    fname = filename or archive_filename(
        brand, dealer, branch, upload_no or upload_id, MODULE_LABEL_PRODUCT_HISTORY, "jsonl.gz"
    )
    return _lifecycle_file_key(
        MODULE_PRODUCT_HISTORY,
        archive_date,
        LIFECYCLE_CANCELLED if cancelled else LIFECYCLE_CURRENT,
        fname,
    )


def product_hub_products_key(archive_date, upload_id: str, *, cancelled: bool = False, **kwargs) -> str:
    """Alias — publish archives use Product History path/naming."""
    return product_history_products_key(archive_date, upload_id, cancelled=cancelled, **kwargs)


def product_history_companion_key(
    archive_date,
    upload_id: str,
    name: str,
    *,
    cancelled: bool = False,
    brand: str = "",
    dealer: str = "",
    branch: str = "",
    upload_no: str = "",
) -> str:
    stem = archive_filename(
        brand, dealer, branch, upload_no or upload_id, MODULE_LABEL_PRODUCT_HISTORY, "jsonl.gz"
    )
    if stem.endswith(".jsonl.gz"):
        stem = stem[: -len(".jsonl.gz")]
    companion = str(name or "companion").split("/")[-1]
    return _lifecycle_file_key(
        MODULE_PRODUCT_HISTORY,
        archive_date,
        LIFECYCLE_CANCELLED if cancelled else LIFECYCLE_CURRENT,
        f"{stem}_{companion}",
    )


def product_hub_companion_key(archive_date, upload_id: str, name: str, *, cancelled: bool = False, **kwargs) -> str:
    return product_history_companion_key(archive_date, upload_id, name, cancelled=cancelled, **kwargs)


def order_package_key(
    archive_date,
    order_id: str,
    *,
    cancelled: bool = False,
    brand: str = "",
    dealer: str = "",
    branch: str = "",
    order_number: str = "",
    filename: Optional[str] = None,
) -> str:
    fname = filename or archive_filename(
        brand, dealer, branch, order_number or order_id, MODULE_LABEL_ORDER, "jsonl.gz"
    )
    return _entity_folder(
        MODULE_ORDERS,
        archive_date,
        LIFECYCLE_CANCELLED if cancelled else LIFECYCLE_CURRENT,
        order_id,
        fname,
    )


def request_package_key(
    archive_date,
    request_id: str,
    *,
    cancelled: bool = False,
    brand: str = "",
    dealer: str = "",
    branch: str = "",
    request_number: str = "",
    filename: Optional[str] = None,
) -> str:
    fname = filename or archive_filename(
        brand, dealer, branch, request_number or request_id, MODULE_LABEL_REQUEST, "jsonl.gz"
    )
    return _entity_folder(
        MODULE_REQUESTS,
        archive_date,
        LIFECYCLE_CANCELLED if cancelled else LIFECYCLE_CURRENT,
        request_id,
        fname,
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
