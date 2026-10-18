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


async def migrate_legacy_master_roles(db, raw_db=None) -> dict:
    """Promote original Master accounts from role=master → master_admin.

    Safe / idempotent. Does not demote any already-correct master_admin.
    Leaves other role=master rows untouched so mid-tier Master accounts can exist.

    In Testing, also updates the raw nmts_testing.users collection so snapshot
    copies (data_origin=snapshot) that are invisible to the merged overlay view
    still get promoted.
    """
    targets = [db]
    if raw_db is not None and raw_db is not db:
        targets.append(raw_db)

    promoted = 0
    for target in targets:
        for email in ORIGINAL_MASTER_ADMIN_EMAILS:
            result = await target.users.update_many(
                {
                    "email": {"$regex": f"^{email}$", "$options": "i"},
                    "role": {"$in": ["master", ROLE_MASTER]},
                },
                {"$set": {"role": ROLE_MASTER_ADMIN}},
            )
            promoted += int(getattr(result, "modified_count", 0) or 0)
        flagged = await target.users.update_many(
            {"is_testing_master": True, "role": {"$in": ["master", ROLE_MASTER]}},
            {"$set": {"role": ROLE_MASTER_ADMIN}},
        )
        promoted += int(getattr(flagged, "modified_count", 0) or 0)
    return {"promoted": promoted}


async def hide_mirrored_production_master_admin(db, raw_db=None) -> dict:
    """Hide Production admin@ from Testing merge (credential isolation).

    Writes tombstones directly on the raw Testing DB so API delete guards that
    block user-driven tombstones of mirrored masters do not apply. Never
    touches Production. Testing Master Admin (testing.master@) is never hidden.
    """
    try:
        from . import testing_overlay as overlay
        from . import testing_runtime
    except ImportError:
        import testing_overlay as overlay  # type: ignore
        import testing_runtime  # type: ignore

    if not testing_runtime.is_testing_env():
        return {"tombstoned": 0, "skipped": "not_testing"}

    tombs_db = raw_db if raw_db is not None else db
    ids: list[str] = []

    # Production-base rows (live read via overlay).
    try:
        cursor = db.users.find(
            {
                overlay.QUERY_BASE_ONLY: True,
                "email": {"$regex": r"^admin@sleepingstock\.in$", "$options": "i"},
            },
            {"_id": 0, "id": 1, "email": 1, "data_origin": 1},
        )
        async for row in cursor:
            if row.get("id") and is_mirrored_production_master_admin(row):
                ids.append(str(row["id"]))
    except Exception:
        pass

    # Snapshot leftovers of admin@ in nmts_testing (not testing-created).
    if raw_db is not None:
        cursor = raw_db.users.find(
            {"email": {"$regex": r"^admin@sleepingstock\.in$", "$options": "i"}},
            {"_id": 0, "id": 1, "email": 1, "data_origin": 1, "is_testing_master": 1},
        )
        async for row in cursor:
            origin = str(row.get("data_origin") or "").strip().lower()
            if origin in ("", "production", "snapshot") and row.get("id"):
                ids.append(str(row["id"]))
            # Remove snapshot copy of Production admin — never keep Prod credential hash.
            if origin == "snapshot" and not bool(row.get("is_testing_master")):
                await raw_db.users.delete_one({"id": row["id"]})

    ids = sorted({i for i in ids if i})
    tombs = tombs_db[overlay.TOMBSTONE_COLLECTION]
    tombstoned = 0
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    for ident in ids:
        result = await tombs.update_one(
            {"collection": "users", "production_id": ident},
            {
                "$set": {
                    "collection": "users",
                    "production_id": ident,
                    "op": "tombstone",
                    "reason": "hide_mirrored_production_master_admin",
                    "updated_at": now,
                },
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )
        if getattr(result, "upserted_id", None) is not None or int(getattr(result, "modified_count", 0) or 0):
            tombstoned += 1
    return {"tombstoned": tombstoned, "ids": ids}


# Back-compat alias — previous name restored visibility; now hides Prod credential.
async def restore_mirrored_master_admin_visibility(db, raw_db=None) -> dict:
    return await hide_mirrored_production_master_admin(db, raw_db=raw_db)
