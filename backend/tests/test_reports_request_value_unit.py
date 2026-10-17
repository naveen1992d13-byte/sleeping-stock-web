import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import reports_center as rc


def test_request_unit_reads_price_captured_at_request():
    assert rc._request_unit({"unit_value_at_request": 300.0}) == 300.0
    assert rc._request_unit({"part_value": 10, "unit_value_at_request": 300.0}) == 10.0


def test_received_and_completed_transfers_are_done():
    assert rc._is_transfer_done({"status": "Received"})
    assert rc._is_transfer_done({"status": "Completed"})
    assert not rc._is_transfer_done({"status": "Dispatched"})
