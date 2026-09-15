"""Focused test: rejected qty is excluded from active/requestable quantity."""
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import order_desk_workflow as odw


def test_rejected_qty_excluded_next_branch_request_allowed():
    required = 1
    rejected_alloc = {
        'dealer_name': 'KUN',
        'branch': 'Branch A',
        'request_qty': 1,
        'request_no': 'RQ-A',
        'status': 'Rejected',
        'request_status': 'Rejected',
    }
    item = {
        'id': 'i1',
        'required_qty': required,
        'allocations': [{
            **rejected_alloc,
            'status': 'Awaiting Response',
            'request_status': 'Awaiting Response',
        }],
    }
    order = {'dealer_name': 'KUN', 'branch': 'Own'}
    live_req = {
        'id': 'r1',
        'status': 'Requested',
        'requested_qty': 1,
        'accepted_qty': 0,
        'supplying_dealer': 'KUN',
        'supplying_branch': 'Branch A',
        'request_number': 'RQ-A',
        'approval_remarks': '',
    }

    live = odw.compute_item_workflow(item, order, [live_req])
    assert live['remaining_qty'] == 0
    assert live['allocated_qty'] == 1

    rejected_item = {**item, 'allocations': [rejected_alloc]}
    rejected_req = {
        **live_req,
        'status': 'Rejected',
        'approval_remarks': 'No stock today',
    }
    rejected = odw.compute_item_workflow(rejected_item, order, [rejected_req])
    assert rejected['remaining_qty'] == 1
    assert rejected['allocated_qty'] == 0
    assert odw.sum_active_allocation_qty(rejected_item['allocations']) == 0
    assert rejected['request_history'][0]['request_status'] == 'Rejected'
    assert rejected['request_history'][0]['remarks'] == 'No stock today'
    assert len(rejected_item['allocations']) == 1

    next_alloc = {
        'dealer_name': 'Other Dealer',
        'branch': 'Branch B',
        'request_qty': 1,
        'status': odw.REQUEST_STATUS_READY,
        'request_status': odw.REQUEST_STATUS_READY,
    }
    cleaned = [rejected_alloc, next_alloc]
    assert odw.sum_active_allocation_qty(cleaned) == 1
    assert odw.request_qty_exceeds_required(cleaned, required) is False

    over = [rejected_alloc, {**next_alloc, 'request_qty': 2}]
    assert odw.request_qty_exceeds_required(over, required) is True

    accepted_alloc = {
        'dealer_name': 'KUN',
        'branch': 'Branch C',
        'request_qty': 1,
        'request_no': 'RQ-C',
        'status': 'Accepted',
        'request_status': 'Accepted',
    }
    assert odw.sum_active_allocation_qty([accepted_alloc]) == 1
    assert odw.request_qty_exceeds_required([accepted_alloc, next_alloc], required) is True
