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

    def sort(self, key_or_list, direction=None):
        spec = [(key_or_list, direction)] if direction is not None else key_or_list
        pairs = ov._sort_pairs(spec)
        if pairs:
            self._rows.sort(key=lambda row: ov._sort_key_tuple(row, pairs))
        return self

    def skip(self, count):
        self._rows = self._rows[int(count or 0):]
        return self

    def limit(self, count):
        if count is not None:
            self._rows = self._rows[: int(count)]
        return self

    async def to_list(self, n):
        return self._rows[: int(n)] if n is not None else list(self._rows)

    async def __aiter__(self):
        for row in self._rows:
            yield row


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

    def aggregate(self, pipeline, *args, **kwargs):
        return FakeCursor(ov._memory_aggregate(list(self.rows), list(pipeline or [])))

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
    for method in (
        "insert_one", "insert_many", "update_one", "update_many", "replace_one",
        "delete_one", "delete_many", "bulk_write", "find_one_and_update",
        "find_one_and_replace", "find_one_and_delete", "find_one_and_modify",
        "create_index",
    ):
        try:
            getattr(ro, method)({"id": "x"})
            assert False, f"expected ProductionWriteBlocked for {method}"
        except ov.ProductionWriteBlocked:
            pass
    try:
        ro.drop()
        assert False, "expected ProductionWriteBlocked for drop via getattr"
    except ov.ProductionWriteBlocked:
        pass
    try:
        ro.insert_something({"id": "x"})
        assert False, "expected ProductionWriteBlocked for getattr insert fallback"
    except ov.ProductionWriteBlocked:
        pass


def test_wrap_requires_readonly_url(monkeypatch):
    monkeypatch.delenv("MONGO_READONLY_URL", raising=False)
    monkeypatch.delenv("DOCDB_READONLY_SECRET_ID", raising=False)
    monkeypatch.delenv("SNAPSHOT_SOURCE_DOCDB_SECRET_ID", raising=False)
    try:
        ov.resolve_production_readonly_url()
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "MONGO_READONLY_URL" in str(exc)


class RangeCursor:
    def __init__(self, n, skip=0, limit=None):
        self.n = n
        self._skip = skip
        self._limit = limit

    def sort(self, *args, **kwargs):
        return self

    def skip(self, count):
        self._skip = int(count or 0)
        return self

    def limit(self, count):
        self._limit = int(count) if count is not None else None
        return self

    async def to_list(self, n):
        start = self._skip
        end = self.n
        if self._limit is not None:
            end = min(end, start + self._limit)
        if n is not None:
            end = min(end, start + int(n))
        return [{"id": str(i), "qty": 1} for i in range(start, end)]

    async def __aiter__(self):
        start = self._skip
        end = self.n
        if self._limit is not None:
            end = min(end, start + self._limit)
        for i in range(start, end):
            yield {"id": str(i), "qty": 1}


class RangeCollection:
    def __init__(self, n):
        self.n = n

    def find(self, query=None, projection=None):
        return RangeCursor(self.n)

    async def count_documents(self, query=None):
        return self.n

    async def find_one(self, query=None, projection=None):
        return {"id": "0", "qty": 1} if self.n else None


def test_overlay_count_and_pagination_beyond_previous_cap():
    prod = RangeCollection(200010)
    col = ov.OverlayCollection("products", prod, FakeCollection(), FakeCollection(), FakeCollection())

    async def _go():
        total = await col.count_documents({})
        page = await col.find({}).skip(200000).limit(10).to_list(10)
        return total, page

    total, page = asyncio.run(_go())
    assert total == 200010
    assert [row["id"] for row in page] == [str(i) for i in range(200000, 200010)]


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


def test_sorted_pagination_merges_deltas_without_truncation():
    prod = FakeCollection([
        {"id": "a", "part_number": "P1", "qty": 1},
        {"id": "b", "part_number": "P2", "qty": 1},
        {"id": "c", "part_number": "P3", "qty": 1},
        {"id": "d", "part_number": "P4", "qty": 1},
    ])
    created = FakeCollection([
        {"id": "TS-1", "part_number": "P15", "qty": 9, "data_origin": "testing"},
    ])
    overlays = FakeCollection([
        {"collection": "products", "production_id": "b", "payload": {"id": "b", "part_number": "P25", "qty": 4}},
    ])
    tombs = FakeCollection([{"collection": "products", "production_id": "c"}])
    col = ov.OverlayCollection("products", prod, created, overlays, tombs)

    async def _go():
        total = await col.count_documents({})
        page = await col.find({}).sort("part_number", 1).skip(1).limit(2).to_list(2)
        grouped = await col.aggregate([
            {"$match": {}},
            {"$group": {"_id": None, "n": {"$sum": 1}, "qty": {"$sum": "$qty"}}},
        ]).to_list(10)
        return total, page, grouped

    total, page, grouped = asyncio.run(_go())
    assert total == 4
    assert [row["id"] for row in page] == ["TS-1", "b"]
    assert grouped[0]["n"] == 4
    assert grouped[0]["qty"] == 1 + 4 + 1 + 9
    assert prod.rows[1]["qty"] == 1

    async def _async_for():
        ids = []
        async for row in col.find({}).sort("part_number", 1):
            ids.append(row["id"])
        return ids

    assert asyncio.run(_async_for()) == ["a", "TS-1", "b", "d"]


def test_ops_db_does_not_bool_motor_database():
    import testing_verify as tv

    class MotorLike:
        def __bool__(self):
            raise NotImplementedError("Database objects do not implement truth value testing")

    raw = MotorLike()
    fallback = object()
    try:
        _ = raw or fallback
        assert False, "Motor-like bool should raise"
    except NotImplementedError:
        pass
    assert tv.ops_db(raw, fallback) is raw
    assert tv.ops_db(None, fallback) is fallback
