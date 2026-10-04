#!/usr/bin/env python3
"""One-time Gmail API OAuth helper. Does not send mail and never asks for a password.

Sign in as naveen@sleepingstock.in only. notifications@sleepingstock.in is a
Send-as / alternate-email alias of that mailbox — not a separate Workspace
user and not an OAuth identity. Do not create a second paid user for the alias.

On a machine with a browser (local server callback):
  ./venv/bin/python scripts/gmail_oauth_setup.py --client-secrets /path/to/client.json

On a headless host, print the Google consent URL, then finish with the redirect:
  ./venv/bin/python scripts/gmail_oauth_setup.py --client-secrets /path/to/client.json --print-auth-url
  ./venv/bin/python scripts/gmail_oauth_setup.py --client-secrets /path/to/client.json --auth-response 'http://localhost/?code=...'

Required either --client-secrets (Desktop client JSON) or:
  GMAIL_OAUTH_CLIENT_ID
  GMAIL_OAUTH_CLIENT_SECRET
"""
from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from pathlib import Path

SCOPE = "https://www.googleapis.com/auth/gmail.send"
OAUTH_USER = "naveen@sleepingstock.in"
SEND_AS_ALIAS = "notifications@sleepingstock.in"
DEFAULT_PENDING = "/opt/nmts-testing/secrets/gmail_oauth_pending.json"


def _client_config_from_env() -> dict:
    client_id = (os.environ.get("GMAIL_OAUTH_CLIENT_ID") or "").strip()
    client_secret = (os.environ.get("GMAIL_OAUTH_CLIENT_SECRET") or "").strip()
    if not client_id or not client_secret:
        return {}
    return {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://127.0.0.1", "http://localhost"],
        }
    }


def _load_client_config(client_secrets: str) -> dict:
    if client_secrets:
        path = Path(client_secrets)
        data = json.loads(path.read_text())
        if "installed" not in data and "web" not in data:
            raise SystemExit("Client JSON is not a Desktop/Web OAuth client file.")
        return data
    data = _client_config_from_env()
    if not data:
        raise SystemExit(
            "Set --client-secrets or GMAIL_OAUTH_CLIENT_ID and GMAIL_OAUTH_CLIENT_SECRET.\n"
            f"Sign in as {OAUTH_USER} only. Do not sign in as {SEND_AS_ALIAS}.\n"
            "Do not enter a Gmail password here."
        )
    return data


def _flow(client_config: dict):
    from google_auth_oauthlib.flow import InstalledAppFlow

    return InstalledAppFlow.from_client_config(client_config, scopes=[SCOPE])


def _redirect_uri(client_config: dict) -> str:
    block = client_config.get("installed") or client_config.get("web") or {}
    uris = [str(u).strip() for u in (block.get("redirect_uris") or []) if str(u).strip()]
    for preferred in ("http://localhost", "http://127.0.0.1", "http://localhost/", "http://127.0.0.1/"):
        if preferred in uris:
            return preferred
    return uris[0] if uris else "http://localhost"


def _write_private(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def _print_auth_url(client_config: dict, pending_path: Path) -> int:
    flow = _flow(client_config)
    flow.redirect_uri = _redirect_uri(client_config)
    auth_url, state = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        include_granted_scopes="true",
        login_hint=OAUTH_USER,
    )
    _write_private(
        pending_path,
        {
            "state": state,
            "redirect_uri": flow.redirect_uri,
            "code_verifier": getattr(flow, "code_verifier", None),
        },
    )
    print("Sign in as", OAUTH_USER, "only.")
    print("Do not sign in as", SEND_AS_ALIAS)
    print("Do not enter a Gmail password into this script.")
    print("After Google redirects, copy the full browser address bar URL and rerun with --auth-response.")
    print("AUTHORIZATION_URL")
    print(auth_url)
    return 0


def _finish_auth(client_config: dict, pending_path: Path, auth_response: str) -> int:
    pending = json.loads(pending_path.read_text())
    flow = _flow(client_config)
    flow.redirect_uri = pending.get("redirect_uri") or _redirect_uri(client_config)
    if pending.get("code_verifier"):
        flow.code_verifier = pending["code_verifier"]
    flow.fetch_token(authorization_response=auth_response.strip())
    creds = flow.credentials
    if not creds.refresh_token:
        print("No refresh token returned. Revoke the app access and retry with prompt=consent.", file=sys.stderr)
        return 1
    print("GMAIL_OAUTH_REFRESH_TOKEN=" + creds.refresh_token)
    print(f"Authorized account must be {OAUTH_USER}")
    print(f"RFC822 From stays Sleeping Stock <{SEND_AS_ALIAS}>")
    try:
        pending_path.unlink()
    except OSError:
        pass
    return 0


def _local_server(client_config: dict) -> int:
    flow = _flow(client_config)
    creds = flow.run_local_server(
        port=0,
        prompt="consent",
        access_type="offline",
        login_hint=OAUTH_USER,
    )
    if not creds.refresh_token:
        print("No refresh token returned. Revoke the app access and retry with prompt=consent.", file=sys.stderr)
        return 1
    print("GMAIL_OAUTH_REFRESH_TOKEN=" + creds.refresh_token)
    print(f"Authorized account must be {OAUTH_USER}")
    print(f"RFC822 From stays Sleeping Stock <{SEND_AS_ALIAS}>")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Gmail API OAuth setup for Sleeping Stock.")
    parser.add_argument("--client-secrets", default="", help="Desktop OAuth client JSON path")
    parser.add_argument("--print-auth-url", action="store_true", help="Print Google consent URL and stop")
    parser.add_argument("--auth-response", default="", help="Full localhost redirect URL after owner consent")
    parser.add_argument("--pending-file", default=DEFAULT_PENDING)
    args = parser.parse_args()
    client_config = _load_client_config(args.client_secrets)
    pending_path = Path(args.pending_file)
    if args.print_auth_url:
        return _print_auth_url(client_config, pending_path)
    if args.auth_response:
        return _finish_auth(client_config, pending_path, args.auth_response)
    return _local_server(client_config)


if __name__ == "__main__":
    sys.exit(main())
