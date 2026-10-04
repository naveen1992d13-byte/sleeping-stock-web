#!/usr/bin/env python3
"""Read-only Amazon SES status for Sleeping Stock in ap-south-1.

Uses the default AWS credential chain (EC2 instance role). Never asks for
root credentials or long-lived access keys. Does not send mail or change DNS.
"""
from __future__ import annotations

import json
import sys

import boto3
from botocore.exceptions import BotoCoreError, ClientError

REGION = "ap-south-1"
IDENTITY = "sleepingstock.in"
FROM_EMAIL = "notification@sleepingstock.in"


def _client():
    return boto3.client("sesv2", region_name=REGION)


def _safe(label, fn):
    try:
        return {"ok": True, "label": label, "data": fn()}
    except (BotoCoreError, ClientError, Exception) as exc:
        return {"ok": False, "label": label, "error": str(exc)[:400]}


def main() -> int:
    client = _client()
    results = [
        _safe("get_account", lambda: client.get_account()),
        _safe("list_email_identities", lambda: client.list_email_identities()),
        _safe("get_email_identity_domain", lambda: client.get_email_identity(EmailIdentity=IDENTITY)),
        _safe("get_email_identity_from", lambda: client.get_email_identity(EmailIdentity=FROM_EMAIL)),
    ]
    print(json.dumps({"region": REGION, "from_email": FROM_EMAIL, "results": results}, default=str, indent=2))
    return 0 if all(item["ok"] or "AccessDenied" in item.get("error", "") for item in results) else 1


if __name__ == "__main__":
    sys.exit(main())
