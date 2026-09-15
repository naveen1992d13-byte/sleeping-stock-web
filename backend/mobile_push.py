"""Expo push notifications for Sleeping Stock Mobile (best-effort, non-blocking)."""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger("nmts.mobile_push")

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"
_EXPO_TOKEN_RE = re.compile(r"^ExponentPushToken\[.+\]$")
ANDROID_REQUEST_CHANNEL_ID = "sleeping-stock-requests-v3"
ANDROID_REQUEST_SOUND = "sleeping_stock_alert_2_rising_dispatch.wav"
REQUEST_CATEGORY_ID = "branch-request"


def _ci_exact(field: str, value: str) -> dict:
    value = (value or "").strip()
    if not value:
        return {field: value}
    return {field: {"$regex": f"^{re.escape(value)}$", "$options": "i"}}


def is_expo_push_token(token: str) -> bool:
    return bool(_EXPO_TOKEN_RE.match((token or "").strip()))


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
        "{request_number}",
        "Requested Branch: {requesting_branch} · Items: {total_items} · Qty: {total_qty}",
    ),
    "reminder_1": (
        "{request_number}",
        "Requested Branch: {requesting_branch} · Items: {total_items} · Qty: {total_qty}",
    ),
    "reminder_2": (
        "{request_number}",
        "Requested Branch: {requesting_branch} · Items: {total_items} · Qty: {total_qty}",
    ),
    "reminder_3": (
        "{request_number}",
        "Requested Branch: {requesting_branch} · Items: {total_items} · Qty: {total_qty}",
    ),
}


_KIND_TO_ROUND = {
    "new": 0,
    "reminder_1": 1,
    "reminder_2": 2,
    "reminder_3": 3,
}


def build_branch_request_push_message(token: str, group_doc: dict, kind: str = "new") -> dict:
    """Data-only FCM payload for a branch request (initial + SLA reminder rounds).

    Title/body/sound/channel are omitted so Android delivers a data message to
    the native FirebaseMessagingService while the app is killed. SLA scheduling
    and reminder-count logic are unchanged; only the payload shape is aligned.
    """
    request_number = (group_doc or {}).get("request_number") or ""
    requesting_branch = (group_doc or {}).get("requesting_branch") or ""
    total_items = (group_doc or {}).get("total_items")
    total_qty = (group_doc or {}).get("total_qty")
    if total_items in (None, ""):
        total_items = len((group_doc or {}).get("items") or [])
    if total_qty in (None, ""):
        total_qty = (group_doc or {}).get("total_quantity") or 0
    request_group_key = (group_doc or {}).get("id") or request_number
    round_n = _KIND_TO_ROUND.get(kind, 0)
    return {
        "to": token,
        "priority": "high",
        "_contentAvailable": True,
        "data": {
            "type": "branch_request",
            "screen": "request",
            "kind": kind,
            "round": round_n,
            "requestId": request_group_key,
            "requestNumber": request_number,
            "branchName": requesting_branch,
            "totalItems": total_items,
            "totalQuantity": total_qty,
            "request_group_key": request_group_key,
            "request_number": request_number,
            "requesting_branch": requesting_branch,
            "requested_branch": requesting_branch,
            "total_items": total_items,
            "total_qty": total_qty,
            "categoryId": REQUEST_CATEGORY_ID,
            "sticky": True,
            "autoDismiss": False,
        },
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
        preview = build_branch_request_push_message("ExponentPushToken[preview]", group_doc, kind)
        data = preview["data"]
        title = data.get("requestNumber") or request_number
        body = f"round={data.get('round')} {data.get('branchName') or ''} items={data.get('totalItems')} qty={data.get('totalQuantity')}"
        devices = await db.mobile_devices.find(
            {
                **_ci_exact("dealer_name", dealer),
                **_ci_exact("branch", branch),
                "status": "active",
                "push_token": {"$exists": True, "$ne": ""},
            },
            {"_id": 0, "device_id": 1, "push_token": 1, "mobile_user_id": 1},
        ).to_list(100)
        devices = [dev for dev in devices if is_expo_push_token(dev.get("push_token") or "")]
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
            build_branch_request_push_message(dev.get("push_token"), group_doc, kind)
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
