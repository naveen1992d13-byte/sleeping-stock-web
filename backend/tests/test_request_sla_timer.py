"""Focused tests: per-request SLA timer start, freeze, and fresh next-branch timer."""
from datetime import datetime, timedelta, timezone
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import order_desk_workflow as odw


def test_new_request_starts_full_sla_not_previous_remaining():
    now = datetime.now(timezone.utc)
    previous = odw.compute_response_schedule(5, now - timedelta(minutes=20))
    prev_eval = odw.evaluate_group_timer({**previous, 'status': 'Requested'}, now)
    assert prev_eval['countdown_active'] is True
    assert prev_eval['remaining_seconds'] <= 11 * 60

    nxt = odw.compute_response_schedule(5, now)
    new_eval = odw.evaluate_group_timer({**nxt, 'status': 'Requested'}, now)
    assert nxt['timer_frozen'] is False
    assert new_eval['countdown_active'] is True
    assert new_eval['response_time_minutes'] == 30
    assert new_eval['remaining_seconds'] >= 29 * 60
    assert new_eval['remaining_seconds'] > prev_eval['remaining_seconds']
    assert nxt['request_sent_at'] != previous['request_sent_at']
    assert nxt['response_deadline'] != previous['response_deadline']


def test_response_freezes_timer_and_stops_countdown():
    now = datetime.now(timezone.utc)
    header = {**odw.compute_response_schedule(5, now - timedelta(minutes=8)), 'status': 'Requested'}
    live = odw.evaluate_group_timer(header, now)
    assert live['countdown_active'] is True
    frozen = {**header, **odw.freeze_response_timer(header, now, 'responded'), 'status': 'Approved'}
    stopped = odw.evaluate_group_timer(frozen, now)
    later = odw.evaluate_group_timer(frozen, now + timedelta(minutes=10))
    assert stopped['countdown_active'] is False
    assert stopped['timer_frozen'] is True
    assert later['countdown_active'] is False
    assert later['remaining_seconds'] == stopped['remaining_seconds']
    assert later['remaining_seconds'] == live['remaining_seconds']


def test_each_terminal_status_stops_timer():
    now = datetime.now(timezone.utc)
    base = odw.compute_response_schedule(5, now - timedelta(minutes=3))
    for status, expected in (
        ('Approved', 'responded'),
        ('Partially Approved', 'responded'),
        ('Rejected', 'responded'),
        ('Cancelled', 'cancelled'),
        ('Completed', 'responded'),
        ('Dispatched', 'responded'),
        ('Received', 'responded'),
    ):
        header = {**base, 'status': status}
        timer = odw.evaluate_group_timer(header, now + timedelta(minutes=20))
        assert timer['countdown_active'] is False, status
        assert timer['timer_frozen'] is True, status
        assert timer['response_status'] == expected, status


def test_timeout_freeze_does_not_keep_counting():
    now = datetime.now(timezone.utc)
    header = {
        **odw.compute_response_schedule(5, now - timedelta(minutes=40)),
        'status': 'Requested',
    }
    expired = odw.evaluate_group_timer(header, now)
    assert expired['response_status'] == 'expired'
    assert expired['countdown_active'] is False
    frozen = {**header, **odw.freeze_response_timer(header, now, 'timeout'), 'status': 'Cancelled', 'timeout_cancelled': True}
    later = odw.evaluate_group_timer(frozen, now + timedelta(minutes=15))
    assert later['countdown_active'] is False
    assert later['timer_frozen'] is True
    assert later['response_status'] == 'timeout'
    assert later['remaining_seconds'] == 0


def test_history_row_countdown_only_while_requested():
    now = datetime.now(timezone.utc)
    header = {**odw.compute_response_schedule(5, now), 'status': 'Requested'}
    timer = odw.evaluate_group_timer(header, now)
    item = {'id': 'i1', 'required_qty': 4, 'allocations': []}
    order = {'dealer_name': 'KUN', 'branch': 'Vanagaram'}
    reqs = [
        {
            'id': 'r1', 'status': 'Approved', 'requested_qty': 2, 'accepted_qty': 1,
            'supplying_dealer': 'KUN', 'supplying_branch': 'Koyambedu', 'request_number': 'RQ-OLD',
        },
        {
            'id': 'r2', 'status': 'Requested', 'requested_qty': 2, 'accepted_qty': 0,
            'supplying_dealer': 'KUN', 'supplying_branch': 'Ambattur', 'request_number': 'RQ-NEW',
        },
    ]
    headers = {
        'RQ-OLD': {**header, 'status': 'Approved', **odw.freeze_response_timer(header, now, 'responded')},
        'RQ-NEW': header,
    }
    timers = {k: odw.evaluate_group_timer(v, now) for k, v in headers.items()}
    wf = odw.compute_item_workflow(item, order, reqs, header_timers=timers)
    by_no = {row['request_no']: row for row in wf['request_history']}
    assert by_no['RQ-OLD']['countdown_active'] is False
    assert by_no['RQ-NEW']['countdown_active'] is True
    assert by_no['RQ-NEW']['remaining_seconds'] >= 29 * 60
