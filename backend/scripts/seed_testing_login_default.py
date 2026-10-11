#!/usr/bin/env python3
"""Seed the attached promotional PNG as the Testing Default Login image."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
ASSET = Path(__file__).resolve().parents[2] / "deploy" / "testing" / "assets" / "login-screen-default.png"


def _refuse() -> None:
    if os.getenv("APP_ENV", "").strip().lower() != "testing":
        raise SystemExit("Refusing: APP_ENV must be testing")
    if os.getenv("DB_NAME", "").strip() != "nmts_testing":
        raise SystemExit("Refusing: DB_NAME must be nmts_testing")
    if os.getenv("NMTS_STORAGE_ENV", "").strip().lower() != "testing":
        raise SystemExit("Refusing: NMTS_STORAGE_ENV must be testing")


async def main() -> int:
    from dotenv import load_dotenv
    from motor.motor_asyncio import AsyncIOMotorClient

    load_dotenv(os.getenv("NMTS_ENV_FILE") or "/opt/nmts-testing/backend/.env", override=True)
    _refuse()
    if not ASSET.is_file():
        raise SystemExit(f"Missing seed image: {ASSET}")

    import login_screen as ls
    from mongo_connection import build_mongo_client_args, resolve_mongo_url

    url, kwargs = build_mongo_client_args(resolve_mongo_url())
    db = AsyncIOMotorClient(url, **kwargs)["nmts_testing"]
    data = ASSET.read_bytes()
    row = await ls.save_default(db, data, "image/png", "login-screen-default.png")
    print("SEEDED", row)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
