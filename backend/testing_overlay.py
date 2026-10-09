"""Testing overlay / delta layer.

Production DocumentDB is a read-only base. Testing stores only:
  - testing_overlays   (edits of a production id)
  - testing_tombstones (hide a production id)
  - native testing-created docs (data_origin=testing and/or TS- prefix)

Reads merge: production + overlays + testing-created − tombstones.
Writes never touch the production database.
Viewing production data does not insert testing records.
"""
from __future__ import annotations

import copy
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

try:
    from . import testing_runtime
except ImportError:
    import testing_runtime

logger = logging.getLogger("nmts.testing_overlay")

OVERLAY_COLLECTION = "testing_overlays"
TOMBSTONE_COLLECTION = "testing_tombstones"
AUDIT_COLLECTION = "testing_audit_receipts"
OPERATION_COLLECTION = "testing_operations"

QUERY_BASE_ONLY = "_nmts_base_only"
QUERY_TESTING_ONLY = "_nmts_testing_only"

TS_PREFIX = testing_runtime.TS_PREFIX
DATA_ORIGIN_TESTING = testing_runtime.DATA_ORIGIN_TESTING
DATA_ORIGIN_SNAPSHOT = testing_runtime.DATA_ORIGIN_SNAPSHOT

# Collections that stay testing-local (never read production, never overlay).
LOCAL_ONLY_COLLECTIONS = frozenset({
    OVERLAY_COLLECTION,
    TOMBSTONE_COLLECTION,
    AUDIT_COLLECTION,
    OPERATION_COLLECTION,
    "testing_snapshot_meta",
})

# Business collections merged from production + testing deltas.
OVERLAY_COLLECTIONS = frozenset({
    "products", "upload_items", "uploads", "batch_summaries",
    "order_headers", "order_items", "order_requests", "order_activity",
    "request_headers", "stock_reservations",
    "users", "brands", "dealers", "branches", "states", "groups",
    "user_alerts", "notices", "notice_attachments", "notice_user_status",
    "queries", "query_attachments",
    "templates",
    "stock_verifications", "stock_verification_sessions", "stock_verification_history",
})

_ID_FIELDS = ("id", "user_id", "upload_id", "order_id", "request_id")


class ProductionWriteBlocked(RuntimeError):
    """Raised when any write is attempted against the production read-only handle."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _strip_overlay_flags(query: Optional[Mapping[str, Any]]) -> tuple[dict, bool, bool]:
    payload = dict(query or {})
    base_only = bool(payload.pop(QUERY_BASE_ONLY, False))
    testing_only = bool(payload.pop(QUERY_TESTING_ONLY, False))
    return payload, base_only, testing_only


def origin_overlay_clause(origin: Optional[str] = None) -> dict[str, Any]:
    """Replace snapshot-copy filters when overlay mode is on."""
    if not testing_runtime.is_testing_env():
        return {}
    requested = str(origin or "all").strip().lower()
    if requested in {"snapshot", "snapshot_reference", "reference", "production", "base"}:
        return {QUERY_BASE_ONLY: True}
    if requested in {"testing", "testing_created", "created"}:
        return {QUERY_TESTING_ONLY: True}
    return {}


def is_testing_created_doc(doc: Mapping[str, Any] | None) -> bool:
    if not doc:
        return False
    origin = str(doc.get("data_origin") or "").strip().lower()
    if origin == DATA_ORIGIN_TESTING:
        return True
    if origin == DATA_ORIGIN_SNAPSHOT:
        return False
    for key in ("order_number", "request_number", "upload_no", "user_id", "id"):
        val = str(doc.get(key) or "")
        if val.startswith(TS_PREFIX):
            return True
    return False


def doc_id(doc: Mapping[str, Any] | None) -> str:
    if not doc:
        return ""
    for key in _ID_FIELDS:
        val = doc.get(key)
        if val:
            return str(val)
    if doc.get("_id") is not None:
        return str(doc["_id"])
    return ""


def apply_mongo_update(base: dict, update: Mapping[str, Any]) -> dict:
    """Apply a subset of Mongo update operators in memory for overlay payloads."""
    out = copy.deepcopy(base)
    if not update:
        return out
    if any(str(k).startswith("$") for k in update.keys()):
        if "$set" in update and isinstance(update["$set"], Mapping):
            out.update(update["$set"])
        if "$unset" in update and isinstance(update["$unset"], Mapping):
            for key in update["$unset"]:
                out.pop(key, None)
        if "$inc" in update and isinstance(update["$inc"], Mapping):
            for key, amount in update["$inc"].items():
                try:
                    out[key] = type(amount)(out.get(key, 0) or 0) + amount
                except (TypeError, ValueError):
                    out[key] = amount
        if "$push" in update and isinstance(update["$push"], Mapping):
            for key, val in update["$push"].items():
                cur = out.get(key)
                if not isinstance(cur, list):
                    cur = []
                cur.append(val)
                out[key] = cur
        return out
    out.update(update)
    return out


def merge_documents(
    production: Sequence[Mapping[str, Any]],
    testing_created: Sequence[Mapping[str, Any]],
    overlays: Sequence[Mapping[str, Any]],
    tombstone_ids: Iterable[str],
) -> List[dict]:
    """Merge production base + overlays + testing-created − tombstones by document id."""
    hidden = {str(x) for x in tombstone_ids if x}
    overlay_by_id = {}
    for row in overlays:
        oid = str(row.get("production_id") or "")
        if oid:
            overlay_by_id[oid] = row

    merged: Dict[str, dict] = {}
    for row in production:
        payload = dict(row)
        payload.pop("_id", None)
        ident = doc_id(payload)
        if not ident or ident in hidden:
            continue
        overlay = overlay_by_id.get(ident)
        if overlay and overlay.get("payload"):
            payload.update(dict(overlay["payload"]))
            payload["_overlay"] = True
        payload.setdefault("data_origin", "production")
        merged[ident] = payload

    for row in testing_created:
        payload = dict(row)
        payload.pop("_id", None)
        if not is_testing_created_doc(payload):
            continue
        ident = doc_id(payload)
        if not ident or ident in hidden:
            continue
        payload.setdefault("data_origin", DATA_ORIGIN_TESTING)
        merged[ident] = payload
    return list(merged.values())


def _doc_matches(doc: Mapping[str, Any], query: Mapping[str, Any] | None) -> bool:
    if not query:
        return True
    if "$and" in query:
        return all(_doc_matches(doc, part) for part in (query.get("$and") or []))
    if "$or" in query:
        parts = query.get("$or") or []
        return any(_doc_matches(doc, part) for part in parts) if parts else True
    if "$nor" in query:
        return not any(_doc_matches(doc, part) for part in (query.get("$nor") or []))
    for key, expected in query.items():
        if key in {QUERY_BASE_ONLY, QUERY_TESTING_ONLY}:
            continue
        if key.startswith("$"):
            continue
        actual = doc.get(key)
        if isinstance(expected, Mapping):
            if "$in" in expected:
                if actual not in expected["$in"] and str(actual) not in {str(x) for x in expected["$in"]}:
                    return False
            if "$nin" in expected and (actual in expected["$nin"] or str(actual) in {str(x) for x in expected["$nin"]}):
                return False
            if "$ne" in expected and actual == expected["$ne"]:
                return False
            if "$exists" in expected:
                exists = key in doc and doc.get(key) is not None
                if bool(expected["$exists"]) != exists:
                    return False
            if "$regex" in expected:
                flags = re.I if "i" in str(expected.get("$options") or "") else 0
                if not re.search(str(expected["$regex"]), str(actual or ""), flags):
                    return False
            if "$gt" in expected and not (actual is not None and actual > expected["$gt"]):
                return False
            if "$gte" in expected and not (actual is not None and actual >= expected["$gte"]):
                return False
            if "$lt" in expected and not (actual is not None and actual < expected["$lt"]):
                return False
            if "$lte" in expected and not (actual is not None and actual <= expected["$lte"]):
                return False
            continue
        if actual != expected and str(actual) != str(expected):
            return False
    return True


class _WriteResult:
    def __init__(self, **kwargs):
        self.acknowledged = True
        self.matched_count = int(kwargs.get("matched_count") or 0)
        self.modified_count = int(kwargs.get("modified_count") or 0)
        self.deleted_count = int(kwargs.get("deleted_count") or 0)
        self.inserted_id = kwargs.get("inserted_id")
        self.upserted_id = kwargs.get("upserted_id")
        self.inserted_ids = kwargs.get("inserted_ids") or []
        self.raw_result = kwargs


class OverlayCursor:
    def __init__(self, fetch, projection=None):
        self._fetch = fetch
        self._projection = projection
        self._sort = None
        self._skip = 0
        self._limit = None

    def sort(self, key_or_list, direction=None):
        if direction is None:
            self._sort = key_or_list
        else:
            self._sort = [(key_or_list, direction)]
        return self

    def skip(self, count):
        self._skip = int(count or 0)
        return self

    def limit(self, count):
        self._limit = int(count) if count is not None else None
        return self

    def allow_disk_use(self, _value=True):
        return self

    def _project(self, doc: dict) -> dict:
        if not self._projection:
            return doc
        include = {k for k, v in self._projection.items() if v}
        exclude = {k for k, v in self._projection.items() if not v}
        if include:
            keep = {k: doc.get(k) for k in include if k in doc or k == "_id"}
            if "_id" not in self._projection:
                if "id" in doc:
                    keep.setdefault("id", doc.get("id"))
            return keep
        out = dict(doc)
        for key in exclude:
            if key != "_id":
                out.pop(key, None)
        return out

    async def to_list(self, length):
        rows = await self._fetch()
        if self._sort:
            keys = self._sort if isinstance(self._sort, list) else [(self._sort, 1)]
            for field, direction in reversed(keys):
                reverse = int(direction or 1) < 0
                rows.sort(key=lambda r: (r.get(field) is None, r.get(field)), reverse=reverse)
        if self._skip:
            rows = rows[self._skip:]
        cap = length if length is not None else self._limit
        if self._limit is not None:
            rows = rows[: self._limit]
        if cap is not None:
            rows = rows[: int(cap)]
        return [self._project(r) for r in rows]


class ReadOnlyCollection:
    def __init__(self, inner, name: str):
        self._inner = inner
        self.name = name

    def _blocked(self, op: str):
        raise ProductionWriteBlocked(
            f"Refusing production write {op} on collection {self.name!r} from the testing overlay"
        )

    def find(self, *args, **kwargs):
        return self._inner.find(*args, **kwargs)

    def find_one(self, *args, **kwargs):
        return self._inner.find_one(*args, **kwargs)

    def aggregate(self, *args, **kwargs):
        return self._inner.aggregate(*args, **kwargs)

    def count_documents(self, *args, **kwargs):
        return self._inner.count_documents(*args, **kwargs)

    def estimated_document_count(self, *args, **kwargs):
        return self._inner.estimated_document_count(*args, **kwargs)

    def insert_one(self, *args, **kwargs):
        self._blocked("insert_one")

    def insert_many(self, *args, **kwargs):
        self._blocked("insert_many")

    def update_one(self, *args, **kwargs):
        self._blocked("update_one")

    def update_many(self, *args, **kwargs):
        self._blocked("update_many")

    def replace_one(self, *args, **kwargs):
        self._blocked("replace_one")

    def delete_one(self, *args, **kwargs):
        self._blocked("delete_one")

    def delete_many(self, *args, **kwargs):
        self._blocked("delete_many")

    def bulk_write(self, *args, **kwargs):
        self._blocked("bulk_write")

    def find_one_and_update(self, *args, **kwargs):
        self._blocked("find_one_and_update")

    def create_index(self, *args, **kwargs):
        return self._inner.create_index(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._inner, name)


class ReadOnlyDatabase:
    def __init__(self, inner):
        self._inner = inner

    def __getitem__(self, name: str):
        return ReadOnlyCollection(self._inner[name], name)

    def __getattr__(self, name: str):
        if name.startswith("_"):
            return super().__getattribute__(name)
        return self[name]


class OverlayCollection:
    def __init__(self, name: str, prod, test, overlays, tombstones):
        self.name = name
        self._prod = prod
        self._test = test
        self._overlays = overlays
        self._tombstones = tombstones

    def _local_only(self) -> bool:
        return self.name in LOCAL_ONLY_COLLECTIONS or self.name not in OVERLAY_COLLECTIONS

    async def _tombstone_ids(self) -> set[str]:
        rows = await self._tombstones.find({"collection": self.name}, {"_id": 0, "production_id": 1}).to_list(20000)
        return {str(r.get("production_id")) for r in rows if r.get("production_id")}

    async def _overlay_rows(self) -> List[dict]:
        return await self._overlays.find({"collection": self.name}, {"_id": 0}).to_list(20000)

    def _testing_created_query(self, query: dict) -> dict:
        created = {
            "$or": [
                {"data_origin": DATA_ORIGIN_TESTING},
                {"order_number": {"$regex": f"^{re.escape(TS_PREFIX)}"}},
                {"request_number": {"$regex": f"^{re.escape(TS_PREFIX)}"}},
                {"upload_no": {"$regex": f"^{re.escape(TS_PREFIX)}"}},
                {"user_id": {"$regex": f"^{re.escape(TS_PREFIX)}"}},
            ]
        }
        if not query:
            return created
        return {"$and": [query, created]}

    async def _merged(self, query=None, projection=None) -> List[dict]:
        raw, base_only, testing_only = _strip_overlay_flags(query)
        if self._local_only():
            cursor = self._test.find(raw, projection or {"_id": 0})
            return await cursor.to_list(200000)
        hidden = await self._tombstone_ids()
        overlays = [] if (base_only or testing_only) else await self._overlay_rows()
        prod_docs: List[dict] = []
        if not testing_only:
            try:
                prod_docs = await self._prod.find(raw, {"_id": 0}).to_list(200000)
            except Exception as exc:
                logger.warning("Production read failed for %s: %s", self.name, exc)
                prod_docs = []
        created: List[dict] = []
        if not base_only:
            try:
                created = await self._test.find(self._testing_created_query(raw), {"_id": 0}).to_list(200000)
            except Exception as exc:
                logger.warning("Testing-created read failed for %s: %s", self.name, exc)
                created = []
        if testing_only:
            prod_docs = []
            overlays = []
        if base_only:
            created = []
            overlays = []
        merged = merge_documents(prod_docs, created, overlays, hidden)
        return [row for row in merged if _doc_matches(row, raw)]

    def find(self, query=None, projection=None, *args, **kwargs):
        async def fetch():
            return await self._merged(query, projection)
        return OverlayCursor(fetch, projection)

    async def find_one(self, query=None, projection=None, *args, **kwargs):
        rows = await self.find(query, projection).to_list(1)
        return rows[0] if rows else None

    async def count_documents(self, query=None, *args, **kwargs):
        rows = await self._merged(query)
        return len(rows)

    async def estimated_document_count(self, *args, **kwargs):
        return await self.count_documents({})

    async def insert_one(self, doc, *args, **kwargs):
        if self._local_only():
            return await self._test.insert_one(doc)
        payload = testing_runtime.stamp_testing_origin(dict(doc))
        payload.setdefault("data_origin", DATA_ORIGIN_TESTING)
        return await self._test.insert_one(payload)

    async def insert_many(self, docs, *args, **kwargs):
        if self._local_only():
            return await self._test.insert_many(docs, *args, **kwargs)
        payload = [testing_runtime.stamp_testing_origin(dict(d)) for d in docs]
        for row in payload:
            row.setdefault("data_origin", DATA_ORIGIN_TESTING)
        return await self._test.insert_many(payload)

    async def _is_testing_owned(self, ident: str, existing: Optional[dict]) -> bool:
        if existing and is_testing_created_doc(existing):
            return True
        if ident:
            found = await self._test.find_one(
                self._testing_created_query({"id": ident}),
                {"_id": 0, "id": 1, "data_origin": 1},
            )
            return bool(found)
        return False

    async def update_one(self, query, update, *args, **kwargs):
        if self._local_only():
            return await self._test.update_one(query, update, *args, **kwargs)
        existing = await self.find_one(query)
        if not existing:
            return _WriteResult(matched_count=0, modified_count=0)
        ident = doc_id(existing)
        if await self._is_testing_owned(ident, existing):
            return await self._test.update_one({"id": ident} if ident else query, update)
        overlay_payload = apply_mongo_update(
            (await self._overlays.find_one({"collection": self.name, "production_id": ident}) or {}).get("payload") or existing,
            update,
        )
        overlay_payload["id"] = ident
        await self._overlays.update_one(
            {"collection": self.name, "production_id": ident},
            {
                "$set": {
                    "collection": self.name,
                    "production_id": ident,
                    "payload": overlay_payload,
                    "updated_at": _now_iso(),
                    "op": "upsert",
                },
                "$setOnInsert": {"created_at": _now_iso()},
            },
            upsert=True,
        )
        return _WriteResult(matched_count=1, modified_count=1)

    async def update_many(self, query, update, *args, **kwargs):
        if self._local_only():
            return await self._test.update_many(query, update, *args, **kwargs)
        rows = await self._merged(query)
        modified = 0
        for row in rows:
            result = await self.update_one({"id": doc_id(row)}, update)
            modified += int(getattr(result, "modified_count", 0) or 0)
        return _WriteResult(matched_count=len(rows), modified_count=modified)

    async def replace_one(self, query, replacement, *args, **kwargs):
        return await self.update_one(query, {"$set": dict(replacement)})

    async def find_one_and_update(self, query, update, *args, **kwargs):
        await self.update_one(query, update)
        return await self.find_one(query)

    async def delete_one(self, query, *args, **kwargs):
        if self._local_only():
            return await self._test.delete_one(query)
        existing = await self.find_one(query)
        if not existing:
            return _WriteResult(deleted_count=0)
        ident = doc_id(existing)
        if await self._is_testing_owned(ident, existing):
            result = await self._test.delete_one({"id": ident} if ident else query)
            await self._overlays.delete_many({"collection": self.name, "production_id": ident})
            return result
        await self._tombstones.update_one(
            {"collection": self.name, "production_id": ident},
            {
                "$set": {
                    "collection": self.name,
                    "production_id": ident,
                    "op": "tombstone",
                    "updated_at": _now_iso(),
                },
                "$setOnInsert": {"created_at": _now_iso()},
            },
            upsert=True,
        )
        await self._overlays.delete_many({"collection": self.name, "production_id": ident})
        return _WriteResult(deleted_count=1)

    async def delete_many(self, query, *args, **kwargs):
        if self._local_only():
            return await self._test.delete_many(query)
        rows = await self._merged(query)
        deleted = 0
        for row in rows:
            result = await self.delete_one({"id": doc_id(row)})
            deleted += int(getattr(result, "deleted_count", 0) or 0)
        return _WriteResult(deleted_count=deleted)

    async def bulk_write(self, requests, *args, **kwargs):
        if self._local_only():
            return await self._test.bulk_write(requests, *args, **kwargs)
        raise ProductionWriteBlocked(
            f"bulk_write is not supported on overlay collection {self.name!r}"
        )

    async def aggregate(self, pipeline, *args, **kwargs):
        stages = list(pipeline or [])
        match = {}
        rest = stages
        if stages and isinstance(stages[0], Mapping) and "$match" in stages[0]:
            match = dict(stages[0]["$match"] or {})
            rest = stages[1:]
        docs = await self._merged(match)
        try:
            out = _memory_aggregate(docs, rest)
        except Exception as exc:
            logger.warning("In-memory overlay aggregate fallback to production-only for %s: %s", self.name, exc)
            if self._local_only():
                inner = self._test.aggregate(pipeline, *args, **kwargs)
                return OverlayCursor(lambda: inner.to_list(100000) if hasattr(inner, "to_list") else _as_list(inner))
            inner = self._prod.aggregate(pipeline, *args, **kwargs)

            async def fetch_prod():
                return await inner.to_list(100000)

            return OverlayCursor(fetch_prod)

        async def fetch():
            return out

        return OverlayCursor(fetch)

    async def create_index(self, *args, **kwargs):
        return await self._test.create_index(*args, **kwargs)

    async def create_indexes(self, *args, **kwargs):
        return await self._test.create_indexes(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._test, name)


async def _as_list(value):
    if hasattr(value, "to_list"):
        return await value.to_list(100000)
    return list(value)


def _eval_group_expr(expr: Any, doc: Mapping[str, Any], acc=None):
    if expr == 1:
        return 1
    if isinstance(expr, (int, float)):
        return expr
    if isinstance(expr, str) and expr.startswith("$"):
        return doc.get(expr[1:])
    if not isinstance(expr, Mapping):
        return expr
    if "$ifNull" in expr:
        left, right = expr["$ifNull"]
        val = _eval_group_expr(left, doc)
        return right if val is None else val
    if "$toDouble" in expr:
        try:
            return float(_eval_group_expr(expr["$toDouble"], doc) or 0)
        except (TypeError, ValueError):
            return 0.0
    if "$cond" in expr:
        spec = expr["$cond"]
        if isinstance(spec, list) and len(spec) == 3:
            pred, yes, no = spec
        else:
            pred, yes, no = spec.get("if"), spec.get("then"), spec.get("else")
        return _eval_group_expr(yes if _truthy(pred, doc) else no, doc)
    if "$gt" in expr:
        left, right = expr["$gt"]
        try:
            return _eval_group_expr(left, doc) > _eval_group_expr(right, doc)
        except TypeError:
            return False
    if "$sum" in expr:
        val = _eval_group_expr(expr["$sum"], doc)
        return 0 if val is None else val
    if "$max" in expr:
        return _eval_group_expr(expr["$max"], doc)
    if "$min" in expr:
        return _eval_group_expr(expr["$min"], doc)
    return None


def _truthy(pred, doc) -> bool:
    val = _eval_group_expr(pred, doc)
    return bool(val)


def _memory_aggregate(docs: List[dict], pipeline: Sequence[Mapping[str, Any]]) -> List[dict]:
    rows = list(docs)
    for stage in pipeline:
        if not isinstance(stage, Mapping) or len(stage) != 1:
            raise ValueError("unsupported aggregate stage")
        op, spec = next(iter(stage.items()))
        if op == "$match":
            rows = [r for r in rows if _doc_matches(r, spec)]
        elif op == "$limit":
            rows = rows[: int(spec)]
        elif op == "$skip":
            rows = rows[int(spec):]
        elif op == "$sort":
            for field, direction in reversed(list(spec.items())):
                rows.sort(key=lambda r: (r.get(field) is None, r.get(field)), reverse=int(direction) < 0)
        elif op == "$project":
            projected = []
            for row in rows:
                item = {}
                for key, val in spec.items():
                    if val in (1, True):
                        if key in row:
                            item[key] = row[key]
                    elif val in (0, False):
                        continue
                    else:
                        item[key] = _eval_group_expr(val, row)
                projected.append(item)
            rows = projected
        elif op == "$count":
            rows = [{str(spec): len(rows)}]
        elif op == "$group":
            grouped: Dict[Any, dict] = {}
            acc_specs = {k: v for k, v in spec.items() if k != "_id"}
            for row in rows:
                ident = _eval_group_expr(spec.get("_id"), row) if not isinstance(spec.get("_id"), Mapping) else {
                    k: _eval_group_expr(v, row) for k, v in spec["_id"].items()
                } if isinstance(spec.get("_id"), Mapping) else spec.get("_id")
                key = ident if not isinstance(ident, dict) else tuple(sorted(ident.items()))
                slot = grouped.setdefault(key, {"_id": ident})
                for field, expr in acc_specs.items():
                    if isinstance(expr, Mapping) and "$sum" in expr:
                        add = _eval_group_expr(expr["$sum"], row)
                        try:
                            slot[field] = (slot.get(field) or 0) + (0 if add is None else add)
                        except TypeError:
                            slot[field] = add
                    elif isinstance(expr, Mapping) and "$max" in expr:
                        val = _eval_group_expr(expr["$max"], row)
                        cur = slot.get(field)
                        slot[field] = val if cur is None or (val is not None and val > cur) else cur
                    elif isinstance(expr, Mapping) and "$min" in expr:
                        val = _eval_group_expr(expr["$min"], row)
                        cur = slot.get(field)
                        slot[field] = val if cur is None or (val is not None and val < cur) else cur
                    else:
                        slot[field] = _eval_group_expr(expr, row)
            rows = list(grouped.values())
        else:
            raise ValueError(f"unsupported aggregate stage {op}")
    return rows


class OverlayDatabase:
    def __init__(self, prod_db, test_db):
        self._prod = prod_db
        self._test = test_db
        self._cache: Dict[str, OverlayCollection] = {}

    def __getitem__(self, name: str):
        if name not in self._cache:
            self._cache[name] = OverlayCollection(
                name,
                self._prod[name],
                self._test[name],
                self._test[OVERLAY_COLLECTION],
                self._test[TOMBSTONE_COLLECTION],
            )
        return self._cache[name]

    def __getattr__(self, name: str):
        if name.startswith("_"):
            return super().__getattribute__(name)
        return self[name]


def wrap_testing_database(client, test_db, mongo_url: str = "", mongo_kwargs: Optional[dict] = None):
    """Attach a read-only production database handle.

    Uses the same DocumentDB cluster/client. Writes to production collections
    are blocked by ReadOnlyDatabase even if the OS credentials could write.
    Optional MONGO_READONLY_URL may point at a dedicated read-only user later.
    """
    from motor.motor_asyncio import AsyncIOMotorClient

    prod_name = testing_runtime.PRODUCTION_DB_NAME
    readonly_url = (os.getenv("MONGO_READONLY_URL") or "").strip()
    if readonly_url:
        prod_client = AsyncIOMotorClient(readonly_url, **(mongo_kwargs or {}))
        prod_inner = prod_client[prod_name]
    else:
        prod_inner = client[prod_name]
    return OverlayDatabase(ReadOnlyDatabase(prod_inner), test_db)


async def ensure_overlay_indexes(test_db) -> None:
    await test_db[OVERLAY_COLLECTION].create_index(
        [("collection", 1), ("production_id", 1)], unique=True, name="overlay_identity"
    )
    await test_db[TOMBSTONE_COLLECTION].create_index(
        [("collection", 1), ("production_id", 1)], unique=True, name="tombstone_identity"
    )
    await test_db[OPERATION_COLLECTION].create_index("operation_id", unique=True, name="operation_id_unique")


async def reset_overlay(test_db, ident: str, collection: str) -> dict:
    """Drop a testing overlay so the current production value is visible again."""
    result = await test_db[OVERLAY_COLLECTION].delete_one(
        {"collection": collection, "production_id": ident}
    )
    tomb = await test_db[TOMBSTONE_COLLECTION].delete_one(
        {"collection": collection, "production_id": ident}
    )
    return {
        "production_id": ident,
        "collection": collection,
        "overlay_deleted": int(getattr(result, "deleted_count", 0) or 0),
        "tombstone_deleted": int(getattr(tomb, "deleted_count", 0) or 0),
    }
