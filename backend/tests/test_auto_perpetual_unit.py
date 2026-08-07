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


def test_branch_code():
    assert len(branch_code("Main Branch")) == 3
