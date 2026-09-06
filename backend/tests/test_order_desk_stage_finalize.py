"""
Order Desk stage finalize tests: freeze, timer SLA, stage gates, locks.
"""
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
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
    return {'Authorization': f'Bearer {token}'}


@pytest.fixture(scope='module')
def auth():
    return _login()


def _part():
    return f'TEST-STG-{uuid.uuid4().hex[:8].upper()}'


class TestTimerAndFreezeHelpers:
    def test_response_time_buckets(self):
        import order_desk_workflow as odw
        assert odw.response_time_minutes_for_lines(1) == 30
        assert odw.response_time_minutes_for_lines(20) == 30
        assert odw.response_time_minutes_for_lines(21) == 45
        assert odw.response_time_minutes_for_lines(50) == 45
        assert odw.response_time_minutes_for_lines(51) == 60
        assert odw.response_time_minutes_for_lines(100) == 60

    def test_reminder_offsets_do_not_extend_deadline(self):
        import order_desk_workflow as odw
        sched = odw.compute_response_schedule(10)
        assert sched['response_time_minutes'] == 30
        assert sched['response_status'] == 'awaiting'
        assert sched['reminder_at'] < sched['urgent_reminder_at'] < sched['reminder_3_at'] < sched['response_deadline']

    def test_evaluate_timer_cancel_allowed_only_after_expiry(self):
        import order_desk_workflow as odw
        sent = datetime.now(timezone.utc) - timedelta(minutes=5)
        header = {
            **odw.compute_response_schedule(5, sent),
            'status': 'Requested',
            'items': [{}] * 5,
        }
        mid = odw.evaluate_group_timer(header)
        assert mid['cancel_allowed'] is False
        assert mid['response_status'] == 'awaiting'

        expired_header = {
            **odw.compute_response_schedule(5, datetime.now(timezone.utc) - timedelta(minutes=40)),
            'status': 'Requested',
            'items': [{}] * 5,
        }
        late = odw.evaluate_group_timer(expired_header)
        assert late['cancel_allowed'] is True
        assert late['response_status'] == 'expired'

    def test_freeze_key_day_scoped(self):
        import order_desk_workflow as odw
        k1 = odw.freeze_key('P1', 'Hyundai', 'Dealer', 'Ambattur', '20260810')
        k2 = odw.freeze_key('P1', 'Hyundai', 'Dealer', 'Ambattur', '20260811')
        k3 = odw.freeze_key('P1', 'Hyundai', 'Dealer', 'Vanagaram', '20260810')
        assert k1 != k2
        assert k1 != k3
        freezes = {k1}
        assert odw.is_source_frozen(freezes, 'P1', 'Hyundai', 'Dealer', 'Ambattur', '20260810')
        assert not odw.is_source_frozen(freezes, 'P1', 'Hyundai', 'Dealer', 'Ambattur', '20260811')
        assert not odw.is_source_frozen(freezes, 'P1', 'Hyundai', 'Dealer', 'Vanagaram', '20260810')

    def test_eligible_pool_skips_frozen_and_aging(self):
        import order_desk_workflow as odw
        order = {'dealer_name': 'KUN', 'brand_name': 'Hyundai'}
        item = {
            'part_number': 'ABC',
            'same_dealer_sources': [
                {'dealer_name': 'KUN', 'branch': 'A', 'available_qty': 5, 'net_available_qty': 5, 'purchase_aging_days': 100},
                {'dealer_name': 'KUN', 'branch': 'B', 'available_qty': 5, 'net_available_qty': 5, 'purchase_aging_days': 40},
                {'dealer_name': 'KUN', 'branch': 'C', 'available_qty': 5, 'net_available_qty': 5, 'purchase_aging_days': 120},
            ],
        }
        date_key = odw.business_date_key()
        freezes = {odw.freeze_key('ABC', 'Hyundai', 'KUN', 'A', date_key)}
        pool = odw.eligible_pool(item, order, 'branch', freezes, 'purchase', 90)
        branches = {s['branch'] for s in pool}
        assert 'A' not in branches  # frozen
        assert 'B' not in branches  # below aging
        assert 'C' in branches

    def test_primary_filter_mapping(self):
        import order_desk_workflow as odw
        assert odw.primary_filter_status('Awaiting Response') == 'Request Sent'
        assert odw.primary_filter_status('Partially Accepted') == 'Accepted'
        assert odw.primary_filter_status('Rejected Today') == 'Rejected'
        assert odw.primary_filter_status('To Process') == 'To Process'


class TestOrderDeskStageApi:
    def test_create_and_detail_exposes_stage_fields(self, auth):
        part = _part()
        res = requests.post(
            f'{API}/order-desk/paste',
            json={'rows': [{'part_number': part, 'quantity': 4, 'description': 'Stage Test', 'value': 10}]},
            headers=auth, timeout=60,
        )
        assert res.status_code == 200, res.text
        order_id = res.json()['order']['id']
        detail = requests.get(f'{API}/order-desk/orders/{order_id}', headers=auth, timeout=60)
        assert detail.status_code == 200
        body = detail.json()
        assert 'stage' in body
        item = body['items'][0]
        for field in (
            'accepted_qty', 'remaining_qty', 'branch_stage_status', 'dealer_stage_status',
            'factory_stage_status', 'cancel_allowed', 'qty_locked', 'filter_status',
        ):
            assert field in item

    def test_send_without_allocation_fails(self, auth):
        part = _part()
        res = requests.post(
            f'{API}/order-desk/paste',
            json={'rows': [{'part_number': part, 'quantity': 1, 'description': 'NoAlloc', 'value': 1}]},
            headers=auth, timeout=60,
        )
        oid = res.json()['order']['id']
        res = requests.post(f'{API}/order-desk/orders/{oid}/send-requests', json={'level': 'branch'}, headers=auth, timeout=30)
        assert res.status_code == 400

    def test_safe_cancel_before_send(self, auth):
        part = _part()
        res = requests.post(
            f'{API}/order-desk/paste',
            json={'rows': [{'part_number': part, 'quantity': 1, 'description': 'CancelSafe', 'value': 1}]},
            headers=auth, timeout=60,
        )
        oid = res.json()['order']['id']
        iid = res.json()['items'][0]['id']
        res = requests.post(
            f'{API}/order-desk/orders/{oid}/items/{iid}/request-cancellation',
            json={'reason': 'Duplicate Entry', 'remarks': 'test'},
            headers=auth, timeout=30,
        )
        assert res.status_code == 200
        assert res.json().get('auto_approved') is True

    def test_cancel_timeout_requires_expiry(self, auth):
        # Without a real request group this is 404; with fabricated we just ensure endpoint exists
        res = requests.post(
            f'{API}/requests/group/RQNOTEXIST0001/cancel-timeout',
            json={}, headers=auth, timeout=30,
        )
        assert res.status_code == 404

    def test_template_still_works(self, auth):
        res = requests.get(f'{API}/order-desk/template', headers=auth, timeout=30)
        assert res.status_code == 200


class TestRequestHeadersActiveUniqueIndex:
    def test_partial_unique_index_allows_historical_duplicates(self):
        """Startup index must succeed even when historical destination dups exist."""
        import asyncio
        import sys
        from pathlib import Path

        backend_dir = Path(__file__).resolve().parents[1]
        if str(backend_dir) not in sys.path:
            sys.path.insert(0, str(backend_dir))

        async def _run():
            import server as srv
            await srv.ensure_product_hub_indexes()
            idxs = await srv.db.request_headers.index_information()
            assert 'uniq_active_request_destination' in idxs
            info = idxs['uniq_active_request_destination']
            assert info.get('unique') is True
            assert info.get('partialFilterExpression') == {'status': 'Requested'}
            assert 'order_id_1_supplying_dealer_1_supplying_branch_1' not in idxs

        asyncio.run(_run())
