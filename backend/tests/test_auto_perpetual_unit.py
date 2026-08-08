"""Unit tests for Auto Perpetual helpers (no MongoDB required)."""
from datetime import datetime
from zoneinfo import ZoneInfo

from auto_perpetual import _loc_group, _working_days_left_in_month, month_key, branch_code, inventory_date_key


def test_loc_group():
    assert _loc_group("11A01234") == "11A0"
    assert _loc_group("") == "UNKNOWN"


def test_working_days_left():
    ist = ZoneInfo("Asia/Kolkata")
    d = datetime(2026, 8, 7, tzinfo=ist)
    assert _working_days_left_in_month(d) == 25


def test_month_key():
    assert month_key(datetime(2026, 3, 15, tzinfo=ZoneInfo("Asia/Kolkata"))) == "2026-03"


def test_inventory_date_key():
    assert inventory_date_key(datetime(2026, 8, 7, tzinfo=ZoneInfo("Asia/Kolkata"))) == "20260807"


def test_split_mix_20():
    from auto_perpetual_suggestions import _split_mix

    loc, inc, dec = _split_mix(20)
    assert loc + inc + dec == 20
    assert loc == 10 and inc == 5 and dec == 5


def test_select_dedupes():
    from auto_perpetual_suggestions import select_items_502525

    eligible = [
        {"part_number": "A", "system_qty": 1, "loc": "A-R01-B01", "movement": "location"},
        {"part_number": "B", "system_qty": 2, "loc": "A-R01-B02", "movement": "qty_increased"},
        {"part_number": "C", "system_qty": 3, "loc": "A-R01-B03", "movement": "qty_decreased"},
    ]
    picked = select_items_502525(eligible, 3, set())
    parts = [p["part_number"] for p in picked]
    assert len(parts) == len(set(parts))
