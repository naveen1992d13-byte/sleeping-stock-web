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
    assert message['channelId'] == 'sleeping-stock-requests-v3'
    assert message['sound'] == 'sleeping_stock_alert_2_rising_dispatch.wav'
    assert message['categoryId'] == 'branch-request'
    assert message['priority'] == 'high'
    assert message['title'] == 'RQHY2609150001'
    assert 'Requested Branch: Vanagaram' in message['body']
    assert 'Items: 3' in message['body']
    assert 'Qty: 12' in message['body']
    assert message['data']['type'] == 'branch_request'
    assert message['data']['request_group_key'] == 'g1'
    assert message['data']['categoryId'] == 'branch-request'
    assert message['data']['sticky'] is True
    assert message['data']['autoDismiss'] is False
