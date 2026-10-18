"""Four-level website user role hierarchy.

Roles (storage values → display labels):
  master_admin → Master Admin
  master       → Master
  admin        → Admin
  user         → User

Legacy: historical accounts stored as role=master meant the top tier
(displayed as "Master Admin"). Call migrate_legacy_master_roles() on
startup in Testing/Production to promote the original Master accounts to
master_admin. After migration, role=master is the mid-tier Master role.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional, Sequence

ROLE_MASTER_ADMIN = "master_admin"
ROLE_MASTER = "master"
ROLE_ADMIN = "admin"
ROLE_USER = "user"

ALL_ROLES = (ROLE_MASTER_ADMIN, ROLE_MASTER, ROLE_ADMIN, ROLE_USER)

ROLE_LABELS = {
    ROLE_MASTER_ADMIN: "Master Admin",
    ROLE_MASTER: "Master",
    ROLE_ADMIN: "Admin",
    ROLE_USER: "User",
}

# Original Production / Testing Master accounts that must become Master Admin.
ORIGINAL_MASTER_ADMIN_EMAILS = frozenset({
    "admin@sleepingstock.in",
    "testing.master@sleepingstock.in",
})

# Who may create whom (exact matrix).
_CREATABLE = {
    ROLE_MASTER_ADMIN: (ROLE_MASTER, ROLE_ADMIN, ROLE_USER),
    ROLE_MASTER: (ROLE_ADMIN, ROLE_USER),
    ROLE_ADMIN: (ROLE_USER,),
    ROLE_USER: (),
}


def normalize_role(role: Any) -> str:
    text = str(role or "").strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "masteradmin": ROLE_MASTER_ADMIN,
        "master_admin": ROLE_MASTER_ADMIN,
        "master": ROLE_MASTER,
        "admin": ROLE_ADMIN,
        "user": ROLE_USER,
    }
    return aliases.get(text, text)


def role_label(role: Any) -> str:
    return ROLE_LABELS.get(normalize_role(role), str(role or "").strip() or "User")


def is_master_admin(role: Any) -> bool:
    return normalize_role(role) == ROLE_MASTER_ADMIN


def is_master_level(role: Any) -> bool:
    """Master Admin or Master — full module/scope access (legacy master parity)."""
    return normalize_role(role) in {ROLE_MASTER_ADMIN, ROLE_MASTER}


def is_admin(role: Any) -> bool:
    return normalize_role(role) == ROLE_ADMIN


def is_user(role: Any) -> bool:
    return normalize_role(role) == ROLE_USER


def creatable_roles(actor_role: Any) -> tuple[str, ...]:
    return _CREATABLE.get(normalize_role(actor_role), ())


def can_create_role(actor_role: Any, target_role: Any) -> bool:
    target = normalize_role(target_role)
    if target == ROLE_MASTER_ADMIN:
        return False
    return target in creatable_roles(actor_role)


def is_protected_master_admin_doc(doc: Optional[Mapping[str, Any]]) -> bool:
    """True when the account must never be deleted / deactivated / demoted."""
    if not doc:
        return False
    if is_master_admin(doc.get("role")):
        return True
    email = str(doc.get("email") or "").strip().casefold()
    if email in {e.casefold() for e in ORIGINAL_MASTER_ADMIN_EMAILS}:
        return True
    if bool(doc.get("is_testing_master")):
        return True
    return False


def is_mirrored_production_master_admin(doc: Optional[Mapping[str, Any]]) -> bool:
    """Production Master Admin mirrored into Testing (read-only base row)."""
    if not doc:
        return False
    email = str(doc.get("email") or "").strip().casefold()
    if email != "admin@sleepingstock.in":
        return False
    origin = str(doc.get("data_origin") or "").strip().lower()
    # Untagged production-base rows and explicit production origin.
    return origin in ("", "production", "snapshot")


def creation_forbidden_detail(actor_role: Any, target_role: Any) -> str:
    target = normalize_role(target_role)
    if target == ROLE_MASTER_ADMIN:
        return "No one can create another Master Admin"
    actor = normalize_role(actor_role)
    allowed = creatable_roles(actor)
    if not allowed:
        return "Not authorized to create users"
    labels = ", ".join(ROLE_LABELS[r] for r in allowed)
    return f"{ROLE_LABELS.get(actor, actor)} can only create: {labels}"


async def migrate_legacy_master_roles(db) -> dict:
    """Promote original Master accounts from role=master → master_admin.

    Safe / idempotent. Does not demote any already-correct master_admin.
    Leaves other role=master rows untouched so mid-tier Master accounts can exist.
    """
    promoted = 0
    for email in ORIGINAL_MASTER_ADMIN_EMAILS:
        result = await db.users.update_many(
            {
                "email": {"$regex": f"^{email}$", "$options": "i"},
                "role": {"$in": ["master", ROLE_MASTER]},
            },
            {"$set": {"role": ROLE_MASTER_ADMIN}},
        )
        promoted += int(getattr(result, "modified_count", 0) or 0)
    # Also promote any explicit testing-master flag still on role=master.
    flagged = await db.users.update_many(
        {"is_testing_master": True, "role": {"$in": ["master", ROLE_MASTER]}},
        {"$set": {"role": ROLE_MASTER_ADMIN}},
    )
    promoted += int(getattr(flagged, "modified_count", 0) or 0)
    return {"promoted": promoted}


async def restore_mirrored_master_admin_visibility(db) -> dict:
    """Remove Testing tombstones that hide the mirrored Production Master Admin."""
    try:
        from . import testing_overlay as overlay
        from . import testing_runtime
    except ImportError:
        import testing_overlay as overlay  # type: ignore
        import testing_runtime  # type: ignore

    if not testing_runtime.is_testing_env():
        return {"removed": 0, "skipped": "not_testing"}

    # Find production master admin id(s) visible in the merged users collection.
    ids: list[str] = []
    cursor = db.users.find(
        {"email": {"$regex": r"^admin@sleepingstock\.in$", "$options": "i"}},
        {"_id": 0, "id": 1, "email": 1, "role": 1, "data_origin": 1},
    )
    async for row in cursor:
        if is_mirrored_production_master_admin(row) or is_protected_master_admin_doc(row):
            if row.get("id"):
                ids.append(str(row["id"]))

    if not ids:
        # Also clear any tombstone keyed by known email via production_id lookup of historical ids.
        ids = []

    removed = 0
    tombs = db[overlay.TOMBSTONE_COLLECTION]
    if ids:
        result = await tombs.delete_many(
            {"collection": "users", "production_id": {"$in": ids}}
        )
        removed = int(getattr(result, "deleted_count", 0) or 0)
    # Belt-and-suspenders: drop tombstones whose payload email matches.
    result2 = await tombs.delete_many(
        {
            "collection": "users",
            "$or": [
                {"email": {"$regex": r"^admin@sleepingstock\.in$", "$options": "i"}},
                {"doc.email": {"$regex": r"^admin@sleepingstock\.in$", "$options": "i"}},
            ],
        }
    )
    removed += int(getattr(result2, "deleted_count", 0) or 0)
    return {"removed": removed, "ids": ids}
