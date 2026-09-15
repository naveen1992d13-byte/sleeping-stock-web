"""Focused unit tests for reject→factory unlock, LOC mapping, and request-level alerts."""
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

import mobile_api
import order_desk_workflow as odw
import request_print
import user_alerts as ua


def test_mobile_request_line_loc_uses_snapshot_not_branch_name():
    assert mobile_api._request_line_loc({
        'loc_at_request': 'DISPLAY STOCK', 'loc': '', 'location': 'Vanagaram', 'branch': 'Vanagaram',
    }) == 'DISPLAY STOCK'
    assert mobile_api._request_line_loc({'loc': 'RACK-3', 'location': 'Ambattur'}) == 'RACK-3'
    assert mobile_api._request_line_loc({'bin_location': '11A010101A'}) == '11A010101A'
    assert mobile_api._request_line_loc({'location': 'Vanagaram', 'branch': 'Vanagaram'}) == ''
    assert mobile_api._request_line_loc({}) == ''


def test_stock_bin_loc_uses_uploaded_loc_not_branch_name():
    assert odw.stock_bin_loc({'loc': 'DISPLAY STOCK', 'branch': 'Vanagaram', 'location': 'Vanagaram'}) == 'DISPLAY STOCK'
    assert odw.stock_bin_loc({'bin_location': 'RACK-3', 'branch': 'Ambattur'}) == 'RACK-3'
    assert odw.stock_bin_loc({'LOC': '11A010101A', 'location': 'Ambattur', 'branch': 'Ambattur'}) == '11A010101A'
    assert odw.stock_bin_loc({'location': 'Vanagaram', 'branch': 'Vanagaram'}) == ''
    assert odw.stock_bin_loc({'location': 'A2', 'branch': 'Vanagaram'}) == 'A2'
    assert odw.stock_bin_loc({}) == ''


def test_reject_unlocks_remaining_and_stops_that_request_timer():
    item = {
        'id': 'i1', 'required_qty': 4, 'allocations': [
            {
                'dealer_name': 'KUN', 'branch': 'Ambattur', 'request_qty': 4,
                'request_no': 'RQ-REJ', 'request_status': 'Awaiting Response',
                'status': 'Awaiting Response',
            }
        ],
        'factory_order_qty': 4,
        'no_further_stock': True,
    }
    order = {'dealer_name': 'KUN', 'branch': 'Vanagaram'}
    live = odw.compute_item_workflow(item, order, [{
        'id': 'r1', 'status': 'Requested', 'requested_qty': 4, 'accepted_qty': 0,
        'supplying_dealer': 'KUN', 'supplying_branch': 'Ambattur', 'request_number': 'RQ-REJ',
        'approval_remarks': '',
    }])
    assert live['remaining_qty'] == 0
    assert live['qty_locked'] is True
    assert live['factory_order_qty'] == 0

    rejected = odw.compute_item_workflow(item, order, [{
        'id': 'r1', 'status': 'Rejected', 'requested_qty': 4, 'accepted_qty': 0,
        'supplying_dealer': 'KUN', 'supplying_branch': 'Ambattur', 'request_number': 'RQ-REJ',
        'approval_remarks': 'No stock today',
    }])
    assert rejected['remaining_qty'] == 4
    assert rejected['qty_locked'] is False
    assert rejected['request_history'][0]['countdown_active'] is False
    assert rejected['request_history'][0]['timer_frozen'] is True
    assert rejected['request_history'][0]['remarks'] == 'No stock today'
    assert rejected['pending_request_number'] is None
    assert rejected['countdown_active'] is False
    assert 'Ambattur' not in (rejected.get('requested_from') or '')
    assert rejected['request_history'][0]['request_status'] == 'Rejected'


def test_rejected_allocation_is_not_pending():
    item = {
        'id': 'i1', 'remaining_qty': 3,
        'allocations': [{
            'dealer_name': 'KUN', 'branch': 'Ambattur', 'request_no': 'RQ-REJ',
            'status': 'Rejected', 'request_status': 'Rejected',
        }],
        'same_dealer_sources': [], 'other_dealer_sources': [],
        'own_stage_exhausted': True, 'branch_stage_exhausted': True, 'dealer_stage_exhausted': True,
    }
    order = {'dealer_name': 'KUN', 'branch': 'Vanagaram', 'brand_name': 'Hyundai'}
    flags = odw.compute_stage_flags(item, order, set())
    assert flags['factory_stage_status'] == 'open'
    assert flags['active_stage'] == 'factory'
    assert flags['expected_next_outcome'] == 'Factory Order'


def test_print_pdf_matches_html_actors_and_loc():
    group = {
        'request_number': 'RQHY2609140001',
        'order_number': 'ORHY2609140001',
        'created_at': '2026-09-14T10:00:00+00:00',
        'requested_at': '2026-09-14T10:00:00+00:00',
        'status': 'Completed',
        'requested_user_name': 'Requester Name',
        'requester_mobile': '1111111111',
        'accepted_user_name': 'Picker Name',
        'accepted_user_mobile': '2222222222',
        'dispatched_user_name': 'Dispatch Name',
        'dispatched_user_mobile': '3333333333',
        'received_user_name': 'Receive Name',
        'received_user_mobile': '4444444444',
        'requesting_brand': 'Hyundai',
        'requesting_dealer': 'Test Dealer',
        'requesting_branch': 'Vanagaram',
        'supplying_brand': 'Hyundai',
        'supplying_dealer': 'Test Dealer',
        'supplying_branch': 'Koyambedu',
        'total_items': 1,
        'total_qty': 2,
        'total_value': 250,
        'items': [{
            'part_number': 'P-LOC-1',
            'description': 'Loc Part',
            'requested_qty': 2,
            'accepted_qty': 2,
            'loc_at_request': 'DISPLAY STOCK',
            'status': 'Completed',
            'remarks': 'ok',
        }],
    }
    html = request_print.build_request_print_html(group)
    pdf = request_print.build_request_pdf(group)
    assert pdf[:4] == b'%PDF'
    for marker in (
        'REQUESTED BY', 'ACCEPTED / PICKED BY', 'DISPATCHED BY', 'RECEIVED BY',
        'DISPLAY STOCK', 'Requester Name', 'Picker Name', 'Dispatch Name', 'Receive Name',
    ):
        assert marker in html, marker
    assert 'APPROVED BY' not in html
    assert '1111111111' in html
    assert '2222222222' in html


def test_two_parts_same_request_dedupe_to_one_alert():
    import asyncio
    import test_user_alerts as ta

    async def _run():
        database = ta.FakeDB()
        ua.init_user_alerts(database, None, None)
        database.users.docs = [
            {"id": "requester", "email": "req@x.com", "role": "user", "status": "Active", "user_id": "U1"},
        ]
        n1 = await ua.alert_request_event(
            {"id": "part-1", "request_number": "RN-GROUP", "part_number": "P1", "requested_by": "requester",
             "requester_email": "req@x.com"},
            "Request Accepted",
        )
        n2 = await ua.alert_request_event(
            {"id": "part-2", "request_number": "RN-GROUP", "part_number": "P2", "requested_by": "requester",
             "requester_email": "req@x.com"},
            "Request Accepted",
        )
        assert n1 == 1
        assert n2 == 0
        assert len(database.user_alerts.docs) == 1
        assert database.user_alerts.docs[0]["source_id"] == "RN-GROUP"
        assert database.user_alerts.docs[0]["message"] == "RN-GROUP"

    asyncio.get_event_loop().run_until_complete(_run())
