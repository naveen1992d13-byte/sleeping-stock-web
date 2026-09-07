"""Unit tests for request email recipient resolution."""
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from notifications import select_request_receiver_email


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


def test_prefers_supplying_branch_user_over_admin_and_master():
    email = select_request_receiver_email(
        USERS, brand='Hyundai', dealer='FPL Automobiles PVT LTD', branch='Koyambedu',
    )
    assert email == 'koyambedu.user@gmail.com'


def test_branch_match_is_case_insensitive():
    email = select_request_receiver_email(
        USERS, brand='hyundai', dealer='fpl automobiles pvt ltd', branch='vanagaram',
    )
    assert email == 'vanagaram.user@gmail.com'


def test_dealer_admin_fallback_when_branch_user_missing():
    email = select_request_receiver_email(
        USERS, brand='Hyundai', dealer='FPL Automobiles PVT LTD', branch='Unknown Branch',
    )
    assert email == 'dealer.admin@gmail.com'


def test_master_fallback_does_not_require_brand():
    email = select_request_receiver_email(
        USERS, brand='Hyundai', dealer='Unknown Dealer', branch='Unknown Branch',
    )
    assert email == 'admin@sleepingstock.in'


def test_old_admin_only_lookup_would_miss_branch_user():
    admin_only = [u for u in USERS if u['role'] == 'admin' and u['location'] == 'Koyambedu']
    assert admin_only == []
    email = select_request_receiver_email(
        USERS, brand='Hyundai', dealer='FPL Automobiles PVT LTD', branch='Koyambedu',
    )
    assert email == 'koyambedu.user@gmail.com'
