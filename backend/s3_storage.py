"""Reusable NMTS S3 storage service with local filesystem fallback.

Credentials are read only from environment variables and never returned to
callers (so frontend/mobile never see AWS secrets).

When AWS is unavailable or credentials are invalid, the service falls back to
a local object-store directory so archives and uploads can still be exercised
in tests / cloud agents without inventing credentials.
"""

from __future__ import annotations

import hashlib
import logging
import mimetypes
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Env keys (approved)
ENV_AWS_REGION = "AWS_REGION"
ENV_AWS_ACCESS_KEY_ID = "AWS_ACCESS_KEY_ID"
ENV_AWS_SECRET_ACCESS_KEY = "AWS_SECRET_ACCESS_KEY"
ENV_NMTS_S3_BUCKET = "NMTS_S3_BUCKET"
ENV_AWS_S3_BUCKET = "AWS_S3_BUCKET"  # legacy alias
ENV_NMTS_STORAGE_ENV = "NMTS_STORAGE_ENV"
ENV_PRODUCT_MONGO_HOT_DAYS = "PRODUCT_MONGO_HOT_DAYS"
ENV_VERIFICATION_MONGO_HOT_DAYS = "VERIFICATION_MONGO_HOT_DAYS"
ENV_ARCHIVE_PRUNE_ENABLED = "ARCHIVE_PRUNE_ENABLED"
ENV_ARCHIVE_SCHEDULER_ENABLED = "ARCHIVE_SCHEDULER_ENABLED"
ENV_LOCAL_OBJECT_STORE = "NMTS_LOCAL_OBJECT_STORE"


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def storage_env() -> str:
    return (os.getenv(ENV_NMTS_STORAGE_ENV) or "dev").strip() or "dev"


def product_mongo_hot_days() -> int:
    """Calendar days of live Product rows kept in Mongo (IST).

    Final policy default is 1 = today/current operational Product data only.
    Closed previous calendar days are archived; prune (when enabled + real S3)
    removes only verified historical dates — never today's live set.
    """
    return max(1, _env_int(ENV_PRODUCT_MONGO_HOT_DAYS, 1))


def verification_mongo_hot_days() -> int:
    # Keep a longer verification hot window — Auto Perpetual / MTD still need
    # current-month raw verification rows in Mongo.
    return max(1, _env_int(ENV_VERIFICATION_MONGO_HOT_DAYS, 90))


def archive_prune_enabled() -> bool:
    # Approved default: false — never mass-delete until Master enables after
    # verified REAL S3 archives exist.
    return _env_bool(ENV_ARCHIVE_PRUNE_ENABLED, False)


# Estimated S3 cost inputs (overridable). Labels must say "Estimated Cost".
ENV_S3_STORAGE_PRICE_PER_GB_MONTH = "S3_STORAGE_PRICE_PER_GB_MONTH"
ENV_S3_PUT_PRICE_PER_1000 = "S3_PUT_PRICE_PER_1000"
ENV_S3_GET_PRICE_PER_1000 = "S3_GET_PRICE_PER_1000"
ENV_S3_FREE_EGRESS_GB = "S3_FREE_EGRESS_GB"
ENV_S3_EGRESS_PRICE_PER_GB = "S3_EGRESS_PRICE_PER_GB"


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def s3_pricing_config() -> Dict[str, float]:
    """Transparent estimate model — not a final AWS invoice."""
    return {
        "storage_price_per_gb_month": _env_float(ENV_S3_STORAGE_PRICE_PER_GB_MONTH, 0.023),
        "put_price_per_1000": _env_float(ENV_S3_PUT_PRICE_PER_1000, 0.005),
        "get_price_per_1000": _env_float(ENV_S3_GET_PRICE_PER_1000, 0.0004),
        "free_egress_gb": _env_float(ENV_S3_FREE_EGRESS_GB, 100.0),
        "egress_price_per_gb": _env_float(ENV_S3_EGRESS_PRICE_PER_GB, 0.09),
    }


def archive_scheduler_enabled() -> bool:
    return _env_bool(ENV_ARCHIVE_SCHEDULER_ENABLED, True)


def resolve_bucket() -> str:
    return (os.getenv(ENV_NMTS_S3_BUCKET) or os.getenv(ENV_AWS_S3_BUCKET) or "").strip()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _aws_error_code(exc: BaseException) -> str:
    """Best-effort AWS error code only — never log secret values or full messages."""
    resp = getattr(exc, "response", None)
    if isinstance(resp, dict):
        err = resp.get("Error") if isinstance(resp.get("Error"), dict) else {}
        code = err.get("Code") if err else None
        if code:
            return str(code)[:64]
    return ""


def guess_content_type(filename: str, fallback: str = "application/octet-stream") -> str:
    ctype, _ = mimetypes.guess_type(filename or "")
    return ctype or fallback


def build_key(*parts: str) -> str:
    """Join key segments safely under {env}/..."""
    cleaned = []
    for part in parts:
        text = str(part or "").strip().replace("\\", "/").lstrip("/")
        text = "/".join(seg for seg in text.split("/") if seg not in {"", ".", ".."})
        if text:
            cleaned.append(text)
    return "/".join(cleaned)


@dataclass
class StoredObject:
    storage_provider: str
    storage_key: str
    content_type: str
    file_size: int
    sha256: str
    etag: Optional[str] = None


class S3StorageError(RuntimeError):
    pass


class _LocalObjectStore:
    """Filesystem-backed object store used when AWS is unavailable."""

    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, key: str) -> Path:
        safe = build_key(key)
        path = (self.root / safe).resolve()
        if self.root.resolve() not in path.parents and path != self.root.resolve():
            raise S3StorageError("Invalid object key path")
        return path

    def put(self, key: str, data: bytes, content_type: str) -> StoredObject:
        path = self._path(key)
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            meta = path.with_suffix(path.suffix + ".meta")
            meta.write_text(f"{content_type}\n{sha256_bytes(data)}\n", encoding="utf-8")
        return StoredObject(
            storage_provider="local",
            storage_key=key,
            content_type=content_type,
            file_size=len(data),
            sha256=sha256_bytes(data),
        )

    def get(self, key: str) -> Tuple[bytes, str]:
        path = self._path(key)
        if not path.is_file():
            raise FileNotFoundError(key)
        data = path.read_bytes()
        ctype = "application/octet-stream"
        meta = path.with_suffix(path.suffix + ".meta")
        if meta.is_file():
            lines = meta.read_text(encoding="utf-8").splitlines()
            if lines:
                ctype = lines[0] or ctype
        return data, ctype

    def head(self, key: str) -> Optional[Dict[str, Any]]:
        path = self._path(key)
        if not path.is_file():
            return None
        data = path.read_bytes()
        ctype = "application/octet-stream"
        digest = sha256_bytes(data)
        meta = path.with_suffix(path.suffix + ".meta")
        if meta.is_file():
            lines = meta.read_text(encoding="utf-8").splitlines()
            if lines:
                ctype = lines[0] or ctype
            if len(lines) > 1 and lines[1]:
                digest = lines[1]
        return {
            "storage_key": key,
            "content_type": ctype,
            "file_size": len(data),
            "sha256": digest,
            "exists": True,
            "storage_provider": "local",
        }

    def delete(self, key: str) -> bool:
        path = self._path(key)
        meta = path.with_suffix(path.suffix + ".meta")
        existed = path.is_file()
        if path.is_file():
            path.unlink()
        if meta.is_file():
            meta.unlink()
        return existed


class S3StorageService:
    """Upload / download / head / integrity helpers for NMTS archives and files."""

    def __init__(self):
        self.region = (os.getenv(ENV_AWS_REGION) or "").strip() or "us-east-1"  # pragma: allowlist secret
        self.bucket = resolve_bucket()
        self.env = storage_env()
        self.access_key = (os.getenv(ENV_AWS_ACCESS_KEY_ID) or "").strip()
        self.secret_key = (os.getenv(ENV_AWS_SECRET_ACCESS_KEY) or "").strip()
        local_root = Path(
            os.getenv(ENV_LOCAL_OBJECT_STORE)
            or (Path(__file__).resolve().parent / ".local_object_store")
        )
        self._local = _LocalObjectStore(local_root)
        self._client = None
        self._mode = "local"
        self._ensure_attempted = False
        self._init_client()

    def _credentials_look_valid(self) -> bool:
        # IAM access keys are typically 16–24 chars starting with AKIA/ASIA.
        if not self.access_key or not self.secret_key or not self.bucket:
            return False
        if "/" in self.access_key or len(self.access_key) > 32:
            return False
        if not (self.access_key.startswith("AKIA") or self.access_key.startswith("ASIA")):
            return False
        return True

    def _init_client(self) -> None:
        if not self._credentials_look_valid():
            self._mode = "local"
            logger.warning(
                "S3 credentials/bucket unavailable or malformed; using local object store at %s",
                self._local.root,
            )
            return
        try:
            import boto3
            from botocore.config import Config

            self._client = boto3.client(
                "s3",
                region_name=self.region,
                aws_access_key_id=self.access_key,
                aws_secret_access_key=self.secret_key,
                config=Config(signature_version="s3v4"),
            )
            # Lightweight probe — do not raise into callers at import time.
            self._client.head_bucket(Bucket=self.bucket)
            self._mode = "s3"
            logger.info("S3 storage ready (bucket=%s region=%s env=%s)", self.bucket, self.region, self.env)
        except Exception as exc:
            self._client = None
            self._mode = "local"
            err_code = _aws_error_code(exc)
            logger.warning(
                "S3 init failed (%s%s); using local object store",
                type(exc).__name__,
                f" code={err_code}" if err_code else "",
            )

    def ensure_s3(self) -> bool:
        """One-time re-init after a failed first probe. Does not bypass the REAL S3 gate."""
        if self.is_s3():
            return True
        if getattr(self, "_ensure_attempted", False):
            return False
        self._ensure_attempted = True
        self.region = (os.getenv(ENV_AWS_REGION) or "").strip() or self.region or "us-east-1"
        self.bucket = resolve_bucket()
        self.env = storage_env()
        self.access_key = (os.getenv(ENV_AWS_ACCESS_KEY_ID) or "").strip()
        self.secret_key = (os.getenv(ENV_AWS_SECRET_ACCESS_KEY) or "").strip()
        logger.info("Retrying S3 initialization once")
        self._init_client()
        return self.is_s3()

    @property
    def mode(self) -> str:
        return self._mode

    def is_s3(self) -> bool:
        return self._mode == "s3" and self._client is not None

    def key(self, *parts: str) -> str:
        return build_key(self.env, *parts)

    def upload_bytes(
        self,
        key: str,
        data: bytes,
        content_type: Optional[str] = None,
        metadata: Optional[Dict[str, str]] = None,
    ) -> StoredObject:
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("data must be bytes")
        data = bytes(data)
        ctype = content_type or "application/octet-stream"
        digest = sha256_bytes(data)
        if self.is_s3():
            try:
                extra: Dict[str, Any] = {"ContentType": ctype}
                meta = {"sha256": digest}
                if metadata:
                    meta.update({str(k): str(v)[:1024] for k, v in metadata.items()})
                extra["Metadata"] = meta
                resp = self._client.put_object(Bucket=self.bucket, Key=key, Body=data, **extra)
                return StoredObject(
                    storage_provider="s3",
                    storage_key=key,
                    content_type=ctype,
                    file_size=len(data),
                    sha256=digest,
                    etag=(resp or {}).get("ETag"),
                )
            except Exception as exc:
                raise S3StorageError(f"S3 upload failed: {exc}") from exc
        return self._local.put(key, data, ctype)

    def download_bytes(self, key: str) -> Tuple[bytes, str]:
        if self.is_s3():
            try:
                resp = self._client.get_object(Bucket=self.bucket, Key=key)
                body = resp["Body"].read()
                ctype = resp.get("ContentType") or "application/octet-stream"
                return body, ctype
            except self._client.exceptions.NoSuchKey as exc:
                raise FileNotFoundError(key) from exc
            except Exception as exc:
                # Fall through to local for hybrid test setups
                if "NoSuchKey" in type(exc).__name__ or "404" in str(exc):
                    raise FileNotFoundError(key) from exc
                # Try local fallback before failing hard
                try:
                    return self._local.get(key)
                except FileNotFoundError:
                    raise S3StorageError(f"S3 download failed: {exc}") from exc
        return self._local.get(key)

    def exists(self, key: str) -> bool:
        return self.head(key) is not None

    def head(self, key: str) -> Optional[Dict[str, Any]]:
        if self.is_s3():
            try:
                resp = self._client.head_object(Bucket=self.bucket, Key=key)
                meta = resp.get("Metadata") or {}
                return {
                    "storage_key": key,
                    "content_type": resp.get("ContentType") or "application/octet-stream",
                    "file_size": int(resp.get("ContentLength") or 0),
                    "sha256": meta.get("sha256"),
                    "etag": resp.get("ETag"),
                    "exists": True,
                    "storage_provider": "s3",
                }
            except Exception:
                # Also check local fallback
                local = self._local.head(key)
                if local:
                    return local
                return None
        return self._local.head(key)

    def verify_object(self, key: str, expected_sha256: str, expected_size: int) -> bool:
        info = self.head(key)
        if not info:
            return False
        if int(info.get("file_size") or -1) != int(expected_size):
            return False
        digest = info.get("sha256")
        if not digest:
            data, _ = self.download_bytes(key)
            digest = sha256_bytes(data)
        return digest == expected_sha256

    def presigned_url(self, key: str, expires_seconds: int = 3600, method: str = "get_object") -> Optional[str]:
        if not self.is_s3():
            return None
        try:
            return self._client.generate_presigned_url(
                ClientMethod=method,
                Params={"Bucket": self.bucket, "Key": key},
                ExpiresIn=int(expires_seconds),
            )
        except Exception as exc:
            logger.warning("presign failed: %s", exc)
            return None

    def list_prefix(self, prefix: str = "", *, max_keys: int = 100000) -> List[Dict[str, Any]]:
        """List objects under a prefix. REAL S3 only — empty when local/unavailable."""
        out: List[Dict[str, Any]] = []
        if not self.is_s3():
            return out
        try:
            token = None
            remaining = max(1, int(max_keys))
            while remaining > 0:
                kwargs: Dict[str, Any] = {
                    "Bucket": self.bucket,
                    "Prefix": str(prefix or ""),
                    "MaxKeys": min(1000, remaining),
                }
                if token:
                    kwargs["ContinuationToken"] = token
                resp = self._client.list_objects_v2(**kwargs)
                for obj in resp.get("Contents") or []:
                    out.append(
                        {
                            "storage_key": obj.get("Key"),
                            "file_size": int(obj.get("Size") or 0),
                            "last_modified": obj.get("LastModified").isoformat()
                            if obj.get("LastModified")
                            else None,
                            "storage_provider": "s3",
                        }
                    )
                    remaining -= 1
                    if remaining <= 0:
                        break
                if not resp.get("IsTruncated"):
                    break
                token = resp.get("NextContinuationToken")
                if not token:
                    break
        except Exception as exc:
            logger.warning("S3 list_prefix failed: %s", type(exc).__name__)
        return out

    def sum_prefix_bytes(self, prefix: str = "") -> Dict[str, Any]:
        """Sum actual object sizes under prefix from REAL S3 listing."""
        if not self.is_s3():
            return {
                "ok": False,
                "actual_s3_bytes": None,
                "object_count": 0,
                "reason": "S3 credentials/config unavailable — cannot measure Actual S3 Used Storage",
            }
        objects = self.list_prefix(prefix)
        total = sum(int(o.get("file_size") or 0) for o in objects)
        return {
            "ok": True,
            "actual_s3_bytes": total,
            "object_count": len(objects),
            "reason": None,
        }

    def delete(self, key: str) -> bool:
        if self.is_s3():
            try:
                self._client.delete_object(Bucket=self.bucket, Key=key)
            except Exception as exc:
                logger.warning("S3 delete failed for %s: %s", key, exc)
        return self._local.delete(key)

    def status(self) -> Dict[str, Any]:
        """Safe status for ops — never includes secret values."""
        real_s3 = self.is_s3()
        return {
            "mode": self._mode,
            "storage_backend": "REAL S3" if real_s3 else "LOCAL FALLBACK",
            "real_s3": real_s3,
            "prune_authorized": bool(real_s3 and archive_prune_enabled()),
            "bucket_configured": bool(self.bucket),
            "region": self.region,
            "env": self.env,
            "access_key_present": bool(self.access_key),
            "access_key_looks_valid": self._credentials_look_valid(),
            "archive_prune_enabled": archive_prune_enabled(),
            "archive_scheduler_enabled": archive_scheduler_enabled(),
            "product_mongo_hot_days": product_mongo_hot_days(),
            "verification_mongo_hot_days": verification_mongo_hot_days(),
            "local_store": str(self._local.root),
            "pricing": s3_pricing_config(),
            "server_time_utc": datetime.now(timezone.utc).isoformat(),
            "warning": (
                None
                if real_s3
                else "Cloud archive not active — MongoDB pruning disabled."
            ),
        }


_SERVICE: Optional[S3StorageService] = None
_SERVICE_LOCK = threading.Lock()


def get_storage() -> S3StorageService:
    global _SERVICE
    if _SERVICE is None:
        with _SERVICE_LOCK:
            if _SERVICE is None:
                _SERVICE = S3StorageService()
    return _SERVICE


def ensure_s3() -> bool:
    """Refresh env and retry S3 init once if the singleton is still local."""
    return get_storage().ensure_s3()


def reset_storage_for_tests() -> S3StorageService:
    """Force re-init (tests only)."""
    global _SERVICE
    with _SERVICE_LOCK:
        _SERVICE = S3StorageService()
        return _SERVICE
