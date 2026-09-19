"""TLS-aware MongoDB / DocumentDB client options.

This module does not open a connection. It only decides the URL and kwargs
that callers should pass to AsyncIOMotorClient / MongoClient.

When DOCDB_TLS_CA_FILE is unset or blank (local/dev fallback): return the
MONGO_URL unchanged and an empty kwargs dict so `AsyncIOMotorClient(url)`
is identical to a plain Mongo/Atlas connection.

When DOCDB_TLS_CA_FILE is set (production EC2 uses this path): load
credentials from AWS Secrets Manager at runtime (never from MONGO_URL /
never logged), then pass tls=True, tlsCAFile, retryWrites=false, and
readPreference=secondaryPreferred. Driver names stay Motor/PyMongo —
DocumentDB is Mongo wire-compatible.
"""
from __future__ import annotations

import json
import os
from typing import Any, Mapping, MutableMapping, Optional, Tuple
from urllib.parse import parse_qsl, quote_plus, urlencode, urlsplit, urlunsplit

import boto3

DOCDB_TLS_CA_ENV = "DOCDB_TLS_CA_FILE"
DOCDB_SECRET_ID_ENV = "DOCDB_SECRET_ID"
DEFAULT_DOCDB_SECRET_ID = "nmts/documentdb/credentials"
DOCDB_RETRY_WRITES = "false"
DOCDB_READ_PREFERENCE = "secondaryPreferred"


def documentdb_tls_ca_file(env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    source = env if env is not None else os.environ
    value = (source.get(DOCDB_TLS_CA_ENV) or "").strip()
    return value or None


def documentdb_secret_id(env: Optional[Mapping[str, str]] = None) -> str:
    source = env if env is not None else os.environ
    return (source.get(DOCDB_SECRET_ID_ENV) or DEFAULT_DOCDB_SECRET_ID).strip()


def mongo_url_from_documentdb_secret(secret: Mapping[str, Any]) -> str:
    """Build a mongodb:// URL from a Secrets Manager DocumentDB payload.

    The password is URL-encoded into the URL and is never included in errors.
    """
    host = str(secret.get("host") or secret.get("hostname") or "").strip()
    user = str(secret.get("username") or secret.get("user") or "").strip()
    password = secret.get("password")
    port_raw = secret.get("port") if secret.get("port") not in (None, "") else 27017
    if not host or not user or password is None or str(password) == "":
        raise ValueError("DocumentDB secret is missing host, username, or password")
    try:
        port = int(port_raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("DocumentDB secret has an invalid port") from exc
    return (
        f"mongodb://{quote_plus(user)}:{quote_plus(str(password))}"
        f"@{host}:{port}/?authSource=admin"
    )


def fetch_documentdb_mongo_url(env: Optional[Mapping[str, str]] = None) -> str:
    """Read nmts/documentdb/credentials (or DOCDB_SECRET_ID) from Secrets Manager."""
    source = env if env is not None else os.environ
    secret_id = documentdb_secret_id(source)
    region = (
        (source.get("AWS_REGION") or source.get("AWS_DEFAULT_REGION") or "us-east-1").strip()
    )
    client = boto3.client("secretsmanager", region_name=region)
    try:
        resp = client.get_secret_value(SecretId=secret_id)
    except Exception as exc:
        raise RuntimeError(
            f"Could not read DocumentDB credentials from Secrets Manager ({secret_id})"
        ) from exc
    raw = resp.get("SecretString") or ""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("DocumentDB secret is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("DocumentDB secret JSON must be an object")
    return mongo_url_from_documentdb_secret(payload)


def resolve_mongo_url(env: Optional[Mapping[str, str]] = None) -> str:
    """Atlas MONGO_URL unless DocumentDB TLS is enabled, then Secrets Manager."""
    source = env if env is not None else os.environ
    if documentdb_tls_ca_file(source):
        return fetch_documentdb_mongo_url(source)
    url = source.get("MONGO_URL")
    if not url:
        raise KeyError("MONGO_URL")
    return url


def ensure_documentdb_uri_options(mongo_url: str) -> str:
    """Ensure DocumentDB-required URI options are present on the connection string."""
    parts = urlsplit(mongo_url)
    query: MutableMapping[str, str] = {}
    lower_to_orig: dict[str, str] = {}
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        query[key] = value
        lower_to_orig[key.lower()] = key

    if "retrywrites" in lower_to_orig:
        del query[lower_to_orig["retrywrites"]]
    query["retryWrites"] = DOCDB_RETRY_WRITES

    if "readpreference" in lower_to_orig:
        del query[lower_to_orig["readpreference"]]
    query["readPreference"] = DOCDB_READ_PREFERENCE

    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def build_mongo_client_args(
    mongo_url: str,
    env: Optional[Mapping[str, str]] = None,
) -> Tuple[str, dict[str, Any]]:
    """Return (url, kwargs) for AsyncIOMotorClient / MongoClient.

    When DOCDB_TLS_CA_FILE is unset, returns (mongo_url, {}) so the caller
    constructs AsyncIOMotorClient(mongo_url) with no extra TLS args — identical
    to the historical Atlas connection.
    """
    ca_file = documentdb_tls_ca_file(env)
    if not ca_file:
        return mongo_url, {}
    url = ensure_documentdb_uri_options(mongo_url)
    kwargs: dict[str, Any] = {
        "tls": True,
        "tlsCAFile": ca_file,
        "retryWrites": False,
        "readPreference": DOCDB_READ_PREFERENCE,
    }
    return url, kwargs
