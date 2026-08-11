"""Unified durable file object helper: S3 (or local object store) + legacy disk fallback."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from fastapi.responses import Response, StreamingResponse
from io import BytesIO

from s3_storage import get_storage, guess_content_type, sha256_bytes

logger = logging.getLogger(__name__)


async def store_bytes(
    *,
    module: str,
    relative_key: str,
    data: bytes,
    original_filename: str,
    content_type: Optional[str] = None,
) -> Dict[str, Any]:
    """Store durable bytes in object storage and return Mongo-friendly metadata."""
    storage = get_storage()
    key = storage.key(module, relative_key)
    ctype = content_type or guess_content_type(original_filename)
    stored = storage.upload_bytes(key, data, content_type=ctype)
    return {
        "storage_provider": stored.storage_provider,
        "storage_key": stored.storage_key,
        "original_filename": original_filename,
        "content_type": stored.content_type,
        "file_size": stored.file_size,
        "sha256": stored.sha256,
        "archived_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
    }


def read_bytes_from_meta(meta: Dict[str, Any]) -> Tuple[bytes, str]:
    """Read bytes using storage_key when present, else legacy local path fields."""
    storage_key = meta.get("storage_key")
    if storage_key:
        storage = get_storage()
        data, ctype = storage.download_bytes(storage_key)
        return data, meta.get("content_type") or ctype

    # Legacy disk paths used across modules
    for field in (
        "storage_path",
        "attachment_storage_path",
        "apk_path",
        "local_path",
        "file_path",
    ):
        path_val = meta.get(field)
        if path_val:
            path = Path(path_val)
            if path.is_file():
                data = path.read_bytes()
                ctype = meta.get("content_type") or guess_content_type(meta.get("original_filename") or path.name)
                return data, ctype

    # Legacy Mongo blob
    raw = meta.get("raw_file_bytes") or meta.get("fileBytes")
    if raw is not None:
        if isinstance(raw, memoryview):
            raw = raw.tobytes()
        elif not isinstance(raw, (bytes, bytearray)):
            raw = bytes(raw)
        ctype = meta.get("content_type") or meta.get("raw_file_content_type") or meta.get("contentType") or "application/octet-stream"
        return bytes(raw), ctype

    raise FileNotFoundError("No storage_key, legacy path, or embedded bytes available")


def streaming_response_from_meta(meta: Dict[str, Any], filename: Optional[str] = None) -> Response:
    data, ctype = read_bytes_from_meta(meta)
    name = filename or meta.get("original_filename") or meta.get("file_name") or meta.get("fileName") or meta.get("apk_filename") or "download.bin"
    return StreamingResponse(
        BytesIO(data),
        media_type=ctype or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


def meta_has_readable_bytes(meta: Dict[str, Any]) -> bool:
    if meta.get("storage_key"):
        return True
    for field in ("storage_path", "attachment_storage_path", "apk_path", "local_path", "file_path"):
        path_val = meta.get(field)
        if path_val and Path(path_val).is_file():
            return True
    if meta.get("raw_file_bytes") is not None or meta.get("fileBytes") is not None:
        return True
    return False
