"""S3 historical reader for terminal Order Desk packages.

Mongo remains the operational source. Archived packages are merged for History
views so Order Mongo cleanup cannot be enabled until this path is proven.
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
        logger.warning("order archive read failed %s: %s", storage_key, type(exc).__name__)
        return None


def unpack_order_package(data: bytes) -> Dict[str, Any]:
    header = None
    items: List[Dict[str, Any]] = []
    requests: List[Dict[str, Any]] = []
    request_headers: List[Dict[str, Any]] = []
    activity: List[Dict[str, Any]] = []
    for row in _iter_jsonl_gz(data):
        kind = str(row.get("record_type") or "")
        body = {k: v for k, v in row.items() if k != "record_type"}
        if kind == "order_header":
            header = body
        elif kind == "order_item":
            items.append(body)
        elif kind == "order_request":
            requests.append(body)
        elif kind == "request_header":
            request_headers.append(body)
        elif kind == "order_activity":
            activity.append(body)
        elif header is None and row.get("id") and row.get("order_number"):
            header = row
    return {
        "order": header or {},
        "items": items,
        "order_requests": requests,
        "request_headers": request_headers,
        "order_activity": activity,
    }


async def read_order_package(db, order_id: str) -> Optional[Dict[str, Any]]:
    manifest = await am.find_s3_readable_entity(db, ak.MODULE_ORDERS, order_id)
    if not manifest:
        return None
    data = _s3_download(manifest.get("storage_key") or "")
    if not data:
        return None
    packed = unpack_order_package(data)
    packed["source"] = "s3"
    packed["manifest_status"] = manifest.get("status")
    packed["lifecycle_status"] = manifest.get("lifecycle_status")
    return packed


async def list_archived_order_headers(
    db,
    *,
    exclude_ids: Optional[set] = None,
    brand: Optional[str] = None,
    dealer: Optional[str] = None,
    branch: Optional[str] = None,
    limit: int = 1000,
) -> List[Dict[str, Any]]:
    """Return archived order headers not already present in Mongo (id set)."""
    exclude_ids = exclude_ids or set()
    rows = await am.list_s3_readable_entities(db, ak.MODULE_ORDERS, limit=limit)
    out: List[Dict[str, Any]] = []
    for man in rows:
        eid = str(man.get("entity_id") or "")
        if not eid or eid in exclude_ids:
            continue
        if brand and str(man.get("brand_name") or "") != str(brand):
            continue
        if dealer and str(man.get("dealer_name") or "") != str(dealer):
            continue
        if branch and str(man.get("branch") or "") != str(branch):
            continue
        packed = await read_order_package(db, eid)
        header = (packed or {}).get("order") or {}
        if not header:
            continue
        header = dict(header)
        header["archive_source"] = "s3"
        header["lifecycle_status"] = man.get("lifecycle_status")
        out.append(header)
        if len(out) >= limit:
            break
    return out
