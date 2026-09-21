"""DocumentDB-safe testing snapshot helpers.

Amazon DocumentDB does not support collection rename. This module therefore
never rename/swaps collections. Staging collections are written, validated,
then copied into the live collections with a new snapshot_version. The
previous snapshot stays queryable until the new version is marked active.

The snapshot is a full read-only mirror of production `nmts` collections into
`nmts_testing`. Production is never written. S3 `dev/` objects are not copied.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

TESTING_MASTER_EMAIL = "testing.master@sleepingstock.in"

# Master data still validated for Brand/Dealer/Branch mapping. All other
# production collections are also mirrored; see discover_mirror_collections().
REFERENCE_COLLECTIONS = (
    "brands",
    "dealers",
    "branches",
    "states",
    "groups",
    "products",
    "batch_summaries",
    "templates",
)
# Backwards-compatible alias used by older tests and docs.
APPROVED_COLLECTIONS = REFERENCE_COLLECTIONS

SKIP_MIRROR_PREFIXES = ("system.", "snap_ingest_")

# Staging / system only. Application collections (users, orders, uploads, …)
# are included in the full mirror and are no longer forbidden.
FORBIDDEN_COLLECTIONS: set[str] = set()

# Explicit unique/identity keys used when activating ingest into live testing
# collections. Unknown collections fall back to `id` then `_id`.
UNIQUE_ACTIVATE = {
    "brands": ("name",),
    "dealers": ("name", "brand"),
    "branches": ("name", "dealer", "brand"),
    "states": ("name",),
    "groups": ("name",),
    "products": ("id",),
    "batch_summaries": ("brand_name", "dealer_name", "branch", "active_date_key"),
    "templates": ("id",),
    "users": ("id",),
    "counters": ("_id",),
    "uploads": ("id",),
    "upload_items": ("id",),
    "order_headers": ("id",),
    "order_items": ("id",),
    "order_requests": ("id",),
    "request_headers": ("id",),
    "orders": ("id",),
    "order_stocks": ("id",),
    "order_activity": ("id",),
    "order_cancellation_requests": ("id",),
    "activity_logs": ("id",),
    "user_alerts": ("id",),
    "notifications": ("id",),
    "notification_logs": ("id",),
    "notices": ("id",),
    "notice_attachments": ("file_id",),
    "notice_user_status": ("notice_id", "user_id"),
    "notice_audit_logs": ("id",),
    "queries": ("query_no",),
    "query_attachments": ("file_id",),
    "old_report_requests": ("request_number",),
    "old_report_downloads": ("id",),
    "analytics_stock_daily_snapshots": (
        "snapshot_date_ist", "brand_id", "dealer_id", "branch_id", "part_number",
    ),
    "archive_job_locks": ("lock_key",),
    "archive_manifests": ("archive_id",),
    "archive_outbox": ("job_id",),
    "archive_runs": ("run_id",),
    "auto_perpetual_assignments": ("allocation_date", "branch", "mobile_user_id", "part_number"),
    "auto_perpetual_branch_planner": ("month_key", "branch", "brand_name", "dealer_name"),
    "auto_perpetual_daily_runs": ("allocation_date", "branch", "brand_name", "dealer_name"),
    "auto_perpetual_pool": ("month_key", "branch", "part_number", "coverage_kind"),
    "auto_perpetual_suggestions": ("suggestion_number",),
    "mobile_app_versions": ("version_code",),
    "mobile_devices": ("device_id",),
    "mobile_notification_actions": ("request_id", "mobile_user_id"),
    "mobile_pairing_codes": ("pairing_token_hash",),
    "mobile_request_group_locks": ("request_group_key",),
    "mobile_request_push_claims": ("request_group_key", "kind"),
    "mobile_sessions": ("session_token_hash",),
    "mobile_user_attendance": ("attendance_date", "mobile_user_id"),
    "mobile_users": ("mobile_user_id",),
    "mobile_audit_logs": ("id",),
    "mobile_push_delivery_logs": ("id",),
    "source_rejection_freezes": ("freeze_key",),
    "stock_reservations": ("id",),
    "stock_verification_history": ("device_id", "client_id"),
    "stock_verification_sessions": ("session_id",),
    "stock_verifications": ("id",),
    "storage_usage_daily": ("date_key", "brand", "dealer", "branch", "module"),
    "storage_usage_events": ("id",),
    "order_archive_index": ("id",),
    "request_archive_index": ("id",),
}

REQUIRED_FIELDS = {
    "brands": ("name",),
    "dealers": ("name",),
    "branches": ("name",),
    "states": ("name",),
    "groups": ("name",),
    "products": ("part_number", "brand_name", "dealer_name", "branch", "active_date_key"),
    "batch_summaries": ("brand_name", "dealer_name", "branch", "active_date_key"),
    "templates": ("id",),
}

PRODUCT_IDENTITY = ("brand_name", "dealer_name", "branch", "part_number", "loc")
MASTER_IDENTITY = {
    "brands": ("name",),
    "dealers": ("name", "brand"),
    "branches": ("name", "dealer", "brand"),
    "states": ("name",),
    "groups": ("name",),
    "batch_summaries": ("brand_name", "dealer_name", "branch", "active_date_key"),
    "templates": ("id",),
}

PRESERVE_TARGET_COLLECTIONS = (
    "users",
    "counters",
    "orders",
    "order_requests",
    "request_headers",
    "uploads",
    "upload_items",
)

# Explicit module list so Testing Master Admin stays at parity even if some
# UI checks the permissions array instead of role === master.
MASTER_PERMISSION_LABELS = (
    "User Hub",
    "Upload Center",
    "Product Hub",
    "Product Hub History",
    "Order Desk",
    "Request Center",
    "Reports",
    "Analytics",
    "Storage & Cost Monitor",
    "Notice Board",
    "Query Desk",
    "Sleeping Stock Mobile",
    "Stock Audit",
)


def should_skip_collection(name: str) -> bool:
    text = str(name or "")
    return text.startswith(SKIP_MIRROR_PREFIXES)


def discover_mirror_collections(source_db) -> list[str]:
    names = list(source_db.list_collection_names())
    discovered = [n for n in names if not should_skip_collection(n)]
    # Keep a stable order: reference collections first, then the rest.
    head = [n for n in REFERENCE_COLLECTIONS if n in discovered]
    tail = sorted(n for n in discovered if n not in REFERENCE_COLLECTIONS)
    return head + tail


def is_protected_user(doc: Mapping[str, Any] | None) -> bool:
    if not doc:
        return False
    email = str(doc.get("email") or "").strip().casefold()
    return email == TESTING_MASTER_EMAIL.casefold() or bool(doc.get("is_testing_master"))


def activation_identity(collection: str, doc: Mapping[str, Any] | None = None) -> tuple[str, ...]:
    configured = UNIQUE_ACTIVATE.get(collection)
    if configured:
        return configured
    if doc and doc.get("id") not in (None, ""):
        return ("id",)
    return ("_id",)


def keeps_original_id(collection: str, doc: Mapping[str, Any] | None = None) -> bool:
    return "_id" in activation_identity(collection, doc)


def ingest_collection_name(collection: str, version: str) -> str:
    safe = "".join(ch if ch.isalnum() else "_" for ch in version)[:40]
    return f"snap_ingest_{collection}_{safe}"


def stamp_snapshot_doc(
    doc: Mapping[str, Any],
    version: str,
    copied_at: str,
    business_date: str,
    *,
    keep_id: bool = False,
) -> dict[str, Any]:
    payload = dict(doc)
    if not keep_id:
        payload.pop("_id", None)
    payload["data_origin"] = "snapshot"
    payload["is_snapshot_reference"] = True
    payload["snapshot_version"] = version
    payload["snapshot_source"] = "nmts"
    payload["snapshot_copied_at"] = copied_at
    payload["snapshot_business_date"] = business_date
    return payload


def missing_required_fields(collection: str, docs: Iterable[Mapping[str, Any]]) -> list[str]:
    required = REQUIRED_FIELDS.get(collection, ())
    missing: list[str] = []
    for idx, doc in enumerate(docs):
        for field in required:
            if doc.get(field) in (None, ""):
                missing.append(f"{collection}[{idx}].{field}")
                if len(missing) >= 20:
                    return missing
    return missing


def duplicate_identity_keys(collection: str, docs: Iterable[Mapping[str, Any]]) -> list[str]:
    if collection == "products":
        fields = PRODUCT_IDENTITY
    else:
        fields = MASTER_IDENTITY.get(collection)
    if not fields:
        return []
    seen: set[tuple] = set()
    dupes: list[str] = []
    for doc in docs:
        key = tuple(str(doc.get(field) or "").strip().casefold() for field in fields)
        if key in seen:
            dupes.append("|".join(key))
            if len(dupes) >= 20:
                return dupes
        seen.add(key)
    return dupes


def mapping_errors(docs: Iterable[Mapping[str, Any]], brands: set[str], dealers: set[str], branches: set[str], collection: str = "") -> list[str]:
    errors: list[str] = []
    brand_l = {x.casefold() for x in brands}
    dealer_l = {x.casefold() for x in dealers}
    branch_l = {x.casefold() for x in branches}
    for doc in docs:
        brand = str(doc.get("brand_name") or doc.get("brand") or "").strip()
        dealer = str(doc.get("dealer_name") or doc.get("dealer") or "").strip()
        if collection == "branches":
            branch = str(doc.get("name") or doc.get("branch") or "").strip()
        else:
            branch = str(doc.get("branch") or "").strip()
        if collection == "dealers":
            dealer = str(doc.get("name") or dealer).strip()
            branch = ""
        if collection == "brands":
            brand = str(doc.get("name") or brand).strip()
            dealer = ""
            branch = ""
        if brand and brand_l and brand.casefold() not in brand_l:
            errors.append(f"unknown brand {brand}")
        if dealer and dealer_l and dealer.casefold() not in dealer_l:
            errors.append(f"unknown dealer {dealer}")
        if branch and branch_l and branch.casefold() not in branch_l:
            errors.append(f"unknown branch {branch}")
        if len(errors) >= 20:
            return errors
    return errors


def activation_filter(collection: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    fields = activation_identity(collection, payload)
    filt: dict[str, Any] = {}
    for field in fields:
        value = payload.get(field)
        if value in (None, "") and field == "brand":
            value = payload.get("brand_name")
        if value in (None, "") and field == "dealer":
            value = payload.get("dealer_name")
        if value in (None, "") and field == "id":
            value = payload.get("id") or payload.get("upload_id")
        filt[field] = value
    return filt


def testing_master_parity_fields() -> dict[str, Any]:
    return {
        "username": "Testing Master Admin",
        "name": "Testing Master Admin",
        "role": "master",
        "status": "active",
        "permissions": list(MASTER_PERMISSION_LABELS),
        "brand": "",
        "group": "",
        "location": "",
        "state": "",
        "dealer": "",
        "branch": "",
        "data_origin": "testing",
        "is_snapshot_reference": False,
        "is_testing_master": True,
    }


class SourceWriteBlocked(RuntimeError):
    pass


class ReadOnlyDatabase:
    """Proxy that allows find/count/distinct/aggregate only."""

    _WRITE_METHODS = {
        "insert_one", "insert_many", "update_one", "update_many", "replace_one",
        "delete_one", "delete_many", "find_one_and_update", "find_one_and_replace",
        "find_one_and_delete", "bulk_write", "drop", "rename", "create_index",
        "create_indexes", "drop_index", "drop_indexes",
    }

    def __init__(self, db):
        self._db = db

    def __getattr__(self, name: str):
        if name in self._WRITE_METHODS:
            raise SourceWriteBlocked(f"Refusing source write: {name}")
        value = getattr(self._db, name)
        if name in {"get_collection", "command"}:
            return value
        if hasattr(value, "find"):
            return ReadOnlyCollection(value)
        return value

    def __getitem__(self, name: str):
        return ReadOnlyCollection(self._db[name])

    def list_collection_names(self, *args, **kwargs):
        return self._db.list_collection_names(*args, **kwargs)


class ReadOnlyCollection:
    def __init__(self, collection):
        self._collection = collection

    def __getattr__(self, name: str):
        if name in ReadOnlyDatabase._WRITE_METHODS:
            raise SourceWriteBlocked(f"Refusing source write on {self._collection.name}: {name}")
        return getattr(self._collection, name)
