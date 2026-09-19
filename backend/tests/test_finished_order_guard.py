"""Finished-order freeze + additive value/total_value helpers."""
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from fastapi import HTTPException

import order_desk_guards as odg


class TestFinishedOrderGuard:
    def test_open_order_allowed(self):
        odg.assert_order_not_finished({'status': 'Requested'})
        odg.assert_order_not_finished({'status': 'Order Created'})

    def test_finished_at_blocks(self):
        with pytest.raises(HTTPException) as exc:
            odg.assert_order_not_finished({'status': 'Completed', 'finished_at': '2026-09-01T00:00:00+00:00'})
        assert exc.value.status_code == 409
        assert exc.value.detail == odg.FINISHED_ORDER_MESSAGE

    def test_finished_status_blocks(self):
        with pytest.raises(HTTPException) as exc:
            odg.assert_order_not_finished({'status': 'Finished'})
        assert exc.value.status_code == 409

    def test_completed_with_finished_by_blocks(self):
        with pytest.raises(HTTPException):
            odg.assert_order_not_finished({'status': 'Completed', 'finished_by': 'u1', 'overall_status': 'Completed'})


class TestOrderValueFields:
    def test_legacy_unit_value_only(self):
        item = {'required_qty': 4, 'unit_value': 10}
        assert odg.order_line_unit_value(item) == 10
        assert odg.order_line_total_value(item) == 40
        stamped = odg.apply_order_item_values(dict(item))
        assert stamped['value'] == 10
        assert stamped['total_value'] == 40
        assert stamped['unit_value'] == 10

    def test_missing_value_is_zero_not_error(self):
        item = {'required_qty': 3}
        assert odg.order_line_unit_value(item) == 0
        assert odg.order_line_total_value(item) == 0


FINISHED_MUTATING_ENDPOINTS = (
    'order_desk_check_availability',
    'order_desk_allocate',
    'order_desk_auto_suggest',
    'order_desk_send_requests_v2',
    'order_desk_add_items',
    'order_desk_re_enquire',
    'order_desk_request_cancellation',
    'save_factory_system_order',
    'save_factory_system_order_bulk',
)


class TestFinishedOrderGuardWiredToMutatingEndpoints:
    def test_every_mutating_order_endpoint_calls_assert_order_not_finished(self):
        import inspect
        import server as srv

        for name in FINISHED_MUTATING_ENDPOINTS:
            fn = getattr(srv, name)
            source = inspect.getsource(fn)
            assert 'odg.assert_order_not_finished' in source, f'{name} is missing the finished-order freeze'
