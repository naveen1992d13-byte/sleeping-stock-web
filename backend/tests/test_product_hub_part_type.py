"""Focused Product Hub Part Type filter tests."""
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import server as srv


def test_all_part_type_does_not_narrow_scoped_query():
    query = {
        "publish_status": "Published",
        "brand_name": "Hyundai",
        "dealer_name": "FPL Automobiles",
        "branch": "Koyambedu",
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
        "dealer_name": "FPL Automobiles",
        "branch": "Koyambedu",
    }
    srv._apply_category_filter(query, "Battery")
    assert "$and" in query
    scoped = query["$and"][0]
    assert scoped["brand_name"] == "Hyundai"
    assert scoped["dealer_name"] == "FPL Automobiles"
    assert scoped["branch"] == "Koyambedu"
    assert scoped["publish_status"] == "Published"
    clause = query["$and"][1]
    fields = {list(item.keys())[0] for item in clause["$or"]}
    assert fields == {"part_category", "category", "parts_type"}
    assert all(item[next(iter(item))]["$regex"] == "^Battery$" for item in clause["$or"])


def test_dropdown_keeps_actual_stored_part_types():
    available = srv._distinct_part_types_from_raw([
        "Battery",
        "Oil/Lubricants",
        "Others",
        "OE Parts",
        "",
        None,
        "battery",
    ])
    assert available == ["Battery", "OE Parts", "Oil/Lubricants", "Others"]
    assert "Accessories" not in available
    assert available[0] == "Battery"


def test_product_hub_part_types_route_is_registered():
    paths = [getattr(route, "path", "") for route in srv.api_router.routes]
    assert "/api/product-hub/part-types" in paths
