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


def _as_datetime(value: Any) -> Optional[datetime]:
    """Parse ISO date/datetime strings so overlay queries can match Mongo $or clauses."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed
    return None


def _normalize_compare_pair(actual: Any, expected: Any) -> tuple[Any, Any]:
    left_dt = _as_datetime(actual)
    right_dt = _as_datetime(expected)
    if left_dt is not None and right_dt is not None:
        if left_dt.tzinfo is None and right_dt.tzinfo is not None:
            left_dt = left_dt.replace(tzinfo=timezone.utc)
        if right_dt.tzinfo is None and left_dt.tzinfo is not None:
            right_dt = right_dt.replace(tzinfo=timezone.utc)
        return left_dt, right_dt
    return actual, expected


def _ordered_compare(actual: Any, expected: Any, op: str) -> bool:
    """Compare $gt/$gte/$lt/$lte without raising on mixed str/datetime types."""
    if actual is None:
        return False
    left, right = _normalize_compare_pair(actual, expected)
    try:
        if op == "gt":
            return left > right
        if op == "gte":
            return left >= right
        if op == "lt":
            return left < right
        if op == "lte":
            return left <= right
    except TypeError:
        return False
    return False


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
            if "$gt" in expected and not _ordered_compare(actual, expected["$gt"], "gt"):
                return False
            if "$gte" in expected and not _ordered_compare(actual, expected["$gte"], "gte"):
                return False
            if "$lt" in expected and not _ordered_compare(actual, expected["$lt"], "lt"):
                return False
            if "$lte" in expected and not _ordered_compare(actual, expected["$lte"], "lte"):
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


async def _aiter(cursor):
    if cursor is None:
        return
        yield  # pragma: no cover
    if hasattr(cursor, "__aiter__"):
        async for item in cursor:
            yield item
        return
    if hasattr(cursor, "to_list"):
        rows = await cursor.to_list(None)
        for item in rows:
            yield item
        return
    for item in cursor:
        yield item


class OverlayCursor:
    def __init__(self, stream=None, inner=None, prepare=None, projection=None):
        self._stream = stream
        self._inner = inner
        self._prepare = prepare
        self._projection = projection
        self._sort = None
        self._skip = 0
        self._limit = None

    def sort(self, key_or_list, direction=None):
        if direction is None:
            self._sort = key_or_list
        else:
            self._sort = [(key_or_list, direction)]
        if self._inner is not None and hasattr(self._inner, "sort"):
            if direction is None:
                self._inner.sort(key_or_list)
            else:
                self._inner.sort(key_or_list, direction)
        return self

    def skip(self, count):
        self._skip = int(count or 0)
        if self._inner is not None and hasattr(self._inner, "skip"):
            self._inner.skip(count)
        return self

    def limit(self, count):
        self._limit = int(count) if count is not None else None
        if self._inner is not None and hasattr(self._inner, "limit") and count is not None:
            self._inner.limit(count)
        return self

    def allow_disk_use(self, _value=True):
        if self._inner is not None and hasattr(self._inner, "allow_disk_use"):
            self._inner.allow_disk_use(_value)
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

    async def _ensure(self):
        if self._prepare is None:
            return
        prepare = self._prepare
        self._prepare = None
        kind, obj = await prepare()
        if kind == "inner":
            self._inner = obj
            if self._sort is not None and hasattr(self._inner, "sort"):
                spec = self._sort
                if isinstance(spec, list) and len(spec) == 1:
                    self._inner.sort(spec[0][0], spec[0][1])
                else:
                    self._inner.sort(spec)
            if self._skip and hasattr(self._inner, "skip"):
                self._inner.skip(self._skip)
            if self._limit is not None and hasattr(self._inner, "limit"):
                self._inner.limit(self._limit)
        else:
            self._stream = obj

    async def to_list(self, length):
        await self._ensure()
        cap = length if length is not None else self._limit
        if self._inner is not None:
            inner = self._inner
            if cap is None:
                rows = await inner.to_list(None)
            else:
                rows = await inner.to_list(int(cap))
            return [self._project(r) for r in rows]
        rows = []
        skipped = 0
        async for doc in self._stream():
            if skipped < self._skip:
                skipped += 1
                continue
            rows.append(self._project(doc))
            if cap is not None and len(rows) >= int(cap):
                break
        return rows

    async def __aiter__(self):
        await self._ensure()
        if self._inner is not None:
            async for doc in _aiter(self._inner):
                yield self._project(doc)
            return
        skipped = 0
        taken = 0
        stream = self._stream
        if stream is None:
            return
        async for doc in stream():
            if skipped < self._skip:
                skipped += 1
                continue
            yield self._project(doc)
            taken += 1
            if self._limit is not None and taken >= int(self._limit):
                return


_MISSING = object()


class _Peek:
    def __init__(self, source, kind: str = ""):
        self.kind = kind
        self._source = source
        self._aiter = None
        self._cur = _MISSING

    async def _ensure(self):
        if self._aiter is None:
            self._aiter = _aiter(self._source).__aiter__()

    async def peek(self):
        await self._ensure()
        if self._cur is _MISSING:
            try:
                self._cur = await self._aiter.__anext__()
            except StopAsyncIteration:
                self._cur = None
        return self._cur

    async def pop(self):
        value = await self.peek()
        self._cur = _MISSING
        return value


def _sort_pairs(spec) -> List[tuple[str, int]]:
    if spec is None:
        return []
    if isinstance(spec, Mapping):
        return [(str(key), int(val)) for key, val in spec.items()]
    if isinstance(spec, (list, tuple)):
        if spec and isinstance(spec[0], (list, tuple)):
            return [(str(key), int(direction)) for key, direction in spec]
        if len(spec) == 2 and isinstance(spec[0], str):
            return [(spec[0], int(spec[1]))]
    if isinstance(spec, str):
        return [(spec, 1)]
    return []


def _apply_sort(cursor, spec):
    pairs = _sort_pairs(spec)
    if not pairs or cursor is None or not hasattr(cursor, "sort"):
        return cursor
    if len(pairs) == 1:
        cursor.sort(pairs[0][0], pairs[0][1])
    else:
        cursor.sort(pairs)
    return cursor


def _sort_key_tuple(doc: Mapping[str, Any], pairs: Sequence[tuple[str, int]]):
    keys = []
    for field, direction in pairs:
        val = doc.get(field)
        missing = val is None
        keyed = val if not missing else ""
        if int(direction) < 0:
            keys.append((not missing, keyed))
        else:
            keys.append((missing, keyed))
    return tuple(keys)


def _doc_sort_before(left: Mapping[str, Any], right: Mapping[str, Any], pairs) -> bool:
    lkey = _sort_key_tuple(left, pairs)
    rkey = _sort_key_tuple(right, pairs)
    try:
        return lkey < rkey
    except TypeError:
        return str(lkey) < str(rkey)


def _extract_sort_limit(stages: Sequence[Mapping[str, Any]]):
    core: List[Mapping[str, Any]] = []
    sort_spec = None
    skip_n = 0
    limit_n = None
    for stage in stages or []:
        if not isinstance(stage, Mapping) or len(stage) != 1:
            core.append(stage)
            continue
        op, spec = next(iter(stage.items()))
        if op == "$sort":
            sort_spec = spec
        elif op == "$skip":
            skip_n = spec
        elif op == "$limit":
            limit_n = spec
        else:
            core.append(stage)
    return core, sort_spec, skip_n, limit_n


def _merge_group_results(rows: Sequence[Mapping[str, Any]], group_spec: Mapping[str, Any]) -> List[dict]:
    acc_specs = {key: val for key, val in group_spec.items() if key != "_id"}
    grouped: Dict[Any, dict] = {}
    for row in rows:
        ident = row.get("_id")
        slot_key = ident if not isinstance(ident, dict) else tuple(sorted(ident.items()))
        slot = grouped.get(slot_key)
        if slot is None:
            grouped[slot_key] = dict(row)
            continue
        for field, expr in acc_specs.items():
            if isinstance(expr, Mapping) and "$sum" in expr:
                try:
                    slot[field] = (slot.get(field) or 0) + (row.get(field) or 0)
                except TypeError:
                    slot[field] = row.get(field, slot.get(field))
            elif isinstance(expr, Mapping) and "$max" in expr:
                cur, nxt = slot.get(field), row.get(field)
                slot[field] = nxt if cur is None else (cur if nxt is None else max(cur, nxt))
            elif isinstance(expr, Mapping) and "$min" in expr:
                cur, nxt = slot.get(field), row.get(field)
                slot[field] = nxt if cur is None else (cur if nxt is None else min(cur, nxt))
            else:
                if field not in slot or slot.get(field) is None:
                    slot[field] = row.get(field)
    return list(grouped.values())


_WRITE_METHOD_PREFIXES = (
    "insert", "update", "replace", "delete", "remove", "bulk", "drop", "rename",
    "create_index", "create_indexes", "drop_index", "find_one_and", "save",
)
_WRITE_METHODS = frozenset({
    "insert_one", "insert_many", "insert",
    "update_one", "update_many", "update",
    "replace_one", "replace",
    "delete_one", "delete_many", "remove",
    "bulk_write", "bulk_write_with_session",
    "find_one_and_update", "find_one_and_replace", "find_one_and_delete",
    "find_one_and_modify",
    "drop", "drop_index", "drop_indexes", "create_index", "create_indexes",
    "rename", "save",
})


class ReadOnlyCollection:
    def __init__(self, inner, name: str):
        self._inner = inner
        self.name = name

    def _blocked(self, op: str):
        raise ProductionWriteBlocked(
            f"Refusing production write {op} on collection {self.name!r} from the testing overlay"
        )

    def _is_write(self, name: str) -> bool:
        if name in _WRITE_METHODS:
            return True
        return name.startswith(_WRITE_METHOD_PREFIXES)

    def find(self, *args, **kwargs):
        return self._inner.find(*args, **kwargs)

    def find_one(self, *args, **kwargs):
        return self._inner.find_one(*args, **kwargs)

    def aggregate(self, *args, **kwargs):
        pipeline = args[0] if args else kwargs.get("pipeline") or []
        for stage in pipeline:
            if isinstance(stage, Mapping) and any(k in stage for k in ("$out", "$merge", "$changeStream")):
                self._blocked("aggregate_write")
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

    def find_one_and_replace(self, *args, **kwargs):
        self._blocked("find_one_and_replace")

    def find_one_and_delete(self, *args, **kwargs):
        self._blocked("find_one_and_delete")

    def find_one_and_modify(self, *args, **kwargs):
        self._blocked("find_one_and_modify")

    def create_index(self, *args, **kwargs):
        self._blocked("create_index")

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        if self._is_write(name):
            self._blocked(name)
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
        hidden = set()
        cursor = self._tombstones.find({"collection": self.name}, {"_id": 0, "production_id": 1})
        async for row in _aiter(cursor):
            ident = row.get("production_id")
            if ident:
                hidden.add(str(ident))
        return hidden

    async def _overlay_map(self) -> Dict[str, dict]:
        overlay_by_id: Dict[str, dict] = {}
        cursor = self._overlays.find({"collection": self.name}, {"_id": 0})
        async for row in _aiter(cursor):
            oid = str(row.get("production_id") or "")
            if oid:
                overlay_by_id[oid] = row
        return overlay_by_id

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

    def _apply_overlay_row(self, payload: dict, overlay: Optional[Mapping[str, Any]]) -> dict:
        out = dict(payload)
        out.pop("_id", None)
        if overlay and overlay.get("payload"):
            out.update(dict(overlay["payload"]))
            out["_overlay"] = True
        out.setdefault("data_origin", "production")
        return out

    async def _stream_merged(self, query=None, sort=None):
        raw, base_only, testing_only = _strip_overlay_flags(query)
        if self._local_only():
            cursor = self._test.find(raw, {"_id": 0})
            cursor = _apply_sort(cursor, sort)
            async for row in _aiter(cursor):
                yield dict(row)
            return
        hidden = set() if testing_only else await self._tombstone_ids()
        overlays = {} if (base_only or testing_only) else await self._overlay_map()
        pairs = _sort_pairs(sort)
        if pairs and not testing_only:
            async for row in self._sorted_merge(raw, hidden, overlays, base_only, pairs):
                yield row
            return
        created_by_id: Dict[str, dict] = {}
        if not base_only:
            try:
                created_cursor = self._test.find(self._testing_created_query(raw), {"_id": 0})
                async for row in _aiter(created_cursor):
                    payload = dict(row)
                    payload.pop("_id", None)
                    if not is_testing_created_doc(payload):
                        continue
                    ident = doc_id(payload)
                    if ident and ident not in hidden:
                        payload.setdefault("data_origin", DATA_ORIGIN_TESTING)
                        created_by_id[ident] = payload
            except Exception as exc:
                logger.warning("Testing-created read failed for %s: %s", self.name, exc)
        seen = set()
        if not testing_only:
            try:
                prod_cursor = self._prod.find(raw, {"_id": 0})
                async for row in _aiter(prod_cursor):
                    payload = dict(row)
                    ident = doc_id(payload)
                    if not ident or ident in hidden:
                        continue
                    if ident in created_by_id:
                        merged = created_by_id[ident]
                    else:
                        merged = self._apply_overlay_row(payload, overlays.get(ident))
                    if not _doc_matches(merged, raw):
                        continue
                    seen.add(ident)
                    yield merged
            except Exception as exc:
                logger.warning("Production read failed for %s: %s", self.name, exc)
        if not base_only:
            for ident, payload in created_by_id.items():
                if ident in seen or ident in hidden:
                    continue
                if _doc_matches(payload, raw):
                    yield payload

    async def _sorted_merge(self, raw, hidden, overlays, base_only, pairs):
        """Merge production + overlay-applied + testing-created without loading all rows."""
        overlay_ids = set(overlays)
        exclude = set(hidden) | overlay_ids
        prod_query = raw
        if exclude:
            nin = {"id": {"$nin": list(exclude)}}
            prod_query = {"$and": [raw, nin]} if raw else nin
        overlay_docs = []
        for ident, overlay in overlays.items():
            if ident in hidden:
                continue
            try:
                orig = await self._prod.find_one({"id": ident}, {"_id": 0})
            except Exception:
                orig = None
            if not orig:
                continue
            merged = self._apply_overlay_row(orig, overlay)
            if _doc_matches(merged, raw):
                overlay_docs.append(merged)
        overlay_docs.sort(key=lambda d: _sort_key_tuple(d, pairs))
        streams = []
        try:
            prod_cursor = _apply_sort(self._prod.find(prod_query, {"_id": 0}), pairs)
            streams.append(_Peek(prod_cursor, "prod"))
        except Exception as exc:
            logger.warning("Production sorted read failed for %s: %s", self.name, exc)
        if overlay_docs:
            streams.append(_Peek(overlay_docs, "overlay"))
        if not base_only:
            try:
                created_cursor = _apply_sort(
                    self._test.find(self._testing_created_query(raw), {"_id": 0}),
                    pairs,
                )
                streams.append(_Peek(created_cursor, "created"))
            except Exception as exc:
                logger.warning("Testing-created sorted read failed for %s: %s", self.name, exc)
        seen = set()
        while True:
            best_idx = -1
            best_doc = None
            for i, peek in enumerate(streams):
                nxt = await peek.peek()
                if nxt is None:
                    continue
                payload = dict(nxt)
                payload.pop("_id", None)
                if peek.kind == "created" and not is_testing_created_doc(payload):
                    await peek.pop()
                    continue
                if best_doc is None or _doc_sort_before(payload, best_doc, pairs):
                    best_idx = i
                    best_doc = payload
            if best_idx < 0 or best_doc is None:
                return
            kind = streams[best_idx].kind
            await streams[best_idx].pop()
            ident = doc_id(best_doc)
            if ident and ident in seen:
                continue
            if ident and ident in hidden:
                continue
            if ident in overlay_ids and kind == "prod":
                continue
            if not _doc_matches(best_doc, raw):
                continue
            if ident:
                seen.add(ident)
            if is_testing_created_doc(best_doc):
                best_doc.setdefault("data_origin", DATA_ORIGIN_TESTING)
            yield best_doc

    def find(self, query=None, projection=None, *args, **kwargs):
        raw, base_only, testing_only = _strip_overlay_flags(query)
        if self._local_only():
            return OverlayCursor(inner=self._test.find(raw, projection or {"_id": 0}), projection=projection)

        cursor = OverlayCursor(prepare=None, projection=projection)

        async def prepare():
            if not testing_only and not base_only and not await self._has_any_deltas():
                return "inner", self._prod.find(raw, projection or {"_id": 0})

            async def stream():
                async for row in self._stream_merged(query, sort=cursor._sort):
                    yield row

            return "stream", stream

        cursor._prepare = prepare
        return cursor

    async def find_one(self, query=None, projection=None, *args, **kwargs):
        rows = await self.find(query, projection).to_list(1)
        return rows[0] if rows else None

    async def count_documents(self, query=None, *args, **kwargs):
        raw, base_only, testing_only = _strip_overlay_flags(query)
        if self._local_only():
            return int(await self._test.count_documents(raw))
        if not testing_only and not base_only and not await self._has_any_deltas():
            return int(await self._prod.count_documents(raw))
        hidden = set() if testing_only else await self._tombstone_ids()
        overlays = {} if (base_only or testing_only) else await self._overlay_map()
        created_n = 0
        created_ids: List[str] = []
        if not base_only:
            created_q = self._testing_created_query(raw)
            created_n = int(await self._test.count_documents(created_q))
            if created_n:
                async for row in _aiter(self._test.find(created_q, {"_id": 0, "id": 1, "user_id": 1, "order_id": 1, "request_id": 1, "upload_id": 1, "order_number": 1, "request_number": 1, "upload_no": 1, "data_origin": 1})):
                    ident = doc_id(row)
                    if ident and ident not in hidden:
                        created_ids.append(ident)
        if testing_only:
            return created_n
        prod_query: dict = raw
        if hidden:
            prod_query = {"$and": [raw, {"id": {"$nin": list(hidden)}}]} if raw else {"id": {"$nin": list(hidden)}}
        try:
            prod_n = int(await self._prod.count_documents(prod_query))
        except Exception as exc:
            logger.warning("Production count failed for %s: %s", self.name, exc)
            prod_n = 0
        overlap = 0
        if created_ids:
            try:
                overlap = int(await self._prod.count_documents(
                    {"$and": [prod_query, {"id": {"$in": created_ids}}]} if prod_query else {"id": {"$in": created_ids}}
                ))
            except Exception:
                overlap = 0
        overlay_delta = 0
        for ident, overlay in overlays.items():
            if ident in hidden:
                continue
            try:
                orig = await self._prod.find_one({"id": ident}, {"_id": 0})
            except Exception:
                orig = None
            if not orig:
                continue
            orig_match = _doc_matches(orig, raw)
            new_match = _doc_matches(self._apply_overlay_row(orig, overlay), raw)
            if orig_match and not new_match:
                overlay_delta -= 1
            elif not orig_match and new_match:
                overlay_delta += 1
        return prod_n - overlap + len(created_ids) + overlay_delta

    async def estimated_document_count(self, *args, **kwargs):
        return await self.count_documents({})

    async def _collect_merged(self, query=None) -> List[dict]:
        rows: List[dict] = []
        async for row in self._stream_merged(query):
            rows.append(row)
        return rows

    async def _has_any_deltas(self) -> bool:
        try:
            if int(await self._tombstones.count_documents({"collection": self.name})) > 0:
                return True
            if int(await self._overlays.count_documents({"collection": self.name})) > 0:
                return True
            return int(await self._test.count_documents(self._testing_created_query({}))) > 0
        except Exception:
            return False

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
        rows = await self._collect_merged(query)
        modified = 0
        for row in rows:
            result = await self.update_one({"id": doc_id(row)}, update)
            modified += int(getattr(result, "modified_count", 0) or 0)
        return _WriteResult(matched_count=len(rows), modified_count=modified)

    async def replace_one(self, query, replacement, *args, **kwargs):
        return await self.update_one(query, {"$set": dict(replacement)})

    async def find_one_and_update(self, query, update, *args, **kwargs):
        """Atomic find-and-modify. Local-only collections (counters) go to Testing."""
        if self._local_only():
            return await self._test.find_one_and_update(query, update, *args, **kwargs)
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
        rows = await self._collect_merged(query)
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

    def aggregate(self, pipeline, *args, **kwargs):
        if self._local_only():
            return OverlayCursor(inner=self._test.aggregate(pipeline, *args, **kwargs))
        cursor = OverlayCursor(prepare=None)

        async def prepare():
            if not await self._has_any_deltas():
                return "inner", self._prod.aggregate(pipeline, *args, **kwargs)
            stages = list(pipeline or [])
            match = {}
            rest = stages
            if stages and isinstance(stages[0], Mapping) and "$match" in stages[0]:
                match = dict(stages[0]["$match"] or {})
                rest = stages[1:]
            try:
                out = await self._split_aggregate(match, rest, *args, **kwargs)
            except Exception as exc:
                logger.warning("Split overlay aggregate failed for %s: %s", self.name, exc)
                return "inner", self._prod.aggregate(pipeline, *args, **kwargs)

            async def stream():
                for row in out:
                    yield row

            return "stream", stream

        cursor._prepare = prepare
        return cursor

    async def _split_aggregate(self, match, rest, *args, **kwargs) -> List[dict]:
        """Keep large Production aggregates on the server; merge small Testing deltas."""
        hidden = await self._tombstone_ids()
        overlays = await self._overlay_map()
        exclude = set(hidden) | set(overlays)
        group_stage = rest[0] if rest and isinstance(rest[0], Mapping) and "$group" in rest[0] else None
        tail = rest[1:] if group_stage is not None else rest
        core, sort_spec, skip_n, limit_n = _extract_sort_limit(tail if group_stage is None else tail)

        prod_match = dict(match)
        if exclude:
            nin = {"id": {"$nin": list(exclude)}}
            prod_match = {"$and": [match, nin]} if match else nin
        created_match = self._testing_created_query(match)

        async def run_coll(coll, query, extra):
            pipe = ([{"$match": query}] if query else []) + list(extra)
            if not pipe:
                pipe = [{"$match": {}}]
            return await _as_list(coll.aggregate(pipe, *args, **kwargs))

        extra = [group_stage] if group_stage is not None else list(core)
        prod_rows = []
        created_rows = []
        try:
            prod_rows = await run_coll(self._prod, prod_match, extra)
        except Exception as exc:
            logger.warning("Production aggregate failed for %s: %s", self.name, exc)
        try:
            created_rows = await run_coll(self._test, created_match, extra)
        except Exception as exc:
            logger.warning("Testing-created aggregate failed for %s: %s", self.name, exc)

        overlay_docs = []
        for ident, overlay in overlays.items():
            if ident in hidden:
                continue
            try:
                orig = await self._prod.find_one({"id": ident}, {"_id": 0})
            except Exception:
                orig = None
            if not orig:
                continue
            merged = self._apply_overlay_row(orig, overlay)
            if _doc_matches(merged, match):
                overlay_docs.append(merged)
        overlay_extra = [group_stage] if group_stage is not None else list(core)
        overlay_rows = _memory_aggregate(overlay_docs, overlay_extra) if overlay_docs else []

        if group_stage is not None:
            merged = _merge_group_results(list(prod_rows) + list(overlay_rows) + list(created_rows), group_stage["$group"])
            out = _memory_aggregate(merged, core) if core else merged
        else:
            out = list(prod_rows) + list(overlay_rows) + list(created_rows)
        if sort_spec:
            out = _memory_aggregate(out, [{"$sort": sort_spec}])
        if skip_n:
            out = out[int(skip_n):]
        if limit_n is not None:
            out = out[: int(limit_n)]
        return out

    async def create_index(self, *args, **kwargs):
        return await self._test.create_index(*args, **kwargs)

    async def create_indexes(self, *args, **kwargs):
        return await self._test.create_indexes(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._test, name)


async def _as_list(value):
    if hasattr(value, "to_list"):
        return await value.to_list(None)
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


READONLY_OPERATOR_STEPS = (
    "Create a DocumentDB user that can read Production db 'nmts' only, for example: "
    "db.createUser({user:'nmts_testing_readonly', pwd:'<secret>', "
    "roles:[{role:'read', db:'nmts'}]}). Store the URI on the Testing host as "
    "MONGO_READONLY_URL or as Secrets Manager secret DOCDB_READONLY_SECRET_ID. "
    "Do not commit credentials. Do not grant readWrite or clusterAdmin."
)


def resolve_production_readonly_url() -> str:
    """Dedicated Production read-only URI. Never logs the value."""
    url = (os.getenv("MONGO_READONLY_URL") or "").strip()
    if url:
        return url
    secret_id = (
        (os.getenv("DOCDB_READONLY_SECRET_ID") or "").strip()
        or (os.getenv("SNAPSHOT_SOURCE_DOCDB_SECRET_ID") or "").strip()
    )
    if secret_id:
        try:
            from .mongo_connection import fetch_documentdb_mongo_url
        except ImportError:
            from mongo_connection import fetch_documentdb_mongo_url
        env = dict(os.environ)
        env["DOCDB_SECRET_ID"] = secret_id
        return fetch_documentdb_mongo_url(env)
    raise RuntimeError(
        "Testing overlay mode requires MONGO_READONLY_URL or DOCDB_READONLY_SECRET_ID. "
        + READONLY_OPERATOR_STEPS
    )


def wrap_testing_database(client, test_db, mongo_url: str = "", mongo_kwargs: Optional[dict] = None):
    """Attach Production as a dedicated read-only base.

    Requires MONGO_READONLY_URL (or DOCDB_READONLY_SECRET_ID). Writes are also
    blocked in-process by ReadOnlyDatabase. The Testing Motor client is used
    only for nmts_testing.
    """
    from motor.motor_asyncio import AsyncIOMotorClient

    readonly_url = resolve_production_readonly_url()
    prod_name = testing_runtime.PRODUCTION_DB_NAME
    kwargs = dict(mongo_kwargs or {})
    try:
        from .mongo_connection import build_mongo_client_args
    except ImportError:
        from mongo_connection import build_mongo_client_args
    readonly_url, tls_kwargs = build_mongo_client_args(readonly_url)
    kwargs.update(tls_kwargs)
    prod_client = AsyncIOMotorClient(readonly_url, **kwargs)
    return OverlayDatabase(ReadOnlyDatabase(prod_client[prod_name]), test_db)


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
