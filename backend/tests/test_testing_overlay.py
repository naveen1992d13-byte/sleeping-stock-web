"""Unit tests for the testing overlay merge layer. No live DocumentDB."""
import asyncio
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import testing_overlay as ov


class FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)

    async def to_list(self, n):
        return self._rows[: int(n)] if n is not None else list(self._rows)


class FakeCollection:
    def __init__(self, rows=None):
        self.rows = list(rows or [])

    def find(self, query=None, projection=None):
        return FakeCursor([r for r in self.rows if ov._doc_matches(r, query or {})])

    async def find_one(self, query=None, projection=None):
        rows = await self.find(query, projection).to_list(1)
        return rows[0] if rows else None

    async def insert_one(self, doc):
        self.rows.append(dict(doc))
        return ov._WriteResult(inserted_id=doc.get("id"))

    async def insert_many(self, docs):
        for d in docs:
            self.rows.append(dict(d))
        return ov._WriteResult(inserted_ids=[d.get("id") for d in docs])

    async def update_one(self, query, update, upsert=False):
        for i, row in enumerate(self.rows):
            if ov._doc_matches(row, query or {}):
                self.rows[i] = ov.apply_mongo_update(row, update)
                return ov._WriteResult(matched_count=1, modified_count=1)
        if upsert:
            payload = {"id": (query or {}).get("id") or (query or {}).get("production_id")}
            payload.update((query or {}))
            payload = ov.apply_mongo_update(payload, update)
            self.rows.append(payload)
            return ov._WriteResult(matched_count=0, modified_count=1, upserted_id=payload.get("id"))
        return ov._WriteResult(matched_count=0, modified_count=0)

    async def delete_one(self, query):
        for i, row in enumerate(self.rows):
            if ov._doc_matches(row, query or {}):
                self.rows.pop(i)
                return ov._WriteResult(deleted_count=1)
        return ov._WriteResult(deleted_count=0)

    async def delete_many(self, query):
        keep = []
        deleted = 0
        for row in self.rows:
            if ov._doc_matches(row, query or {}):
                deleted += 1
            else:
                keep.append(row)
        self.rows = keep
        return ov._WriteResult(deleted_count=deleted)

    async def count_documents(self, query=None):
        return len([r for r in self.rows if ov._doc_matches(r, query or {})])

    async def create_index(self, *args, **kwargs):
        return "ok"


def test_merge_hides_tombstones_and_applies_overlay():
    prod = [{"id": "p1", "qty": 1, "name": "prod"}, {"id": "p2", "qty": 2}]
    created = [{"id": "TS-1", "data_origin": "testing", "qty": 9}]
    overlays = [{"production_id": "p1", "payload": {"qty": 5, "id": "p1"}}]
    merged = ov.merge_documents(prod, created, overlays, {"p2"})
    by_id = {r["id"]: r for r in merged}
    assert by_id["p1"]["qty"] == 5
    assert "p2" not in by_id
    assert by_id["TS-1"]["qty"] == 9


def test_viewing_does_not_require_testing_write():
    prod = [{"id": "p1", "name": "live"}]
    merged = ov.merge_documents(prod, [], [], [])
    assert merged[0]["name"] == "live"
    assert merged[0].get("data_origin") == "production"


def test_apply_set_and_inc():
    doc = {"id": "1", "qty": 2}
    out = ov.apply_mongo_update(doc, {"$set": {"name": "x"}, "$inc": {"qty": 3}})
    assert out["name"] == "x"
    assert out["qty"] == 5


def test_overlay_collection_write_stays_off_production():
    prod = FakeCollection([{"id": "p1", "name": "prod", "qty": 1}])
    test = FakeCollection([])
    overlays = FakeCollection([])
    tombs = FakeCollection([])
    col = ov.OverlayCollection("products", prod, test, overlays, tombs)

    async def _go():
        await col.update_one({"id": "p1"}, {"$set": {"qty": 8}})
        await col.delete_one({"id": "p1"})
        await col.insert_one({"id": "TS-NEW", "name": "created"})
        rows = await col.find({}).to_list(20)
        return rows, prod.rows, test.rows, overlays.rows, tombs.rows

    rows, prod_rows, test_rows, overlay_rows, tomb_rows = asyncio.run(_go())
    assert prod_rows == [{"id": "p1", "name": "prod", "qty": 1}]
    assert any(r.get("id") == "TS-NEW" for r in test_rows)
    assert tomb_rows and tomb_rows[0]["production_id"] == "p1"
    ids = {r["id"] for r in rows}
    assert "p1" not in ids
    assert "TS-NEW" in ids


def test_memory_aggregate_group_sum():
    docs = [{"id": "a", "qty": 2}, {"id": "b", "qty": 3}]
    out = ov._memory_aggregate(docs, [
        {"$group": {"_id": None, "total": {"$sum": "$qty"}, "n": {"$sum": 1}}},
    ])
    assert out[0]["total"] == 5
    assert out[0]["n"] == 2


def test_readonly_blocks_writes():
    inner = FakeCollection([{"id": "1"}])
    ro = ov.ReadOnlyCollection(inner, "products")
    try:
        ro.insert_one({"id": "x"})
        assert False, "expected ProductionWriteBlocked"
    except ov.ProductionWriteBlocked:
        pass


def test_local_only_insert_many_does_not_stamp_origin():
    test = FakeCollection([])
    col = ov.OverlayCollection("testing_overlays", FakeCollection(), test, FakeCollection(), FakeCollection())

    async def _go():
        await col.insert_many([{"collection": "products", "production_id": "p1", "payload": {"qty": 1}}])
        return test.rows

    rows = asyncio.run(_go())
    assert rows[0]["production_id"] == "p1"
    assert "data_origin" not in rows[0]


def test_reset_overlay_restores_production_value():
    test = FakeCollection([])
    overlays = FakeCollection([{"collection": "products", "production_id": "p1", "payload": {"qty": 9}}])
    tombs = FakeCollection([{"collection": "products", "production_id": "p1"}])
    db = {"testing_overlays": overlays, "testing_tombstones": tombs}

    async def _go():
        return await ov.reset_overlay(db, "p1", "products")

    result = asyncio.run(_go())
    assert result["overlay_deleted"] == 1
    assert result["tombstone_deleted"] == 1
    assert overlays.rows == []
    assert tombs.rows == []
