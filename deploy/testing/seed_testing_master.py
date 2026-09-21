#!/usr/bin/env python3
"""One-time seed: exactly one Testing Master Admin in the testing database.

Uses the existing `master` role as-is, with the full Master Admin module
permission list. Does not create Brand/Dealer/Branch data or Admin/User
accounts.

Password is generated at run time and printed once. It is not stored in this
file. Re-running without --reset applies permission parity and does not rotate
the password.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import secrets
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from passlib.context import CryptContext

BACKEND = Path(os.environ.get("NMTS_TESTING_BACKEND", "/opt/nmts-testing/backend"))
ENV_FILE = BACKEND / ".env"

TESTING_EMAIL = "testing.master@sleepingstock.in"
TESTING_USERNAME = "Testing Master Admin"
TESTING_ROLE = "master"


def _load_env() -> None:
    if not ENV_FILE.is_file():
        raise SystemExit(f"Testing backend .env not found: {ENV_FILE}")
    load_dotenv(ENV_FILE, override=True)


async def seed(reset: bool) -> int:
    _load_env()
    db_name = (os.environ.get("DB_NAME") or "").strip()
    if db_name != "nmts_testing":
        raise SystemExit(f"Refusing to seed: DB_NAME={db_name!r} (expected nmts_testing)")

    sys.path.insert(0, str(BACKEND))
    from motor.motor_asyncio import AsyncIOMotorClient
    from mongo_connection import build_mongo_client_args, resolve_mongo_url
    from testing_snapshot import testing_master_parity_fields

    mongo_url = resolve_mongo_url()
    url, kwargs = build_mongo_client_args(mongo_url)
    client = AsyncIOMotorClient(url, **kwargs)
    db = client[db_name]
    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

    existing = await db.users.find_one({"email": TESTING_EMAIL}, {"_id": 0, "password": 0})
    parity = testing_master_parity_fields()
    if not existing or not existing.get("user_id"):
        parity["user_id"] = "TS-SSMASTER01"

    if existing and not reset:
        await db.users.update_one({"email": TESTING_EMAIL}, {"$set": parity})
        print(f"PARITY_APPLIED email={TESTING_EMAIL} role=master id={existing.get('id')}")
        print(f"permissions={len(parity['permissions'])} modules")
        print("Password was not rotated. Pass --reset to rotate it.")
        client.close()
        return 0

    password = secrets.token_urlsafe(18)
    hashed = pwd_context.hash(password)
    now = datetime.now(timezone.utc).isoformat()

    if existing:
        await db.users.update_one(
            {"email": TESTING_EMAIL},
            {"$set": {**parity, "password": hashed}},
        )
        action = "RESET"
        user_id = existing.get("id")
    else:
        user_id = str(uuid.uuid4())
        await db.users.insert_one({
            "id": user_id,
            "email": TESTING_EMAIL,
            "password": hashed,
            "phone": "",
            "last_login": None,
            "created_at": now,
            **parity,
        })
        action = "CREATED"

    client.close()
    print(f"{action} Testing Master Admin")
    print(f"email={TESTING_EMAIL}")
    print(f"username={TESTING_USERNAME}")
    print(f"role={TESTING_ROLE}")
    print(f"id={user_id}")
    print(f"permissions={len(parity['permissions'])} modules")
    print(f"password={password}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", help="Rotate the testing master password")
    args = parser.parse_args()
    return asyncio.run(seed(args.reset))


if __name__ == "__main__":
    raise SystemExit(main())
