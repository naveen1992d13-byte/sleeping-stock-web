"""S3 historical reader for terminal Request Center packages.

Mongo remains the operational source. Archived packages are merged for History /
print / reports so Request Mongo cleanup cannot be enabled until this path is proven.
"""

from __future__ import annotations

import gzip
import json
import logging
from io import BytesIO
from typing import Any, Dict, List, Optional

import archive_keys as ak
import archive_manifest as am
from s3_storage import get_storage

logger = logging.getLogger(__name__)


def _iter_jsonl_gz(data: bytes):
    with gzip.GzipFile(fileobj=BytesIO(data), mode="rb") as gz:
        for line in gz:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line.decode("utf-8"))


def _s3_download(storage_key: str) -> Optional[bytes]:
    storage = get_storage()
    if not storage.is_s3() or not storage_key:
        return None
    try:
        data, _ = storage.download_bytes(storage_key)
        head = storage.head(storage_key)
        if head and str(head.get("storage_provider") or "").lower() == "local":
            return None
        return data
    except Exception as exc:
        logger.warning("request archive read failed %s: %s", storage_key, type(exc).__name__)
        return None


def unpack_request_package(data: bytes) -> Dict[str, Any]:
    requests: List[Dict[str, Any]] = []
    headers: List[Dict[str, Any]] = []
    activity: List[Dict[str, Any]] = []
    for row in _iter_jsonl_gz(data):
        kind = str(row.get("record_type") or "")
        body = {k: v for k, v in row.items() if k != "record_type"}
        if kind == "order_request":
            requests.append(body)
        elif kind == "request_header":
            headers.append(body)
        elif kind == "order_activity":
            activity.append(body)
        elif row.get("id") and (row.get("request_number") or row.get("status")):
            requests.append(row)
    return {"requests": requests, "request_headers": headers, "order_activity": activity}


async def read_request_package(db, request_id: str) -> Optional[Dict[str, Any]]:
    manifest = await am.find_s3_readable_entity(db, ak.MODULE_REQUESTS, request_id)
    if not manifest:
        return None
    data = _s3_download(manifest.get("storage_key") or "")
    if not data:
        return None
    packed = unpack_request_package(data)
    packed["source"] = "s3"
    packed["manifest_status"] = manifest.get("status")
    packed["lifecycle_status"] = manifest.get("lifecycle_status")
    return packed


def _primary_request(packed: Dict[str, Any], request_id: str) -> Optional[Dict[str, Any]]:
    for row in packed.get("requests") or []:
        if str(row.get("id") or "") == str(request_id):
            return row
    rows = packed.get("requests") or []
    return rows[0] if rows else None


async def list_archived_requests(
    db,
    *,
    exclude_ids: Optional[set] = None,
    limit: int = 2000,
) -> List[Dict[str, Any]]:
    exclude_ids = exclude_ids or set()
    rows = await am.list_s3_readable_entities(db, ak.MODULE_REQUESTS, limit=limit)
    out: List[Dict[str, Any]] = []
    for man in rows:
        eid = str(man.get("entity_id") or "")
        if not eid or eid in exclude_ids:
            continue
        packed = await read_request_package(db, eid)
        if not packed:
            continue
        row = _primary_request(packed, eid)
        if not row:
            continue
        row = dict(row)
        row["archive_source"] = "s3"
        row["lifecycle_status"] = man.get("lifecycle_status")
        out.append(row)
        if len(out) >= limit:
            break
    return out
