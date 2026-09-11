"""Focused Product Hub Part Type filter tests."""
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from part_category import part_matches_type
import server as srv


def test_all_part_type_does_not_narrow_scoped_query():
    query = {
        "publish_status": "Published",
        "brand_name": "Hyundai",
        "dealer_name": "FPL Hyundai",
        "branch": "Vanagaram",
    }
    original = dict(query)
    srv._apply_category_filter(query, "All")
    assert query == original
    srv._apply_category_filter(query, None)
    assert query == original


def test_part_type_filter_keeps_brand_dealer_branch_scope():
    query = {
        "publish_status": "Published",
        "brand_name": "Hyundai",
        "dealer_name": "FPL Hyundai",
        "branch": "Vanagaram",
    }
    srv._apply_category_filter(query, "OE Parts")
    assert "$and" in query
    scoped = query["$and"][0]
    assert scoped["brand_name"] == "Hyundai"
    assert scoped["dealer_name"] == "FPL Hyundai"
    assert scoped["branch"] == "Vanagaram"
    assert scoped["publish_status"] == "Published"
    clause = query["$and"][1]
    assert "$or" in clause
    assert part_matches_type({"part_category": "Genuine Parts"}, "OE Parts")
    assert part_matches_type({"part_category": "OE Parts"}, "OE Parts")
    assert not part_matches_type({"part_category": "Accessories"}, "OE Parts")


def test_scoped_part_types_use_canonical_labels_only():
    available = srv._canonical_part_types_from_raw([
        "Genuine Parts",
        "OE Parts",
        "Accessories",
        "Non OEM parts",
        "Others",
        "",
        None,
        "Random Custom",
    ])
    assert available == ["Accessories", "OE Parts", "Others"]


def test_product_hub_part_types_route_is_registered():
    paths = [getattr(route, "path", "") for route in srv.api_router.routes]
    assert "/api/product-hub/part-types" in paths
