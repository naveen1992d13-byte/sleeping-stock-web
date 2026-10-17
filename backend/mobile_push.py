"""Expo push notifications for Sleeping Stock Mobile (best-effort, non-blocking)."""
from __future__ import annotations

import logging
import os
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


def request_push_scope(group_doc: Optional[dict]) -> tuple:
    """Brand + Dealer + Branch for request push. All three are required."""
    doc = group_doc or {}
    brand = (doc.get("supplying_brand") or doc.get("requesting_brand") or doc.get("brand_name") or "").strip()
    dealer = (doc.get("supplying_dealer") or doc.get("dealer_name") or "").strip()
    branch = (doc.get("supplying_branch") or doc.get("branch") or "").strip()
    return brand, dealer, branch


def eligible_request_device_query(brand: str, dealer: str = "", branch: str = "") -> dict:
    """Logged-in, push-enabled devices.

    New request alerts broadcast to ALL dealers under the Brand (simultaneous
    Pick race). When dealer/branch are provided they further narrow the query
    for legacy same-branch helpers (transfer targets, etc.).
    """
    q = {
        **_ci_exact("brand_name", brand),
        "status": "active",
        "session_active": True,
        "push_enabled": True,
        "push_token": {"$exists": True, "$ne": ""},
    }
    if dealer:
        q.update(_ci_exact("dealer_name", dealer))
    if branch:
        q.update(_ci_exact("branch", branch))
    return q


def is_eligible_request_device(dev: Optional[dict], brand: str, dealer: str = "", branch: str = "") -> bool:
    if not dev:
        return False
    if (dev.get("status") or "") != "active":
        return False
    if dev.get("session_active") is not True:
        return False
    if dev.get("push_enabled") is not True:
        return False
    if not is_expo_push_token(dev.get("push_token") or ""):
        return False

    def _same(left, right):
        return (left or "").strip().casefold() == (right or "").strip().casefold()

    if not _same(dev.get("brand_name"), brand):
        return False
    if dealer and not _same(dev.get("dealer_name"), dealer):
        return False
    if branch and not _same(dev.get("branch"), branch):
        return False
    return True


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


def _testing_push_blocked() -> bool:
    """Never deliver live Expo/FCM to production devices from Testing.

    Default-on in APP_ENV=testing. Set PUSH_TEST_MODE=false only when an
    approved test device allow-list is in use.
    """
    try:
        from . import testing_runtime
    except ImportError:
        import testing_runtime
    if not testing_runtime.is_testing_env():
        return False
    flag = str(os.getenv("PUSH_TEST_MODE", "true") or "true").strip().lower()
    return flag not in {"0", "false", "no", "off"}


def send_expo_push_messages(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not messages:
        return {"ok": True, "sent": 0}
    if _testing_push_blocked():
        logger.info("Skipping Expo push from testing environment (%s messages)", len(messages))
        return {"ok": True, "sent": 0, "skipped": True, "reason": "testing_push_blocked"}
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

    Pre-acceptance attempts may have only attempt_id (no official RQ). The
    display field uses official request_number when present, else attempt_id.
    """
    request_number = (group_doc or {}).get("request_number") or ""
    attempt_id = (group_doc or {}).get("attempt_id") or ""
    display_number = request_number or attempt_id
    requesting_branch = (group_doc or {}).get("requesting_branch") or ""
    total_items = (group_doc or {}).get("total_items")
    total_qty = (group_doc or {}).get("total_qty")
    if total_items in (None, ""):
        total_items = len((group_doc or {}).get("items") or [])
    if total_qty in (None, ""):
        total_qty = (group_doc or {}).get("total_quantity") or 0
    request_group_key = (group_doc or {}).get("id") or request_number or attempt_id
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
            "requestNumber": display_number,
            "attemptId": attempt_id,
            "branchName": requesting_branch,
            "totalItems": total_items,
            "totalQuantity": total_qty,
            "request_group_key": request_group_key,
            "request_number": request_number,
            "attempt_id": attempt_id,
            "requesting_branch": requesting_branch,
            "requested_branch": requesting_branch,
            "total_items": total_items,
            "total_qty": total_qty,
            "categoryId": REQUEST_CATEGORY_ID,
            "sticky": True,
            "autoDismiss": False,
            "official": bool(request_number),
        },
    }


async def notify_branch_request_push(db, group_doc: dict, kind: str = "new"):
    """High-priority request push to ALL eligible dealers under the brand.

    Reminder rounds are disabled for the Pick/Reject flow — only kind=new
    (and explicit reopen) deliver. Never raises.
    """
    try:
        if str(kind or "new").startswith("reminder"):
            return {"ok": True, "sent": 0, "skipped": True, "reason": "reminders_removed"}
        brand, dealer, branch = request_push_scope(group_doc)
        request_number = (group_doc or {}).get("request_number") or ""
        attempt_id = (group_doc or {}).get("attempt_id") or ""
        display_number = request_number or attempt_id
        request_group_key = (group_doc or {}).get("id") or request_number or attempt_id
        if not brand or not (request_number or attempt_id or request_group_key):
            return {"ok": False, "error": "missing_scope"}
        preview = build_branch_request_push_message("ExponentPushToken[preview]", group_doc, "new")
        data = preview["data"]
        # Force Pick/Reject actions only (no Snooze) for clients that read this flag.
        data["actions"] = "pick_reject"
        data["can_snooze"] = False
        data["confirm_window_seconds"] = 50
        title = data.get("requestNumber") or display_number
        body = f"round={data.get('round')} {data.get('branchName') or ''} items={data.get('totalItems')} qty={data.get('totalQuantity')}"
        # Broadcast to every active device on this Brand (all dealers).
        devices = await db.mobile_devices.find(
            eligible_request_device_query(brand),
            {"_id": 0, "device_id": 1, "push_token": 1, "mobile_user_id": 1, "brand_name": 1, "dealer_name": 1, "branch": 1, "session_active": 1, "push_enabled": 1, "status": 1},
        ).to_list(500)
        devices = [dev for dev in devices if is_eligible_request_device(dev, brand)]
        # Exclude the requesting branch so they are not alerted to fulfil their own request.
        req_dealer = ((group_doc or {}).get("requesting_dealer") or "").strip().casefold()
        req_branch = ((group_doc or {}).get("requesting_branch") or "").strip().casefold()
        if req_dealer or req_branch:
            devices = [
                d for d in devices
                if not (
                    (d.get("dealer_name") or "").strip().casefold() == req_dealer
                    and (d.get("branch") or "").strip().casefold() == req_branch
                )
            ]
        # Exclude dealers that already Rejected this request.
        try:
            from mobile_request_claim import dismissed_dealer_keys
            dismissed = await dismissed_dealer_keys(db, request_group_key)
        except Exception:
            dismissed = set()
        if dismissed:
            devices = [
                d for d in devices
                if (d.get("dealer_name") or "").strip().casefold() not in dismissed
            ]
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
            build_branch_request_push_message(dev.get("push_token"), group_doc, "new")
            for dev in devices
            if dev.get("push_token")
        ]
        # Stamp Pick/Reject-only flags onto each message.
        for msg in batch:
            msg.setdefault("data", {})
            msg["data"]["actions"] = "pick_reject"
            msg["data"]["can_snooze"] = False
            msg["data"]["confirm_window_seconds"] = "50"
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
        return {"ok": result.get("ok"), "sent": len(batch), "kind": "new", "broadcast": "brand_dealers"}
    except Exception as exc:
        logger.warning("branch request push failed: %s", exc)
        return {"ok": False, "error": str(exc)[:300]}


async def renotify_after_confirm_timeout(db, group_doc: dict, *, exclude_device_id: str = ""):
    """Re-open the request for remaining eligible dealers after a 50s confirm miss."""
    try:
        result = await notify_branch_request_push(db, group_doc, kind="new")
        if exclude_device_id:
            logger.info(
                "renotify after confirm timeout exclude=%s sent=%s",
                exclude_device_id,
                result.get("sent"),
            )
        return result
    except Exception as exc:
        logger.warning("renotify_after_confirm_timeout failed: %s", exc)
        return {"ok": False, "error": str(exc)[:300]}


def build_request_picked_push_message(token: str, group_doc: dict, picked_by_name: str) -> dict:
    request_number = (group_doc or {}).get("request_number") or ""
    request_group_key = (group_doc or {}).get("id") or request_number
    return {
        "to": token,
        "priority": "high",
        "_contentAvailable": True,
        "data": {
            "type": "request_picked",
            "requestId": request_group_key,
            "request_group_key": request_group_key,
            "request_number": request_number,
            "picked_by_name": picked_by_name or "",
        },
    }


def build_request_transferred_push_message(token: str, group_doc: dict) -> dict:
    request_number = (group_doc or {}).get("request_number") or ""
    requesting_branch = (group_doc or {}).get("requesting_branch") or ""
    total_items = (group_doc or {}).get("total_items")
    total_qty = (group_doc or {}).get("total_qty") or (group_doc or {}).get("total_quantity") or 0
    if total_items in (None, ""):
        total_items = len((group_doc or {}).get("items") or [])
    request_group_key = (group_doc or {}).get("id") or request_number
    return {
        "to": token,
        "priority": "high",
        "_contentAvailable": True,
        "data": {
            "type": "request_transferred",
            "screen": "request",
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
            "actions": "none",
        },
    }


async def _active_branch_devices(db, brand: str, dealer: str, branch: str):
    devices = await db.mobile_devices.find(
        eligible_request_device_query(brand, dealer, branch),
        {"_id": 0, "device_id": 1, "push_token": 1, "mobile_user_id": 1, "brand_name": 1, "dealer_name": 1, "branch": 1, "session_active": 1, "push_enabled": 1, "status": 1},
    ).to_list(100)
    return [
        dev for dev in devices
        if is_eligible_request_device(dev, brand, dealer, branch)
    ]


async def notify_request_picked_push(db, group_doc: dict, *, picked_by_name: str, exclude_device_id: str):
    """Data-only high-priority push to other same-branch devices. Never raises."""
    try:
        brand, dealer, branch = request_push_scope(group_doc)
        if not brand or not dealer or not branch:
            return {"ok": False, "error": "missing_scope"}
        devices = [
            dev for dev in await _active_branch_devices(db, brand, dealer, branch)
            if dev.get("device_id") != exclude_device_id
        ]
        batch = [
            build_request_picked_push_message(dev.get("push_token"), group_doc, picked_by_name)
            for dev in devices if dev.get("push_token")
        ]
        if not batch:
            return {"ok": True, "sent": 0, "skipped": True}
        result = send_expo_push_messages(batch)
        return {"ok": result.get("ok"), "sent": len(batch)}
    except Exception as exc:
        logger.warning("request_picked push failed: %s", exc)
        return {"ok": False, "error": str(exc)[:300]}


BRANCH_TRANSFERRED_MESSAGE = "Your branch has been transferred. Please re-pair your device to continue."


def build_branch_transferred_push_message(
    token: str,
    *,
    from_branch: str = "",
    to_branch: str = "",
    mobile_user_id: str = "",
) -> dict:
    return {
        "to": token,
        "title": "Branch transferred",
        "body": BRANCH_TRANSFERRED_MESSAGE,
        "sound": "default",
        "priority": "high",
        "_contentAvailable": True,
        "data": {
            "type": "branch_transferred",
            "code": "BRANCH_TRANSFERRED",
            "re_pair_required": True,
            "from_branch": from_branch or "",
            "to_branch": to_branch or "",
            "mobile_user_id": mobile_user_id or "",
            "screen": "pair",
        },
    }


async def notify_branch_transferred(db, *, mobile_user_id: str, from_branch: str = "", to_branch: str = ""):
    """Best-effort data/push event after Admin Transfer Branch. Never raises.

    Testing still blocks live Expo/FCM so a Testing transfer cannot notify a
    production-installed device that shares the same Expo token.
    """
    title = "Branch transferred"
    body = BRANCH_TRANSFERRED_MESSAGE
    data = build_branch_transferred_push_message(
        "ExponentPushToken[preview]",
        from_branch=from_branch,
        to_branch=to_branch,
        mobile_user_id=mobile_user_id,
    )["data"]
    try:
        devices = await db.mobile_devices.find(
            {"mobile_user_id": mobile_user_id, "push_token": {"$exists": True, "$ne": ""}},
            {"_id": 0, "device_id": 1, "push_token": 1, "mobile_user_id": 1},
        ).to_list(50)
        devices = [dev for dev in devices if is_expo_push_token(dev.get("push_token") or "")]
        if not devices:
            await log_push_attempt(
                db,
                mobile_user_id=mobile_user_id or "",
                device_id="",
                push_token="",
                title=title,
                body=body,
                data=data,
                status="skipped",
                detail="no_push_token",
            )
            return {"ok": True, "sent": 0, "skipped": True}
        batch = [
            build_branch_transferred_push_message(
                dev.get("push_token"),
                from_branch=from_branch,
                to_branch=to_branch,
                mobile_user_id=mobile_user_id,
            )
            for dev in devices
            if dev.get("push_token")
        ]
        result = send_expo_push_messages(batch)
        status = "sent" if result.get("ok") and not result.get("skipped") else (
            "skipped" if result.get("skipped") else "failed"
        )
        detail = str(result.get("reason") or result.get("error") or result.get("body") or "")[:500]
        for dev in devices:
            await log_push_attempt(
                db,
                mobile_user_id=mobile_user_id or "",
                device_id=dev.get("device_id", ""),
                push_token=dev.get("push_token", ""),
                title=title,
                body=body,
                data=data,
                status=status,
                detail=detail,
            )
        return {"ok": result.get("ok"), "sent": 0 if result.get("skipped") else len(batch), "skipped": result.get("skipped")}
    except Exception as exc:
        logger.warning("branch_transferred push failed: %s", exc)
        return {"ok": False, "error": str(exc)[:300]}


async def notify_request_transferred_push(db, group_doc: dict, *, target_device_id: str):
    """Ringing-style push to the new owner only. Never raises."""
    try:
        brand, dealer, branch = request_push_scope(group_doc)
        if not brand or not dealer or not branch:
            return {"ok": False, "error": "missing_scope"}
        devices = [
            dev for dev in await _active_branch_devices(db, brand, dealer, branch)
            if dev.get("device_id") == target_device_id
        ]
        batch = [
            build_request_transferred_push_message(dev.get("push_token"), group_doc)
            for dev in devices if dev.get("push_token")
        ]
        if not batch:
            return {"ok": True, "sent": 0, "skipped": True}
        result = send_expo_push_messages(batch)
        return {"ok": result.get("ok"), "sent": len(batch)}
    except Exception as exc:
        logger.warning("request_transferred push failed: %s", exc)
        return {"ok": False, "error": str(exc)[:300]}
