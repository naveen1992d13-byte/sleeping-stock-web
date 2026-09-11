"""Focused tests for request push payload (sound, channel, routing data). Does not hit live HTTP."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import mobile_push


def test_request_push_message_uses_custom_sound_and_high_priority_channel():
    group = {
        'id': 'group-1',
        'request_number': 'RQHY2609110001',
        'requesting_dealer': 'KUN Auto Company PVT LTD',
        'requesting_branch': 'Chromepet',
        'supplying_dealer': 'FPL Automobiles PVT LTD',
        'supplying_branch': 'Vanagaram',
        'total_items': 3,
        'total_qty': 12,
        'response_deadline': (datetime.now(timezone.utc) + timedelta(minutes=18)).isoformat(),
    }
    msg = mobile_push.build_branch_request_message('ExponentPushToken[abc]', group, 'new')
    assert msg['sound'] == 'nmts-request-ring'
    assert msg['channelId'] == 'sleeping-stock-requests-v2'
    assert msg['categoryId'] == 'branch-request'
    assert msg['priority'] == 'high'
    assert msg['data']['request_group_key'] == 'group-1'
    assert msg['data']['request_number'] == 'RQHY2609110001'
    assert msg['data']['requesting_dealer'] == 'KUN Auto Company PVT LTD'
    assert msg['data']['supplying_branch'] == 'Vanagaram'
    assert msg['data']['total_items'] == 3
    assert msg['data']['sla_remaining_seconds'] > 0


def test_reminder_kinds_keep_same_sound_and_three_sla_copy_slots():
    assert set(mobile_push._REQUEST_PUSH_COPY) == {'new', 'reminder_1', 'reminder_2', 'reminder_3'}
    group = {'id': 'g2', 'request_number': 'RQ-2', 'supplying_dealer': 'A', 'supplying_branch': 'B'}
    for kind in ('reminder_1', 'reminder_2', 'reminder_3'):
        msg = mobile_push.build_branch_request_message('ExponentPushToken[xyz]', group, kind)
        assert msg['sound'] == 'nmts-request-ring'
        assert msg['data']['kind'] == kind
        assert msg['data']['request_group_key'] == 'g2'
