"""Stage B — PROTECTED_MASTER_IDS / original masters cannot be deleted."""
from __future__ import annotations

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_assert_blocks_default_emails(monkeypatch):
    import server

    monkeypatch.delenv("PROTECTED_MASTER_IDS", raising=False)
    monkeypatch.delenv("PROTECTED_MASTER_EMAILS", raising=False)

    mock_db = MagicMock()
    mock_db.users.find_one = AsyncMock(
        return_value={"id": "af9088a5-d9cf-4874-b6aa-60a72f159fa8", "email": "testing.master@sleepingstock.in", "role": "master"}
    )

    async def _run():
        with patch.object(server, "db", mock_db):
            with pytest.raises(server.HTTPException) as exc:
                await server._assert_user_not_protected_master("af9088a5-d9cf-4874-b6aa-60a72f159fa8")
            assert exc.value.status_code == 403
            assert "Protected master" in str(exc.value.detail)

    asyncio.get_event_loop().run_until_complete(_run())


def test_assert_blocks_protected_ids(monkeypatch):
    import server

    monkeypatch.setenv("PROTECTED_MASTER_IDS", "9f775365-74fe-4b21-bc33-a94ce73cc1c0")
    mock_db = MagicMock()
    mock_db.users.find_one = AsyncMock(
        return_value={"id": "9f775365-74fe-4b21-bc33-a94ce73cc1c0", "email": "admin@sleepingstock.in", "role": "master"}
    )

    async def _run():
        with patch.object(server, "db", mock_db):
            with pytest.raises(server.HTTPException) as exc:
                await server._assert_user_not_protected_master("9f775365-74fe-4b21-bc33-a94ce73cc1c0")
            assert exc.value.status_code == 403

    asyncio.get_event_loop().run_until_complete(_run())


def test_assert_allows_normal_user(monkeypatch):
    import server

    monkeypatch.setenv("PROTECTED_MASTER_IDS", "9f775365-74fe-4b21-bc33-a94ce73cc1c0")
    mock_db = MagicMock()
    mock_db.users.find_one = AsyncMock(
        return_value={"id": "user-1", "email": "dealer.user@example.com", "role": "user"}
    )

    async def _run():
        with patch.object(server, "db", mock_db):
            doc = await server._assert_user_not_protected_master("user-1")
        assert doc["id"] == "user-1"

    asyncio.get_event_loop().run_until_complete(_run())
