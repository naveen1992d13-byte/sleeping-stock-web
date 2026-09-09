"""Unit tests for request email TO/CC routing and subject format."""
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from notifications import (
    build_request_email_subject,
    resolve_request_email_routing,
    send_request_pdf_email,
)


USERS = [
    {
        'email': 'koyambedu.user@gmail.com',
        'role': 'user',
        'group': 'FPL Automobiles PVT LTD',
        'location': 'Koyambedu',
        'status': 'active',
        'brand': 'Hyundai',
    },
    {
        'email': 'vanagaram.user@gmail.com',
        'role': 'user',
        'group': 'FPL Automobiles PVT LTD',
        'location': 'Vanagaram',
        'status': 'active',
        'brand': 'Hyundai',
    },
    {
        'email': 'dealer.admin@gmail.com',
        'role': 'admin',
        'group': 'FPL Automobiles PVT LTD',
        'location': '',
        'status': 'active',
        'brand': 'Hyundai',
    },
    {
        'email': 'admin@sleepingstock.in',
        'role': 'master',
        'group': '',
        'location': '',
        'status': 'active',
        'brand': '',
    },
]


GROUP = {
    'request_number': 'RQHY2609080001',
    'supplying_dealer': 'FPL Automobiles PVT LTD',
    'supplying_branch': 'Koyambedu',
    'requesting_dealer': 'FPL Automobiles PVT LTD',
    'requesting_branch': 'Vanagaram',
}


def test_to_is_supplying_branch_user_only():
    to_emails, cc_emails = resolve_request_email_routing(USERS, GROUP)
    assert to_emails == ['koyambedu.user@gmail.com']
    assert 'koyambedu.user@gmail.com' not in cc_emails


def test_cc_includes_requesting_user_and_optional_admin():
    to_emails, cc_emails = resolve_request_email_routing(USERS, GROUP)
    assert to_emails == ['koyambedu.user@gmail.com']
    assert 'vanagaram.user@gmail.com' in cc_emails
    assert 'dealer.admin@gmail.com' in cc_emails


def test_master_admin_is_never_to_or_cc():
    to_emails, cc_emails = resolve_request_email_routing(USERS, GROUP)
    assert 'admin@sleepingstock.in' not in to_emails
    assert 'admin@sleepingstock.in' not in cc_emails


def test_missing_admin_does_not_block_to():
    no_admin = [u for u in USERS if u['role'] != 'admin']
    to_emails, cc_emails = resolve_request_email_routing(no_admin, GROUP)
    assert to_emails == ['koyambedu.user@gmail.com']
    assert 'vanagaram.user@gmail.com' in cc_emails
    assert 'dealer.admin@gmail.com' not in cc_emails


def test_branch_match_is_case_insensitive():
    group = dict(GROUP, supplying_dealer='fpl automobiles pvt ltd', supplying_branch='koyambedu',
                 requesting_dealer='FPL AUTOMOBILES PVT LTD', requesting_branch='VANAGARAM')
    to_emails, cc_emails = resolve_request_email_routing(USERS, group)
    assert to_emails == ['koyambedu.user@gmail.com']
    assert 'vanagaram.user@gmail.com' in cc_emails


def test_missing_supplying_user_does_not_fall_back_to_admin_or_master():
    to_emails, cc_emails = resolve_request_email_routing(
        USERS,
        dict(GROUP, supplying_branch='Unknown Branch'),
    )
    assert to_emails == []
    assert 'dealer.admin@gmail.com' in cc_emails
    assert 'admin@sleepingstock.in' not in cc_emails


def test_subject_uses_finalized_sleeping_stock_request_format():
    subject = build_request_email_subject(GROUP)
    assert subject == 'Sleeping Stock Request – FPL Automobiles PVT LTD Koyambedu – RQHY2609080001'


def test_invalid_to_skips_without_raising():
    result = send_request_pdf_email('invalid', GROUP, b'%PDF')
    assert result.get('status') in ('skipped', 'failed')
