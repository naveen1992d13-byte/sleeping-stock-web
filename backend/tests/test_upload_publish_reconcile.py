"""
Publish / Product Hub reconcile tests.
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
    return {'Authorization': f'Bearer {token}'}


@pytest.fixture(scope='module')
def auth():
    return _login()


class TestPublishHelpers:
    def test_batch_totals_from_items(self):
        import server as srv
        items = [
            {'available_qty_number': 2, 'unit_value_number': 10, 'total_value_number': 20},
            {'available_qty_number': 0, 'unit_value_number': 5, 'total_value_number': 0},
            {'quantity': 3, 'mav_value': 4},
        ]
        totals = srv._product_hub_batch_totals_from_items(items)
        assert totals['total_item'] == 3
        assert totals['available_item'] == 2
        assert totals['available_qty'] == 5
        assert totals['total_value'] == 20 + 12


class TestVanagaramPublishReconcile:
    """Live reconcile for PUHY260810001 when present in shared Atlas."""

    def test_publish_idempotent_reconcile_and_summary(self, auth):
        uploads = requests.get(f'{API}/uploads/v2?type=product', headers=auth, timeout=60)
        assert uploads.status_code == 200
        target = next((u for u in uploads.json() if u.get('upload_no') == 'PUHY260810001'), None)
        if not target:
            pytest.skip('PUHY260810001 not present in this environment')

        upload_id = target['id']
        # First call reconciles partial publish / marks published
        r1 = requests.put(f'{API}/uploads/{upload_id}/publish-v2', headers=auth, timeout=120)
        assert r1.status_code == 200, r1.text
        body1 = r1.json()
        assert body1.get('items', 0) > 0

        # Second call must be safe success (already published), not 400
        r2 = requests.put(f'{API}/uploads/{upload_id}/publish-v2', headers=auth, timeout=120)
        assert r2.status_code == 200, r2.text
        assert r2.json().get('already_published') is True or r2.json().get('reconciled') is True or 'published' in str(r2.json().get('message', '')).lower()

        meta = requests.get(f'{API}/uploads/v2?type=product', headers=auth, timeout=60).json()
        refreshed = next(u for u in meta if u.get('id') == upload_id)
        assert refreshed.get('publish_status') == 'Published'

        summary = requests.get(
            f'{API}/product-hub/summary?brand=Hyundai&dealer=FPL%20Hyundai&branch=Vanagaram',
            headers=auth, timeout=120,
        )
        assert summary.status_code == 200, summary.text
        s = summary.json()
        assert s.get('totalItem', 0) > 0
        assert s.get('totalAvailableQty', 0) > 0
        assert s.get('totalValue', 0) > 0

        records = requests.get(
            f'{API}/product-hub/records?brand=Hyundai&dealer=FPL%20Hyundai&branch=Vanagaram&page=1&page_size=5',
            headers=auth, timeout=120,
        )
        assert records.status_code == 200
        assert records.json().get('total', 0) == s.get('totalItem')
