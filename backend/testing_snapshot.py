"""DocumentDB-safe testing snapshot helpers.

Amazon DocumentDB does not support collection rename. This module therefore
never rename/swaps collections. Staging collections are written, validated,
then copied into the live collections with a new snapshot_version. The
previous snapshot stays queryable until the new version is marked active.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

APPROVED_COLLECTIONS = (
    "brands",
    "dealers",
    "branches",
    "states",
    "groups",
    "products",
    "batch_summaries",
    "templates",
)

FORBIDDEN_COLLECTIONS = {
    "users",
    "counters",
    "sessions",
    "tokens",
    "orders",
    "order_requests",
    "request_headers",
    "uploads",
    "upload_items",
    "notification_logs",
    "archive_manifest",
    "archive_outbox",
    "archive_runs",
    "mobile_users",
    "mobile_devices",
    "mobile_pairing_codes",
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


def ingest_collection_name(collection: str, version: str) -> str:
    safe = "".join(ch if ch.isalnum() else "_" for ch in version)[:40]
    return f"snap_ingest_{collection}_{safe}"


def stamp_snapshot_doc(doc: Mapping[str, Any], version: str, copied_at: str, business_date: str) -> dict[str, Any]:
    payload = {k: v for k, v in dict(doc).items() if k != "_id"}
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


def mapping_errors(docs: Iterable[Mapping[str, Any]], brands: set[str], dealers: set[str], branches: set[str]) -> list[str]:
    errors: list[str] = []
    brand_l = {x.casefold() for x in brands}
    dealer_l = {x.casefold() for x in dealers}
    branch_l = {x.casefold() for x in branches}
    for doc in docs:
        brand = str(doc.get("brand_name") or doc.get("brand") or "").strip()
        dealer = str(doc.get("dealer_name") or doc.get("dealer") or "").strip()
        branch = str(doc.get("branch") or doc.get("name") or "").strip()
        if brand and brand_l and brand.casefold() not in brand_l:
            errors.append(f"unknown brand {brand}")
        if dealer and dealer_l and dealer.casefold() not in dealer_l:
            errors.append(f"unknown dealer {dealer}")
        if branch and branch_l and branch.casefold() not in branch_l:
            errors.append(f"unknown branch {branch}")
        if len(errors) >= 20:
            return errors
    return errors


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
