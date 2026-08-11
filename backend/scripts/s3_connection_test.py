#!/usr/bin/env python3
"""Safe S3 connectivity check using environment credentials only.

Never prints AWS_ACCESS_KEY_ID or AWS_SECRET_ACCESS_KEY values.
Does not call DeleteObject.
"""

from __future__ import annotations

import os
import sys
from typing import Dict, List

import boto3
from botocore.exceptions import BotoCoreError, ClientError

REQUIRED_ENV_VARS = (
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_REGION",
    "AWS_S3_BUCKET",
)

TEST_KEY = "uploads/s3-connection-test.txt"
TEST_BODY = b"nmts-s3-connection-test\n"
LIST_PREFIXES = ("uploads/", "history/", "reports/")


def _missing_env_vars() -> List[str]:
    return [name for name in REQUIRED_ENV_VARS if not os.environ.get(name)]


def _client(region: str):
    # boto3 reads AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY from the environment.
    return boto3.client("s3", region_name=region)


def _safe_error_summary(exc: BaseException) -> str:
    """Return a credential-safe error summary for logs."""
    if isinstance(exc, ClientError):
        err = exc.response.get("Error", {})
        meta = exc.response.get("ResponseMetadata", {})
        code = err.get("Code", "Unknown")
        message = err.get("Message", "")
        status = meta.get("HTTPStatusCode")
        # Never include request payloads; code/message/status are safe.
        return f"ClientError code={code} status={status} message={message}"
    return f"{exc.__class__.__name__}"


def _credential_shape_warnings() -> List[str]:
    """Structural checks only — never prints secret values."""
    warnings: List[str] = []
    access_key = os.environ.get("AWS_ACCESS_KEY_ID", "")
    if access_key and not (
        access_key.startswith("AKIA") or access_key.startswith("ASIA")
    ):
        warnings.append(
            "AWS_ACCESS_KEY_ID does not start with AKIA/ASIA "
            f"(len={len(access_key)})"
        )
    if access_key and ("/" in access_key):
        warnings.append(
            "AWS_ACCESS_KEY_ID contains '/' which breaks SigV4 credential scope"
        )
    secret = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
    if secret:
        warnings.append(f"AWS_SECRET_ACCESS_KEY present (len={len(secret)})")
    return warnings


def _list_prefix(client, bucket: str, prefix: str) -> Dict[str, object]:
    response = client.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=10)
    keys = [item["Key"] for item in response.get("Contents", [])]
    return {
        "prefix": prefix,
        "key_count": response.get("KeyCount", 0),
        "is_truncated": response.get("IsTruncated", False),
        "sample_keys": keys,
    }


def main() -> int:
    missing = _missing_env_vars()
    if missing:
        print("MISSING_ENV_VARS:")
        for name in missing:
            print(f"  - {name}")
        return 1

    region = os.environ["AWS_REGION"]
    bucket = os.environ["AWS_S3_BUCKET"]

    print(f"bucket: {bucket}")
    print(f"region: {region}")
    print("credentials: loaded from environment (values not printed)")
    for warning in _credential_shape_warnings():
        print(f"credential_check: {warning}")

    client = _client(region)

    try:
        client.head_bucket(Bucket=bucket)
        print("HeadBucket: ok")
    except (ClientError, BotoCoreError) as exc:
        print(f"HeadBucket: failed ({_safe_error_summary(exc)})")
        return 2

    try:
        client.put_object(
            Bucket=bucket,
            Key=TEST_KEY,
            Body=TEST_BODY,
            ContentType="text/plain",
        )
        print(f"PutObject: ok ({TEST_KEY})")
    except (ClientError, BotoCoreError) as exc:
        print(f"PutObject: failed ({_safe_error_summary(exc)})")
        return 3

    try:
        obj = client.get_object(Bucket=bucket, Key=TEST_KEY)
        body = obj["Body"].read()
        if body == TEST_BODY:
            print("GetObject: ok (content verified)")
        else:
            print("GetObject: failed (content mismatch)")
            return 4
    except (ClientError, BotoCoreError) as exc:
        print(f"GetObject: failed ({_safe_error_summary(exc)})")
        return 4

    print("ListObjectsV2:")
    for prefix in LIST_PREFIXES:
        try:
            result = _list_prefix(client, bucket, prefix)
            print(
                f"  {result['prefix']}: key_count={result['key_count']} "
                f"truncated={result['is_truncated']} sample_keys={result['sample_keys']}"
            )
        except (ClientError, BotoCoreError) as exc:
            print(f"  {prefix}: failed ({_safe_error_summary(exc)})")
            return 5

    print("DeleteObject: skipped (by design)")
    print("S3_CONNECTION_TEST: success")
    return 0


if __name__ == "__main__":
    sys.exit(main())
