"""Stage escalation after a source has already accepted (pure unit, no HTTP)."""
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import order_desk_workflow as odw  # noqa: E402

ORDER = {'brand_name': 'Hyundai', 'dealer_name': 'FPL', 'branch': 'Vanagaram'}


def _src(dealer, branch, qty):
    return {'dealer_name': dealer, 'branch': branch, 'available_qty': qty, 'net_available_qty': qty,
            'purchase_aging_days': 200, 'aging_days': 200}


def _item(allocations, remaining=5.0):
    return {
        'part_number': 'QAESC001', 'required_qty': 10, 'remaining_qty': remaining, 'accepted_qty': 10 - remaining,
        'same_dealer_sources': [_src('FPL', 'Vanagaram', 2), _src('FPL', 'Koyambedu', 3)],
        'other_dealer_sources': [_src('KUN', 'Chrompet', 20)],
        'allocations': allocations,
    }


def _alloc(dealer, branch, qty, status, request_no='TS-RQ1'):
    return {'dealer_name': dealer, 'branch': branch, 'request_qty': qty, 'status': status,
            'request_no': request_no, 'request_number': request_no}


def test_accepted_own_and_branch_sources_open_dealer_stage():
    item = _item([
        _alloc('FPL', 'Vanagaram', 2, odw.REQUEST_STATUS_ACCEPTED, 'TS-RQ1'),
        _alloc('FPL', 'Koyambedu', 3, odw.REQUEST_STATUS_ACCEPTED, 'TS-RQ2'),
    ])
    flags = odw.compute_stage_flags(item, ORDER, set())
    assert flags['own_stage_status'] == 'exhausted'
    assert flags['branch_stage_status'] == 'exhausted'
    assert flags['dealer_stage_status'] == 'open'
    assert flags['active_stage'] == 'dealer'
    assert flags['next_source_allowed'] is True
    assert odw.eligible_pool(item, ORDER, 'dealer', set(), 'purchase', 0)


def test_accepted_own_source_opens_branch_stage():
    item = _item([_alloc('FPL', 'Vanagaram', 2, odw.REQUEST_STATUS_ACCEPTED)], remaining=8.0)
    flags = odw.compute_stage_flags(item, ORDER, set())
    assert flags['own_stage_status'] == 'exhausted'
    assert flags['branch_stage_status'] == 'open'
    assert flags['active_stage'] == 'branch'


def test_pending_request_keeps_stage_locked():
    item = _item([_alloc('FPL', 'Vanagaram', 2, odw.REQUEST_STATUS_AWAITING)], remaining=8.0)
    flags = odw.compute_stage_flags(item, ORDER, set())
    assert flags['own_stage_status'] == 'open'
    assert flags['next_source_allowed'] is False


def test_rejected_or_cancelled_source_not_treated_as_accepted():
    item = _item([_alloc('FPL', 'Vanagaram', 2, odw.REQUEST_STATUS_REJECTED)], remaining=10.0)
    assert odw.accepted_source_keys(item) == set()
    item = _item([_alloc('FPL', 'Vanagaram', 2, odw.REQUEST_STATUS_CANCEL_NO_RESP)], remaining=10.0)
    assert odw.accepted_source_keys(item) == set()


def test_unsent_draft_allocation_not_excluded():
    item = _item([{'dealer_name': 'FPL', 'branch': 'Koyambedu', 'request_qty': 3, 'status': odw.REQUEST_STATUS_READY}])
    assert odw.accepted_source_keys(item) == set()


def test_completed_logistics_status_still_excluded():
    item = _item([_alloc('FPL', 'Koyambedu', 3, 'Completed')])
    assert odw.accepted_source_keys(item) == {('fpl', 'koyambedu')}
