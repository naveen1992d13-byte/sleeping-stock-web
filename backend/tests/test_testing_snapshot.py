"""Snapshot validation helpers — no live DocumentDB."""
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from testing_snapshot import (
    APPROVED_COLLECTIONS,
    FORBIDDEN_COLLECTIONS,
    MASTER_PERMISSION_LABELS,
    REFERENCE_COLLECTIONS,
    ReadOnlyCollection,
    SourceWriteBlocked,
    activation_identity,
    discover_mirror_collections,
    duplicate_identity_keys,
    ingest_collection_name,
    is_protected_user,
    mapping_errors,
    missing_required_fields,
    should_skip_collection,
    stamp_snapshot_doc,
)


class _Fake:
    name = "products"

    def insert_one(self, *_a, **_k):
        return None


class _FakeDB:
    def list_collection_names(self):
        return [
            "brands", "products", "users", "orders", "uploads", "upload_items",
            "request_headers", "system.indexes", "snap_ingest_products_x",
        ]


def test_mirror_includes_operational_collections():
    assert not (set(REFERENCE_COLLECTIONS) & FORBIDDEN_COLLECTIONS)
    assert "products" in APPROVED_COLLECTIONS
    assert "users" not in FORBIDDEN_COLLECTIONS
    assert "counters" not in FORBIDDEN_COLLECTIONS
    assert should_skip_collection("system.indexes")
    assert should_skip_collection("snap_ingest_products_x")
    assert not should_skip_collection("users")
    assert not should_skip_collection("request_headers")
    names = discover_mirror_collections(_FakeDB())
    assert "users" in names
    assert "uploads" in names
    assert "system.indexes" not in names
    assert names[0] in REFERENCE_COLLECTIONS


def test_stamp_and_required_fields():
    doc = stamp_snapshot_doc(
        {"_id": "x", "name": "Hyundai", "part_number": "A"},
        "V1",
        "2026-09-20T00:00:00+00:00",
        "20260919",
    )
    assert "_id" not in doc
    assert doc["data_origin"] == "snapshot"
    assert doc["is_snapshot_reference"] is True
    kept = stamp_snapshot_doc(
        {"_id": "counter-1", "seq": 3},
        "V1",
        "2026-09-20T00:00:00+00:00",
        "20260919",
        keep_id=True,
    )
    assert kept["_id"] == "counter-1"
    missing = missing_required_fields("products", [{"part_number": "A"}])
    assert "brand_name" in missing[0]


def test_duplicates_and_mappings():
    docs = [
        {"brand_name": "H", "dealer_name": "D", "branch": "B", "part_number": "P", "loc": "L1"},
        {"brand_name": "H", "dealer_name": "D", "branch": "B", "part_number": "P", "loc": "L1"},
    ]
    assert duplicate_identity_keys("products", docs)
    errors = mapping_errors(
        [{"brand_name": "Other", "dealer_name": "D", "branch": "B"}],
        {"H"},
        {"D"},
        {"B"},
        collection="products",
    )
    assert errors
    dealer_docs = [{"name": "FPL automobiles pvt ltd", "brand": "H"}]
    assert not mapping_errors(dealer_docs, {"H"}, {"FPL automobiles pvt ltd"}, {"Some Branch"}, collection="dealers")


def test_readonly_proxy_blocks_writes():
    wrapped = ReadOnlyCollection(_Fake())
    try:
        wrapped.insert_one({"a": 1})
        assert False, "expected SourceWriteBlocked"
    except SourceWriteBlocked:
        pass


def test_ingest_name_is_documentdb_safe():
    name = ingest_collection_name("products", "20260920T010000Z")
    assert name.startswith("snap_ingest_products_")
    assert "." not in name
    assert "$" not in name


def test_testing_master_is_protected_and_has_full_permissions():
    assert is_protected_user({"email": "testing.master@sleepingstock.in"})
    assert is_protected_user({"is_testing_master": True, "email": "other@x"})
    assert not is_protected_user({"email": "admin@sleepingstock.in", "role": "master"})
    from testing_snapshot import testing_master_parity_fields as master_parity_fields
    fields = master_parity_fields()
    assert fields["role"] == "master"
    for label in (
        "User Hub", "Upload Center", "Product Hub", "Product Hub History",
        "Order Desk", "Request Center", "Reports", "Analytics",
        "Storage & Cost Monitor", "Notice Board", "Query Desk",
        "Sleeping Stock Mobile", "Stock Audit",
    ):
        assert label in fields["permissions"]
        assert label in MASTER_PERMISSION_LABELS
    assert activation_identity("users", {"id": "abc"}) == ("id",)
    assert activation_identity("counters", {"_id": "upload"}) == ("_id",)
