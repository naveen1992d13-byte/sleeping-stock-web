"""Expo push notifications for Sleeping Stock Mobile (best-effort, non-blocking)."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger("nmts.mobile_push")

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"


def _now():
    return datetime.now(timezone.utc)


async def log_push_attempt(db, *, mobile_user_id: str, device_id: str, push_token: str, title: str, body: str, data: dict, status: str, detail: str = ""):
    await db.mobile_push_delivery_logs.insert_one(
        {
            "id": str(uuid.uuid4()),
            "mobile_user_id": mobile_user_id,
            "device_id": device_id,
            "push_token_prefix": (push_token or "")[:12],
            "title": title,
            "body": body,
            "data": data,
            "status": status,
            "detail": detail,
            "created_at": _now(),
        }
    )


def send_expo_push_messages(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not messages:
        return {"ok": True, "sent": 0}
    try:
        resp = requests.post(
            EXPO_PUSH_URL,
            json=messages,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=15,
        )
        return {"ok": resp.ok, "status_code": resp.status_code, "body": resp.json() if resp.content else {}}
    except Exception as exc:
        logger.warning("Expo push send failed: %s", exc)
        return {"ok": False, "error": str(exc)}


async def notify_auto_perpetual_assignments(db, *, assignments_by_user: Dict[str, int], branch: str, allocation_date: str):
    """Send push to each mobile user who received auto work today. Never raises."""
    title = "Auto Perpetual Stock Verification"
    for mobile_user_id, count in assignments_by_user.items():
        if count <= 0:
            continue
        devices = await db.mobile_devices.find(
            {"mobile_user_id": mobile_user_id, "status": "active", "push_token": {"$exists": True, "$ne": ""}},
            {"_id": 0, "device_id": 1, "push_token": 1},
        ).to_list(20)
        body = f"Today's task: verify {count} assigned part(s) for {branch}."
        data = {
            "type": "auto_perpetual",
            "screen": "auto_perpetual",
            "allocation_date": allocation_date,
            "branch": branch,
            "task_count": count,
        }
        batch = []
        for dev in devices:
            token = dev.get("push_token")
            if not token:
                continue
            batch.append(
                {
                    "to": token,
                    "title": title,
                    "body": body,
                    "sound": "default",
                    "priority": "high",
                    "channelId": "sleeping-stock-requests",
                    "data": data,
                }
            )
        if not batch:
            await log_push_attempt(
                db,
                mobile_user_id=mobile_user_id,
                device_id="",
                push_token="",
                title=title,
                body=body,
                data=data,
                status="skipped",
                detail="no_active_push_token",
            )
            continue
        result = send_expo_push_messages(batch)
        status = "sent" if result.get("ok") else "failed"
        detail = str(result.get("error") or result.get("body") or "")[:500]
        for dev in devices:
            await log_push_attempt(
                db,
                mobile_user_id=mobile_user_id,
                device_id=dev.get("device_id", ""),
                push_token=dev.get("push_token", ""),
                title=title,
                body=body,
                data=data,
                status=status,
                detail=detail,
            )


_REQUEST_PUSH_COPY = {
    "new": (
        "New Parts Transfer Request",
        "New request {request_number} from {requesting_branch}. Open Request Center to respond.",
    ),
    "reminder_1": (
        "Request reminder",
        "Request {request_number} is still awaiting your response.",
    ),
    "reminder_2": (
        "Request reminder",
        "Request {request_number} still needs a response.",
    ),
    "reminder_3": (
        "Request deadline approaching",
        "Request {request_number} is near its response deadline.",
    ),
}


async def notify_branch_request_push(db, group_doc: dict, kind: str = "new"):
    """High-priority request push to supplying-branch mobile devices. Never raises."""
    try:
        dealer = (group_doc or {}).get("supplying_dealer") or ""
        branch = (group_doc or {}).get("supplying_branch") or ""
        request_number = (group_doc or {}).get("request_number") or ""
        request_group_key = (group_doc or {}).get("id") or request_number
        if not dealer or not branch or not request_number:
            return {"ok": False, "error": "missing_scope"}
        title_tpl, body_tpl = _REQUEST_PUSH_COPY.get(kind, _REQUEST_PUSH_COPY["new"])
        title = title_tpl
        body = body_tpl.format(
            request_number=request_number,
            requesting_branch=(group_doc or {}).get("requesting_branch") or "",
        )
        data = {
            "type": "branch_request",
            "screen": "request",
            "kind": kind,
            "request_group_key": request_group_key,
            "request_number": request_number,
        }
        devices = await db.mobile_devices.find(
            {
                "dealer_name": dealer,
                "branch": branch,
                "status": "active",
                "push_token": {"$exists": True, "$ne": ""},
            },
            {"_id": 0, "device_id": 1, "push_token": 1, "mobile_user_id": 1},
        ).to_list(100)
        if not devices:
            await log_push_attempt(
                db,
                mobile_user_id="",
                device_id="",
                push_token="",
                title=title,
                body=body,
                data=data,
                status="skipped",
                detail="no_active_push_token",
            )
            return {"ok": True, "sent": 0, "skipped": True}
        batch = [
            {
                "to": dev.get("push_token"),
                "title": title,
                "body": body,
                "sound": "default",
                "priority": "high",
                "channelId": "sleeping-stock-requests",
                "data": data,
            }
            for dev in devices
            if dev.get("push_token")
        ]
        result = send_expo_push_messages(batch)
        status = "sent" if result.get("ok") else "failed"
        detail = str(result.get("error") or result.get("body") or "")[:500]
        for dev in devices:
            await log_push_attempt(
                db,
                mobile_user_id=dev.get("mobile_user_id", ""),
                device_id=dev.get("device_id", ""),
                push_token=dev.get("push_token", ""),
                title=title,
                body=body,
                data=data,
                status=status,
                detail=detail,
            )
        return {"ok": result.get("ok"), "sent": len(batch), "kind": kind}
    except Exception as exc:
        logger.warning("branch request push failed: %s", exc)
        return {"ok": False, "error": str(exc)[:300]}
