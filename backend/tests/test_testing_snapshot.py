"""Snapshot validation helpers — no live DocumentDB."""
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from testing_snapshot import (
    APPROVED_COLLECTIONS,
    FORBIDDEN_COLLECTIONS,
    ReadOnlyCollection,
    SourceWriteBlocked,
    duplicate_identity_keys,
    ingest_collection_name,
    mapping_errors,
    missing_required_fields,
    stamp_snapshot_doc,
)


class _Fake:
    name = "products"

    def insert_one(self, *_a, **_k):
        return None


def test_approved_set_never_includes_secrets_or_history():
    overlap = set(APPROVED_COLLECTIONS) & FORBIDDEN_COLLECTIONS
    assert not overlap
    assert "users" in FORBIDDEN_COLLECTIONS
    assert "counters" in FORBIDDEN_COLLECTIONS
    assert "products" in APPROVED_COLLECTIONS


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
