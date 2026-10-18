"""Unit tests for Phase 2 Reports/Analytics/profile helpers. No live data writes."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import reports_center as rc  # noqa: E402
import analytics_center as ac  # noqa: E402


def test_first_present_skips_none_and_empty():
    assert rc.first_present(None, "", "  ", "ok") == "ok"
    assert rc.first_present(None, 0) == 0
    assert rc.first_present(None, "", None) is None
    assert rc._num_alias(None, "", "12") == 12.0
    assert rc._text(None, "  ", "Alpha") == "Alpha"


def test_request_identity_never_concatenates_null():
    ident = rc._request_identity({"id": "abc", "request_number": None, "part_number": "P1"})
    assert ident == ("abc", "", "P1")
    ident2 = rc._request_identity({"request_number": None, "part_number": None})
    assert ident2 == ("", "", "")


def test_missed_opportunity_terminal_only():
    inventory = [{
        "brand_name": "Hyundai",
        "dealer_name": "FPL",
        "branch": "KUN",
        "part_number": "P1",
        "qty": 5,
        "unit": 10,
        "loc": "A1",
        "pa": 12,
        "sa": 20,
    }]
    open_req = {
        "id": "r1",
        "request_number": "RN-1",
        "part_number": "P1",
        "status": "Requested",
        "requested_qty": 4,
        "accepted_qty": 0,
        "supplying_brand": "Hyundai",
        "supplying_dealer": "FPL",
        "supplying_branch": "KUN",
    }
    rejected = {**open_req, "id": "r2", "request_number": "RN-2", "status": "Rejected"}
    assert rc._missed([open_req], inventory) == []
    missed = rc._missed([rejected], inventory)
    assert len(missed) == 1
    assert missed[0]["unfulfilled"] == 4
    assert missed[0]["eligible"] == 4


def test_unknown_aging_bucket_reports_and_analytics():
    assert rc._bucket(None) == "Unknown"
    assert rc._bucket("") == "Unknown"
    assert rc._bucket(-1) == "Unknown"
    assert ac._analytics_bucket(None) == "Unknown"
    assert ac._analytics_bucket("") == "Unknown"
    assert "Unknown" in ac.ANALYTICS_CHART_BUCKETS
    assert ac.ANALYTICS_CHART_BUCKETS[-1] == "Unknown"
    assert ac.ANALYTICS_CHART_BUCKETS[0] != "Unknown"


def test_stock_movement_disabled_message():
    headers, data = rc._rows("stock-movement", [], [], [], [])
    assert headers == ["Message"]
    assert data == [[rc.STOCK_MOVEMENT_UNAVAILABLE]]


def test_workbook_skips_unit_value_totals(tmp_path=None):
    headers = ["Part Number", "Available Quantity", "Unit Value", "Total Value", "MAV", "Part Value"]
    data = [["P1", 2, 10, 20, 10, 10], ["P2", 3, 5, 15, 5, 5]]
    wb = rc._workbook("Inventory Report", "01", headers, data, {
        "from": "2026-10-01", "to": "2026-10-15", "generated": "now", "user": "qa",
        "brand": "All Brands", "dealer": "All Dealers", "branch": "All Branches",
    })
    ws = wb.active
    # header row is after meta; find TOTAL row
    total_row = None
    for row in ws.iter_rows(min_row=1, max_row=20, max_col=1):
        if row[0].value == "TOTAL":
            total_row = row[0].row
            break
    assert total_row
    formulas = [ws.cell(total_row, i).value for i in range(1, 7)]
    assert formulas[1] and str(formulas[1]).startswith("=SUM")  # quantity
    assert formulas[2] is None  # Unit Value not summed
    assert formulas[3] and str(formulas[3]).startswith("=SUM")  # Total Value
    assert formulas[4] is None  # MAV
    assert formulas[5] is None  # Part Value


def test_password_policy_helper_exists():
    src = (ROOT / "server.py").read_text(encoding="utf-8")
    assert "def validate_password_policy" in src
    assert "password_changed_at" in src
    assert 'to_encode.update({"exp": expire, "iat": int(now.timestamp())})' in src
