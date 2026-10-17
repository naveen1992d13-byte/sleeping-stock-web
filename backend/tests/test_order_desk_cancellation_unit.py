"""Approved item cancellation closes outstanding quantity (pure unit, no HTTP)."""
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import order_desk_workflow as odw  # noqa: E402

ORDER = {'brand_name': 'Hyundai', 'dealer_name': 'FPL', 'branch': 'Vanagaram'}


def _item(item_id, remaining, cancellation_status=None, **extra):
    item = {'id': item_id, 'part_number': item_id, 'required_qty': 4, 'remaining_qty': remaining}
    if cancellation_status:
        item['cancellation_status'] = cancellation_status
    item.update(extra)
    return item


def test_approved_cancellation_does_not_block_finish():
    items = [_item('A', 2, 'approved')]
    readiness = odw.evaluate_finish_readiness(ORDER, items, {})
    assert readiness['can_finish'] is True
    assert readiness['remaining_open_items'] == []
    assert readiness['missing_factory_order_no'] == []


def test_pending_or_rejected_cancellation_still_blocks_finish():
    for status in ('pending', 'rejected'):
        readiness = odw.evaluate_finish_readiness(ORDER, [_item('A', 2, status)], {})
        assert readiness['can_finish'] is False
        assert readiness['remaining_open_items'] == ['A']


def test_cancelled_item_with_sticky_factory_qty_does_not_need_factory_order_no():
    items = [_item('A', 2, 'approved', factory_order_qty=2, no_further_stock=True)]
    readiness = odw.evaluate_finish_readiness(ORDER, items, {})
    assert readiness['can_finish'] is True
    line = odw.build_fulfillment_line(ORDER, items[0], [])
    assert line['factory_qty'] == 0
    assert line['fulfilled_qty'] == 0
    assert all(s['source_type'] != 'Factory' for s in line.get('sources', []))


def test_uncancelled_factory_item_still_needs_factory_order_no():
    items = [_item('A', 2, factory_order_qty=2)]
    readiness = odw.evaluate_finish_readiness(ORDER, items, {})
    assert readiness['missing_factory_order_no'] == ['A']


def test_fulfillment_line_reports_cancelled_with_zero_remaining():
    line = odw.build_fulfillment_line(ORDER, _item('A', 4, 'approved'), [])
    assert line['final_status'] == 'Cancelled'
    assert line['remaining_qty'] == 0


def test_fulfillment_line_keeps_accepted_portion_of_cancelled_item():
    reqs = [{'status': 'Approved', 'accepted_qty': 2, 'supplying_dealer': 'FPL', 'supplying_branch': 'Koyambedu',
             'request_number': 'TS-RQ1'}]
    line = odw.build_fulfillment_line(ORDER, _item('A', 2, 'approved'), reqs)
    assert line['final_status'] == 'Cancelled'
    assert line['accepted_qty'] == 2
    assert line['remaining_qty'] == 0


def test_open_item_unchanged():
    line = odw.build_fulfillment_line(ORDER, _item('A', 4), [])
    assert line['final_status'] == 'Open'
    assert line['remaining_qty'] == 4


def test_workflow_status_cancelled_overrides_no_further_stock():
    item = _item('A', 2, 'approved', factory_order_qty=2, no_further_stock=True, request_status='Cancelled')
    wf = odw.compute_item_workflow(item, ORDER, [])
    assert wf['request_status'] == odw.REQUEST_STATUS_CANCELLED
    assert wf['factory_order_qty'] == 0


def test_workflow_status_no_further_stock_without_cancellation():
    item = _item('A', 2, factory_order_qty=2, no_further_stock=True)
    wf = odw.compute_item_workflow(item, ORDER, [])
    assert wf['request_status'] == odw.REQUEST_STATUS_FACTORY
