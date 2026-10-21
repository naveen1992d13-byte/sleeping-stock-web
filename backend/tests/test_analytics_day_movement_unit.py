import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import analytics_center as ac


def _row(qty, unit):
    return {"qty": qty, "unit": unit, "value": qty * unit, "part_number": "P"}


def test_removed_part_is_valued_at_previous_unit_price():
    prev = {("b", "d", "br", "old"): _row(10, 50.0), ("b", "d", "br", "kept"): _row(2, 100.0)}
    curr = {("b", "d", "br", "kept"): _row(2, 100.0), ("b", "d", "br", "new"): _row(3, 20.0)}
    mv = ac._compute_day_movement(prev, curr, "purchase", "value")
    assert mv["reduced_qty"] == 10 and mv["reduced_value"] == 500.0
    assert mv["added_qty"] == 3 and mv["added_value"] == 60.0
    assert mv["opening_value"] + mv["added_value"] - mv["reduced_value"] == mv["closing_value"]


def test_received_transfer_counts_as_nmts_sourced():
    row = {"status": "Received", "requesting_branch": "Koyambedu", "supplying_branch": "Vanagaram",
           "approved_qty": 2, "part_value": 450}
    qty, value = ac._nmts_sourced_qty_value(row)
    assert qty == 2 and value > 0
    assert ac._nmts_sourced_qty_value({**row, "status": "Dispatched"}) == (0.0, 0.0)
