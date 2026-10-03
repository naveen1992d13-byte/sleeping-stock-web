"""Upload Center Brand/Dealer/Branch guard — unit tests, no Mongo writes."""
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from fastapi import HTTPException

import upload_scope


class TestUploadScopeGuard:
    def test_all_placeholders_blocked(self):
        msg = upload_scope.upload_scope_error_message('All Brands', 'All Dealers', 'All Branches')
        assert 'Please select Brand.' in msg
        assert 'Please select Dealer.' in msg
        assert 'Please select Branch.' in msg

    def test_single_missing_field(self):
        assert upload_scope.upload_scope_error_message('', 'KUN', 'Ambattur') == 'Please select Brand.'
        assert upload_scope.upload_scope_error_message('Hyundai', '', 'Ambattur') == 'Please select Dealer.'
        assert upload_scope.upload_scope_error_message('Hyundai', 'KUN', 'All Branches') == 'Please select Branch.'

    def test_specific_scope_ok(self):
        assert upload_scope.upload_scope_error_message('Hyundai', 'KUN', 'Ambattur') is None
        upload_scope.require_specific_upload_scope('Hyundai', 'KUN', 'Ambattur')

    def test_require_raises_400(self):
        with pytest.raises(HTTPException) as exc:
            upload_scope.require_specific_upload_scope('All Brands', 'KUN', 'Ambattur')
        assert exc.value.status_code == 400
        assert exc.value.detail == 'Please select Brand.'

    def test_admin_upload_uses_selected_allowed_branch_not_assigned(self):
        branch = upload_scope.resolve_actor_upload_branch(
            role='admin',
            assigned_branch='Vanagaram',
            selected_branch='Koyambedu',
            allowed_branches=['Vanagaram', 'Koyambedu'],
        )
        assert branch == 'Koyambedu'

    def test_admin_upload_rejects_branch_outside_allowed_list(self):
        with pytest.raises(HTTPException) as exc:
            upload_scope.resolve_actor_upload_branch(
                role='admin',
                assigned_branch='Vanagaram',
                selected_branch='Ambattur',
                allowed_branches=['Vanagaram', 'Koyambedu'],
            )
        assert exc.value.status_code == 403

    def test_admin_upload_rejects_all_branches_placeholder(self):
        with pytest.raises(HTTPException) as exc:
            upload_scope.resolve_actor_upload_branch(
                role='Admin',
                assigned_branch='Vanagaram',
                selected_branch='All Branches',
                allowed_branches=['Vanagaram', 'Koyambedu'],
            )
        assert exc.value.status_code == 403

    def test_user_upload_stays_on_assigned_branch(self):
        branch = upload_scope.resolve_actor_upload_branch(
            role='user',
            assigned_branch='Vanagaram',
            selected_branch='Koyambedu',
            allowed_branches=['Vanagaram', 'Koyambedu'],
        )
        assert branch == 'Vanagaram'
