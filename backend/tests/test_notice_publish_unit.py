import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import notice_board as nb


class _StaleReadNotices:
    """find_one always returns the Draft snapshot, as a concurrent second click would see it."""

    def __init__(self):
        self.doc = {"id": "n1", "status": "Draft"}

    async def find_one(self, query, *args, **kwargs):
        return {"id": "n1", "status": "Draft"}

    async def find_one_and_update(self, query, update, **kwargs):
        if any(self.doc.get(k) != v for k, v in query.items()):
            return None
        self.doc.update(update["$set"])
        return dict(self.doc)


class _Audit:
    def __init__(self):
        self.rows = []

    async def insert_one(self, doc):
        self.rows.append(doc)


def test_second_concurrent_publish_returns_conflict_without_side_effects(monkeypatch):
    audit = _Audit()
    monkeypatch.setattr(nb, "db", SimpleNamespace(notices=_StaleReadNotices(), notice_audit_logs=audit))
    master = SimpleNamespace(id="m1", username="master", role="master")

    async def _go():
        first = await nb.publish_notice("n1", master)
        with pytest.raises(HTTPException) as exc:
            await nb.publish_notice("n1", master)
        return first, exc.value

    first, err = asyncio.run(_go())
    assert first["status"] == "Published"
    assert err.status_code == 409
    assert [r["action"] for r in audit.rows] == ["notice_published"]
