#!/usr/bin/env python3
"""One-time Gmail API OAuth helper. Does not send mail and never asks for a password.

Run this on a machine where you can sign in as notifications@sleepingstock.in
in a browser. It prints a refresh token to paste into the testing .env.

  cd backend
  ./venv/bin/python scripts/gmail_oauth_setup.py

Required env (from the Google Cloud OAuth client):
  GMAIL_OAUTH_CLIENT_ID
  GMAIL_OAUTH_CLIENT_SECRET
"""
from __future__ import annotations

import os
import sys

SCOPE = "https://www.googleapis.com/auth/gmail.send"


def main() -> int:
    client_id = (os.environ.get("GMAIL_OAUTH_CLIENT_ID") or "").strip()
    client_secret = (os.environ.get("GMAIL_OAUTH_CLIENT_SECRET") or "").strip()
    if not client_id or not client_secret:
        print(
            "Set GMAIL_OAUTH_CLIENT_ID and GMAIL_OAUTH_CLIENT_SECRET, then rerun.\n"
            "Sign in as notifications@sleepingstock.in only. Do not enter a Gmail password here.",
            file=sys.stderr,
        )
        return 2
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_config(
        {
            "installed": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://127.0.0.1"],
            }
        },
        scopes=[SCOPE],
    )
    creds = flow.run_local_server(
        port=0,
        prompt="consent",
        access_type="offline",
    )
    if not creds.refresh_token:
        print("No refresh token returned. Revoke the app access and retry with prompt=consent.", file=sys.stderr)
        return 1
    print("GMAIL_OAUTH_REFRESH_TOKEN=" + creds.refresh_token)
    print("Authorized account should be notifications@sleepingstock.in")
    return 0


if __name__ == "__main__":
    sys.exit(main())
