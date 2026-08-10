"""
Order Desk workflow regression tests.

These hit a running backend (REACT_APP_BACKEND_URL) and shared Atlas DB.
They use clearly-labelled test part numbers and clean up where practical.
"""
import os
import sys
import uuid
from pathlib import Path

import pytest
import requests

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'http://127.0.0.1:8000').rstrip('/')
API = f'{BASE_URL}/api'

ADMIN_EMAIL = 'admin@sleepingstock.in'
ADMIN_PASSWORD = 'admin123'


def _login():
    res = requests.post(f'{API}/auth/login', json={'email': ADMIN_EMAIL, 'password': ADMIN_PASSWORD}, timeout=30)
    assert res.status_code == 200, res.text
    token = res.json().get('access_token') or res.json().get('token')
    assert token
    return {'Authorization': f'Bearer {token}'}


@pytest.fixture(scope='module')
def auth():
    return _login()


def _unique_part():
    return f'TEST-OD-{uuid.uuid4().hex[:8].upper()}'


class TestOrderDeskWorkflowHelpers:
    def test_workflow_module_status_mapping(self):
        import order_desk_workflow as odw
        assert odw.map_request_center_status('Requested') == 'Request Sent'
        assert odw.map_request_center_status('Approved', 5, 5) == 'Accepted'
        assert odw.map_request_center_status('Approved', 5, 2) == 'Partially Accepted'
        assert odw.map_request_center_status('Rejected') == 'Rejected'
        assert odw.email_status_label('sent') == 'Email Sent'
        assert odw.email_status_label('failed') == 'Email Failed'
        assert odw.email_status_label('pending') == 'Email Pending'

    def test_allocation_level_and_compact(self):
        import order_desk_workflow as odw
        order = {'dealer_name': 'KUN Hyundai'}
        assert odw.allocation_level({'dealer_name': 'KUN Hyundai', 'branch': 'Ambattur'}, order) == 'branch'
        assert odw.allocation_level({'dealer_name': 'Other Dealer', 'branch': 'X'}, order) == 'dealer'
        text = odw.compact_requested_from(
            [{'source_type': 'Branch', 'source_branch': 'Ambattur', 'requested_qty': 2},
             {'source_type': 'Branch', 'source_branch': 'Vanagaram', 'requested_qty': 1}],
            [],
            order,
        )
        assert 'Ambattur - 2' in text
        assert 'Vanagaram - 1' in text

    def test_compute_item_partial_remaining(self):
        import order_desk_workflow as odw
        item = {'id': 'i1', 'required_qty': 5, 'allocations': []}
        order = {'dealer_name': 'KUN'}
        reqs = [{
            'id': 'r1', 'status': 'Approved', 'requested_qty': 2, 'accepted_qty': 1,
            'supplying_dealer': 'KUN', 'supplying_branch': 'Ambattur', 'request_number': 'RQTEST1',
        }]
        wf = odw.compute_item_workflow(item, order, reqs)
        assert wf['accepted_qty'] == 1
        assert wf['remaining_qty'] == 4
        assert wf['request_status'] in ('Partially Accepted', 'Remaining Qty')
        assert len(wf['request_history']) == 1


class TestOrderDeskApiSmoke:
    def test_template_endpoint(self, auth):
        res = requests.get(f'{API}/order-desk/template', headers=auth, timeout=30)
        assert res.status_code == 200
        assert 'spreadsheet' in res.headers.get('content-type', '') or res.content[:2] == b'PK'

    def test_create_order_add_items_cancel_safe(self, auth):
        part = _unique_part()
        payload = {
            'rows': [{'part_number': part, 'quantity': 3, 'description': 'OD Workflow Test Part', 'value': 100}],
            'brand': '', 'dealer': '', 'branch': '',
        }
        # Prefer paste; scope may be empty for master — still creates order
        res = requests.post(f'{API}/order-desk/paste', json=payload, headers=auth, timeout=60)
        assert res.status_code == 200, res.text
        data = res.json()
        order = data['order']
        items = data['items']
        assert order.get('order_number')
        assert len(items) == 1
        order_id = order['id']
        item_id = items[0]['id']

        # Add items under same order number
        add_part = _unique_part()
        res = requests.post(
            f'{API}/order-desk/orders/{order_id}/add-items',
            json={'rows': [{'part_number': add_part, 'quantity': 1, 'description': 'Added Line', 'value': 50}]},
            headers=auth,
            timeout=60,
        )
        assert res.status_code == 200, res.text
        assert res.json()['order']['order_number'] == order['order_number']
        assert any(i.get('added_after_order_creation') for i in res.json()['items'])

        # Safe auto-cancel (no request sent)
        res = requests.post(
            f'{API}/order-desk/orders/{order_id}/items/{item_id}/request-cancellation',
            json={'reason': 'Duplicate Entry', 'remarks': 'workflow test'},
            headers=auth,
            timeout=60,
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body.get('auto_approved') is True
        assert body['cancellation']['approval_status'] == 'approved'

        # Detail still loads (history retained)
        res = requests.get(f'{API}/order-desk/orders/{order_id}', headers=auth, timeout=60)
        assert res.status_code == 200
        detail_items = res.json()['items']
        cancelled = next(i for i in detail_items if i['id'] == item_id)
        assert cancelled.get('request_status') == 'Cancelled'
        assert cancelled.get('cancellation_reason') == 'Duplicate Entry'

    def test_purchased_outside_retained(self, auth):
        part = _unique_part()
        res = requests.post(
            f'{API}/order-desk/paste',
            json={'rows': [{'part_number': part, 'quantity': 2, 'description': 'Outside Purchase Test', 'value': 200}]},
            headers=auth,
            timeout=60,
        )
        assert res.status_code == 200, res.text
        order_id = res.json()['order']['id']
        item_id = res.json()['items'][0]['id']
        res = requests.post(
            f'{API}/order-desk/orders/{order_id}/items/{item_id}/request-cancellation',
            json={'reason': 'Purchased Outside', 'remarks': 'bought locally'},
            headers=auth,
            timeout=60,
        )
        assert res.status_code == 200, res.text
        assert res.json()['cancellation'].get('purchased_outside') is True
        detail = requests.get(f'{API}/order-desk/orders/{order_id}', headers=auth, timeout=60).json()
        item = next(i for i in detail['items'] if i['id'] == item_id)
        assert item.get('purchased_outside') is True
        assert float(item.get('purchased_outside_qty') or 0) == 2

    def test_send_requests_requires_level_allocations(self, auth):
        part = _unique_part()
        res = requests.post(
            f'{API}/order-desk/paste',
            json={'rows': [{'part_number': part, 'quantity': 1, 'description': 'No Alloc Test', 'value': 10}]},
            headers=auth,
            timeout=60,
        )
        assert res.status_code == 200
        order_id = res.json()['order']['id']
        res = requests.post(
            f'{API}/order-desk/orders/{order_id}/send-requests',
            json={'level': 'branch'},
            headers=auth,
            timeout=60,
        )
        assert res.status_code == 400

    def test_notifications_pdf_and_email_body(self):
        import notifications
        group = {
            'request_number': 'RQHY2608109999',
            'order_number': 'ORHY260810999',
            'created_at': '2026-08-10T10:00:00+00:00',
            'status': 'Requested',
            'requested_user_name': 'Test User',
            'requesting_brand': 'Hyundai',
            'requesting_dealer': 'Test Dealer',
            'requesting_branch': 'Test Branch',
            'supplying_brand': 'Hyundai',
            'supplying_dealer': 'Supply Dealer',
            'supplying_branch': 'Supply Branch',
            'total_items': 1,
            'total_qty': 1,
            'total_value': 100,
            'items': [{
                'part_number': 'P1', 'description': 'Desc', 'requested_qty': 1,
                'available_qty_at_request': 5, 'value': 100,
                'purchase_aging_days': 90, 'sales_aging_days': 30, 'loc': 'A1',
            }],
            'pdf_filename': 'RQHY2608109999.pdf',
        }
        pdf = notifications.build_request_pdf(group)
        assert pdf[:4] == b'%PDF'
        assert len(pdf) > 500
        # send_request_pdf_email never raises; without SMTP it returns skipped/failed
        result = notifications.send_request_pdf_email('invalid', group, pdf)
        assert result.get('status') in ('skipped', 'failed')
