"""Focused unit tests for branch-request Expo push channel/sound alignment."""
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import mobile_push


def test_branch_request_push_uses_mobile_channel_and_custom_sound():
    group = {
        'id': 'g1',
        'request_number': 'RQHY2609150001',
        'requesting_branch': 'Vanagaram',
        'total_items': 3,
        'total_qty': 12,
    }
    message = mobile_push.build_branch_request_push_message('ExponentPushToken[abc]', group, 'new')
    assert 'title' not in message
    assert 'body' not in message
    assert 'sound' not in message
    assert 'channelId' not in message
    assert message['priority'] == 'high'
    assert message['_contentAvailable'] is True
    assert message['data']['type'] == 'branch_request'
    assert message['data']['requestId'] == 'g1'
    assert message['data']['request_group_key'] == 'g1'
    assert message['data']['requestNumber'] == 'RQHY2609150001'
    assert message['data']['branchName'] == 'Vanagaram'
    assert message['data']['totalItems'] == 3
    assert message['data']['totalQuantity'] == 12
    assert message['data']['round'] == 0
    reminder = mobile_push.build_branch_request_push_message('ExponentPushToken[abc]', group, 'reminder_2')
    assert reminder['data']['round'] == 2
    assert 'title' not in reminder
