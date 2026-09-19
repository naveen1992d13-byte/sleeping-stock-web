"""Derived fulfillment_stage / part_status and dispatch-document gate."""
from datetime import datetime, timedelta, timezone
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import request_fulfillment as rff
import request_in_transit_reminder as rit


class TestFulfillmentStage:
    def test_requested_is_sent(self):
        assert rff.fulfillment_stage({'status': 'Requested'}) == 'sent'

    def test_approved_without_picking_finished_is_picking(self):
        assert rff.fulfillment_stage({'status': 'Approved'}) == 'picking'
        assert rff.fulfillment_stage({'status': 'Partially Approved'}) == 'picking'

    def test_approved_with_picking_finished(self):
        assert rff.fulfillment_stage({'status': 'Approved', 'picking_finished_at': '2026-09-01T00:00:00+00:00'}) == 'picking_finished'

    def test_dispatched_is_in_transit(self):
        assert rff.fulfillment_stage({'status': 'Dispatched'}) == 'in_transit'

    def test_received_and_completed_are_finished(self):
        assert rff.fulfillment_stage({'status': 'Received'}) == 'finished'
        assert rff.fulfillment_stage({'status': 'Completed'}) == 'finished'

    def test_closed_statuses_excluded(self):
        assert rff.fulfillment_stage({'status': 'Rejected'}) is None
        assert rff.fulfillment_stage({'status': 'Cancelled'}) is None

    def test_legacy_missing_fields_do_not_crash(self):
        row = rff.apply_presentation({'status': 'Requested', 'requested_qty': 5})
        assert row['fulfillment_stage'] == 'sent'
        assert row['part_status'] == 'Pending'
        assert row['balance_qty'] == 5
        assert row['total_value'] == 0


class TestPartStatus:
    def test_picking_and_picked(self):
        assert rff.part_status({'status': 'Approved'}) == 'Picking'
        assert rff.part_status({'status': 'Approved', 'picking_finished_at': 'x', 'accepted_qty': 5, 'requested_qty': 5}) == 'Picked'
        assert rff.part_status({'status': 'Approved', 'picking_finished_at': 'x', 'accepted_qty': 2, 'requested_qty': 5}) == 'Partial'
        assert rff.part_status({'status': 'Rejected'}) == 'Not Available'


class TestDispatchDocumentGate:
    def test_missing_fields_incomplete(self):
        assert rff.dispatch_document_complete({}) is False
        assert rff.dispatch_document_complete({'dispatch_document': {'document_no': 'A'}}) is False
        assert rff.dispatch_document_complete({
            'dispatch_document': {
                'document_no': 'INV-1', 'document_date': '2026-09-01', 'document_value': '100',
            }
        }) is False

    def test_complete_when_storage_key_present(self):
        assert rff.dispatch_document_complete({
            'dispatch_document': {
                'document_no': 'INV-1',
                'document_date': '2026-09-01',
                'document_value': '2500',
                'storage_key': 'request-dispatch-documents/RQ1/file.pdf',
            }
        }) is True


class TestDispatchEndpointSourceGate:
    def test_dispatch_handler_requires_document_and_picking_finished(self):
        import inspect
        import server as srv

        source = inspect.getsource(srv.request_center_dispatch)
        assert 'dispatch_document_complete' in source
        assert 'DISPATCH_INCOMPLETE_MESSAGE' in source
        assert 'picking_finished_at' in source
        assert 'Picking must be finished before dispatch.' in source


class TestInTransitFlags:
    def test_due_levels_escalate(self):
        now = datetime.now(timezone.utc)
        header = {
            'status': 'Dispatched',
            'dispatched_at': (now - timedelta(hours=50)).isoformat(),
            'in_transit_flags': {},
        }
        due = rit.due_in_transit_levels(header, now)
        assert '24h' in due
        assert '48h' in due
        assert '72h' not in due

    def test_never_due_when_not_dispatched(self):
        now = datetime.now(timezone.utc)
        header = {'status': 'Approved', 'dispatched_at': (now - timedelta(hours=80)).isoformat()}
        assert rit.due_in_transit_levels(header, now) == []
