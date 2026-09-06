"""Focused unit tests for Order Desk own-branch-first, finish, fulfillment, SLA."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import order_desk_workflow as odw
import request_sla_scheduler as sla


class TestOwnBranchFirst:
    def test_partition_puts_exact_ordering_branch_first(self):
        order = {'branch': 'Ambattur', 'dealer_name': 'KUN Hyundai'}
        pool = [
            {'branch': 'Vanagaram', 'dealer_name': 'KUN Hyundai', 'net_available_qty': 8},
            {'branch': 'Ambattur', 'dealer_name': 'KUN Hyundai', 'net_available_qty': 2},
            {'branch': 'Porur', 'dealer_name': 'KUN Hyundai', 'net_available_qty': 5},
        ]
        ranked = odw.partition_own_branch_first(pool, order)
        assert ranked[0]['branch'] == 'Ambattur'
        assert [s['branch'] for s in ranked[1:]] == ['Vanagaram', 'Porur']

    def test_partition_keeps_other_relative_order(self):
        order = {'branch': 'Missing'}
        pool = [{'branch': 'A'}, {'branch': 'B'}]
        assert odw.partition_own_branch_first(pool, order) == pool

    def test_own_branch_match_is_case_insensitive(self):
        assert odw.is_own_ordering_branch({'branch': 'AMBATTUR'}, {'branch': 'Ambattur'})
        assert not odw.is_own_ordering_branch({'branch': 'Porur'}, {'branch': 'Ambattur'})


class TestReminderSchedule:
    def test_thirds_stay_inside_unchanged_deadline(self):
        first, second, third = odw.reminder_offsets_minutes(30)
        assert first == 10
        assert second == 20
        assert third == 29
        sched = odw.compute_response_schedule(10)
        assert sched['response_time_minutes'] == 30
        assert sched['reminder_at'] < sched['urgent_reminder_at'] < sched['reminder_3_at'] < sched['response_deadline']

    def test_45_and_60_minute_buckets_unchanged(self):
        assert odw.response_time_minutes_for_lines(21) == 45
        assert odw.response_time_minutes_for_lines(51) == 60
        _, _, third45 = odw.reminder_offsets_minutes(45)
        _, _, third60 = odw.reminder_offsets_minutes(60)
        assert third45 == 44
        assert third60 == 59


class TestFinishReadiness:
    def test_blocks_unresolved_request(self):
        order = {'branch': 'Ambattur'}
        items = [{'id': 'i1', 'remaining_qty': 0, 'system_order_number': ''}]
        reqs = {'i1': [{'status': 'Requested', 'request_number': 'RQ1'}]}
        result = odw.evaluate_finish_readiness(order, items, reqs)
        assert result['can_finish'] is False
        assert result['unresolved_requests']

    def test_blocks_missing_factory_order_no(self):
        order = {'branch': 'Ambattur'}
        items = [{'id': 'i1', 'remaining_qty': 0, 'factory_fulfilled_qty': 2, 'system_order_number': ''}]
        result = odw.evaluate_finish_readiness(order, items, {})
        assert result['can_finish'] is False
        assert 'i1' in result['missing_factory_order_no']

    def test_ready_when_accepted_and_factory_complete(self):
        order = {'branch': 'Ambattur'}
        items = [{'id': 'i1', 'remaining_qty': 0, 'factory_fulfilled_qty': 1, 'system_order_number': 'FO-1'}]
        reqs = {'i1': [{'status': 'Approved', 'accepted_qty': 2}]}
        result = odw.evaluate_finish_readiness(order, items, reqs)
        assert result['can_finish'] is True


class TestFulfillmentLine:
    def test_own_branch_and_multi_source_breakup(self):
        order = {'branch': 'Ambattur', 'dealer_name': 'KUN Hyundai'}
        item = {
            'part_number': 'P1', 'required_qty': 5, 'remaining_qty': 0,
            'factory_fulfilled_qty': 1, 'system_order_number': 'FO-99',
        }
        reqs = [
            {'status': 'Approved', 'accepted_qty': 2, 'supplying_dealer': 'KUN Hyundai', 'supplying_branch': 'Ambattur'},
            {'status': 'Approved', 'accepted_qty': 2, 'supplying_dealer': 'Other', 'supplying_branch': 'Porur'},
        ]
        line = odw.build_fulfillment_line(order, item, reqs)
        assert line['requested_qty'] == 5
        assert line['own_branch_fulfilled_qty'] == 2
        assert line['accepted_qty'] == 4
        assert line['source_dealer'] == 'Multiple'
        assert line['source_branch'] == 'Multiple'
        assert line['factory_qty'] == 1
        assert line['factory_order_no'] == 'FO-99'
        assert line['final_status'] == 'Factory Completed'
        assert len(line['sources']) == 3


class TestSlaHelpers:
    def test_timeout_due_only_after_deadline(self):
        sent = datetime.now(timezone.utc) - timedelta(minutes=5)
        header = {**odw.compute_response_schedule(5, sent), 'status': 'Requested'}
        assert sla.is_timeout_due(header) is False
        expired = {**odw.compute_response_schedule(5, datetime.now(timezone.utc) - timedelta(minutes=40)), 'status': 'Requested'}
        assert sla.is_timeout_due(expired) is True

    def test_reminders_due_at_thirds(self):
        sent = datetime.now(timezone.utc) - timedelta(minutes=21)
        header = {**odw.compute_response_schedule(5, sent), 'status': 'Requested', 'mobile_push_sent': {}}
        kinds = sla.reminder_due_kinds(header)
        assert 'reminder_1' in kinds
        assert 'reminder_2' in kinds
        assert 'reminder_3' not in kinds

    def test_reminders_stop_when_not_awaiting(self):
        sent = datetime.now(timezone.utc) - timedelta(minutes=21)
        header = {**odw.compute_response_schedule(5, sent), 'status': 'Approved'}
        assert sla.reminder_due_kinds(header) == []
