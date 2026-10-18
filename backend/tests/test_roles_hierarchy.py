"""Unit tests for four-level website role hierarchy."""
from __future__ import annotations

import roles as role_defs


def test_normalize_and_labels():
    assert role_defs.normalize_role("Master Admin") == "master_admin"
    assert role_defs.normalize_role("master-admin") == "master_admin"
    assert role_defs.role_label("master_admin") == "Master Admin"
    assert role_defs.role_label("master") == "Master"


def test_creatable_matrix():
    assert role_defs.creatable_roles("master_admin") == ("master", "admin", "user")
    assert role_defs.creatable_roles("master") == ("admin", "user")
    assert role_defs.creatable_roles("admin") == ("user",)
    assert role_defs.creatable_roles("user") == ()


def test_nobody_can_create_master_admin():
    for actor in ("master_admin", "master", "admin", "user"):
        assert role_defs.can_create_role(actor, "master_admin") is False


def test_can_create_allowed_roles():
    assert role_defs.can_create_role("master_admin", "master") is True
    assert role_defs.can_create_role("master", "admin") is True
    assert role_defs.can_create_role("master", "master") is False
    assert role_defs.can_create_role("admin", "user") is True
    assert role_defs.can_create_role("admin", "admin") is False


def test_protected_master_admin_doc():
    assert role_defs.is_protected_master_admin_doc({"role": "master_admin"}) is True
    assert role_defs.is_protected_master_admin_doc(
        {"role": "master", "email": "admin@sleepingstock.in"}
    ) is True
    assert role_defs.is_protected_master_admin_doc(
        {"role": "master", "email": "testing.master@sleepingstock.in"}
    ) is True
    assert role_defs.is_protected_master_admin_doc(
        {"role": "user", "email": "someone@example.com"}
    ) is False


def test_mirrored_production_master_admin():
    assert role_defs.is_mirrored_production_master_admin(
        {"email": "admin@sleepingstock.in", "data_origin": "production"}
    ) is True
    assert role_defs.is_mirrored_production_master_admin(
        {"email": "admin@sleepingstock.in", "data_origin": ""}
    ) is True
    assert role_defs.is_mirrored_production_master_admin(
        {"email": "testing.master@sleepingstock.in", "data_origin": "testing"}
    ) is False
