"""Phase-1 Order Desk: attempt ID before official RQ; allocation sent helpers."""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import order_desk_workflow as odw  # noqa: E402
import mobile_push  # noqa: E402


def test_allocation_is_sent_with_attempt_id_only():
    assert odw.allocation_is_sent({'request_attempt_id': 'AT-HY-1', 'status': 'Awaiting Response'})
    assert not odw.allocation_is_sent({'request_qty': 2, 'status': 'Ready to Send'})
    assert odw.allocation_is_sent({'request_number': 'RQHY1', 'status': 'Awaiting Response'})


def test_mark_allocations_sent_defers_official_rq():
    item = {
        'allocations': [
            {'dealer_name': 'FPL', 'branch': 'Vanagaram', 'request_qty': 2, 'status': 'Ready to Send'},
            {'dealer_name': 'FPL', 'branch': 'Porur', 'request_qty': 1, 'status': 'Ready to Send'},
        ]
    }
    stamped = odw.mark_allocations_sent(
        item,
        [item['allocations'][0]],
        request_number=None,
        now='2026-10-17T00:00:00+00:00',
        attempt_id='TS-ATHY2610170001',
    )
    assert stamped[0]['request_attempt_id'] == 'TS-ATHY2610170001'
    assert not stamped[0].get('request_number')
    assert stamped[0]['locked'] is True
    assert not stamped[1].get('request_attempt_id')


def test_stamp_allocations_official_request():
    item = {
        'allocations': [
            {
                'dealer_name': 'FPL',
                'branch': 'Vanagaram',
                'request_attempt_id': 'TS-ATHY2610170001',
                'locked': True,
            }
        ]
    }
    stamped = odw.stamp_allocations_official_request(
        item, 'TS-ATHY2610170001', 'TS-RQHY2610170001',
    )
    assert stamped[0]['request_number'] == 'TS-RQHY2610170001'
    assert stamped[0]['request_no'] == 'TS-RQHY2610170001'
    assert stamped[0]['request_attempt_id'] == 'TS-ATHY2610170001'


def test_attempt_push_payload_without_official_rq():
    msg = mobile_push.build_branch_request_push_message(
        'ExponentPushToken[preview]',
        {
            'id': 'group-1',
            'attempt_id': 'TS-ATHY2610170001',
            'request_number': None,
            'requesting_branch': 'Vanagaram',
            'total_items': 1,
            'total_qty': 2,
        },
        kind='new',
    )
    data = msg['data']
    assert data['request_group_key'] == 'group-1'
    assert data['attempt_id'] == 'TS-ATHY2610170001'
    assert data['requestNumber'] == 'TS-ATHY2610170001'
    assert data['request_number'] in ('', None)
    assert data['official'] is False
