"""Part Type options shared by Product Hub + Analytics."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from part_category import (  # noqa: E402
    PART_TYPE_OPTIONS,
    canonical_part_type,
    is_all_part_type,
    normalize_part_category,
    part_matches_type,
    part_type_mongo_clause,
)


def test_final_part_type_options():
    assert PART_TYPE_OPTIONS == ["OE Parts", "Accessories", "Others"]


def test_normalize_legacy_and_final_labels():
    assert normalize_part_category("Genuine Parts") == "OE Parts"
    assert normalize_part_category("OE Parts") == "OE Parts"
    assert normalize_part_category("Accessories") == "Accessories"
    assert normalize_part_category("Non OEM parts") == "Others"
    assert normalize_part_category("Others") == "Others"


def test_all_part_type():
    assert is_all_part_type("All") is True
    assert is_all_part_type("All Categories") is True
    assert is_all_part_type(None) is True
    assert is_all_part_type("OE Parts") is False


def test_oe_parts_filter_matches_genuine():
    clause = part_type_mongo_clause("OE Parts")
    assert clause and "$or" in clause
    assert part_matches_type({"part_category": "Genuine Parts"}, "OE Parts")
    assert part_matches_type({"part_category": "OE Parts"}, "OE Parts")
    assert not part_matches_type({"part_category": "Accessories"}, "OE Parts")


def test_others_filter_matches_non_oem_and_others():
    assert part_matches_type({"part_category": "Non OEM parts"}, "Others")
    assert part_matches_type({"part_category": "Others"}, "Others")
    assert not part_matches_type({"part_category": "Genuine Parts"}, "Others")


def test_accessories_only():
    assert part_matches_type("Accessories", "Accessories")
    assert not part_matches_type("OE Parts", "Accessories")


def test_canonical_part_type():
    assert canonical_part_type("All") is None
    assert canonical_part_type("Genuine Parts") == "OE Parts"
    assert canonical_part_type("Non OEM parts") == "Others"
