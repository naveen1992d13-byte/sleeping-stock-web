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


class TestOwnThenBranchesThenDealers:
    def test_allocation_level_splits_own_and_other_branches(self):
        order = {'dealer_name': 'KUN Hyundai', 'branch': 'Ambattur'}
        assert odw.allocation_level({'dealer_name': 'KUN Hyundai', 'branch': 'Ambattur'}, order) == 'own'
        assert odw.allocation_level({'dealer_name': 'KUN Hyundai', 'branch': 'Vanagaram'}, order) == 'branch'
        assert odw.allocation_level({'dealer_name': 'Other Dealer', 'branch': 'X'}, order) == 'dealer'

    def test_eligible_pool_own_excludes_other_same_dealer_branches(self):
        order = {'dealer_name': 'KUN', 'branch': 'Ambattur', 'brand_name': 'Hyundai'}
        item = {
            'part_number': 'P1',
            'same_dealer_sources': [
                {'dealer_name': 'KUN', 'branch': 'Ambattur', 'available_qty': 3, 'net_available_qty': 3, 'purchase_aging_days': 100},
                {'dealer_name': 'KUN', 'branch': 'Vanagaram', 'available_qty': 8, 'net_available_qty': 8, 'purchase_aging_days': 100},
            ],
            'other_dealer_sources': [
                {'dealer_name': 'Other', 'branch': 'X', 'available_qty': 5, 'net_available_qty': 5, 'purchase_aging_days': 100},
            ],
        }
        own = [s['branch'] for s in odw.eligible_pool(item, order, 'own', set(), 'purchase', 0)]
        branches = [s['branch'] for s in odw.eligible_pool(item, order, 'branch', set(), 'purchase', 0)]
        dealers = [s['dealer_name'] for s in odw.eligible_pool(item, order, 'dealer', set(), 'purchase', 0)]
        assert own == ['Ambattur']
        assert branches == ['Vanagaram']
        assert dealers == ['Other']

    def test_dealers_and_factory_stay_locked_until_prior_stages_exhaust(self):
        order = {'dealer_name': 'KUN', 'branch': 'Ambattur', 'brand_name': 'Hyundai'}
        item = {
            'remaining_qty': 10,
            'accepted_qty': 0,
            'same_dealer_sources': [
                {'dealer_name': 'KUN', 'branch': 'Ambattur', 'available_qty': 3, 'net_available_qty': 3, 'purchase_aging_days': 100},
                {'dealer_name': 'KUN', 'branch': 'Vanagaram', 'available_qty': 8, 'net_available_qty': 8, 'purchase_aging_days': 100},
            ],
            'other_dealer_sources': [
                {'dealer_name': 'Other', 'branch': 'X', 'available_qty': 5, 'net_available_qty': 5, 'purchase_aging_days': 100},
            ],
        }
        flags = odw.compute_stage_flags(item, order, set())
        assert flags['active_stage'] == 'own'
        assert flags['own_stage_status'] == 'open'
        assert flags['branch_stage_status'] == 'locked'
        assert flags['dealer_stage_status'] == 'locked'
        assert flags['factory_stage_status'] == 'locked'

        after_own = odw.compute_stage_flags({**item, 'own_stage_exhausted': True}, order, set())
        assert after_own['active_stage'] == 'branch'
        assert after_own['own_stage_status'] == 'exhausted'
        assert after_own['branch_stage_status'] == 'open'
        assert after_own['dealer_stage_status'] == 'locked'
        assert after_own['factory_stage_status'] == 'locked'

        after_branches = odw.compute_stage_flags(
            {**item, 'own_stage_exhausted': True, 'branch_stage_exhausted': True},
            order,
            set(),
        )
        assert after_branches['active_stage'] == 'dealer'
        assert after_branches['branch_stage_status'] == 'exhausted'
        assert after_branches['dealer_stage_status'] == 'open'
        assert after_branches['factory_stage_status'] == 'locked'

        after_dealers = odw.compute_stage_flags(
            {**item, 'own_stage_exhausted': True, 'branch_stage_exhausted': True, 'dealer_stage_exhausted': True},
            order,
            set(),
        )
        assert after_dealers['active_stage'] == 'factory'
        assert after_dealers['dealer_stage_status'] == 'exhausted'
        assert after_dealers['factory_stage_status'] == 'open'

    def test_order_stage_prefers_own_over_later_tabs(self):
        items = [
            {'own_stage_status': 'open', 'branch_stage_status': 'locked', 'dealer_stage_status': 'locked', 'factory_stage_status': 'locked'},
            {'own_stage_status': 'exhausted', 'branch_stage_status': 'open', 'dealer_stage_status': 'locked', 'factory_stage_status': 'locked'},
        ]
        stage = odw.compute_order_stage(items)
        assert stage['active_stage'] == 'own'
        assert stage['branch_stage_status'] == 'locked'
        assert stage['dealer_stage_status'] == 'locked'


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
