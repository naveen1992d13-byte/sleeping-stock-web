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

# Fail-closed allow-list for Testing push while PUSH_TEST_MODE=true.
APPROVED_REQUEST_LIFECYCLE_TYPES = frozenset({
    "branch_request",
    "request_picked",
    "request_closed",
    "request_cycle_renotify",
    "request_requester_notify",
})


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


def _post_expo_push_http(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    """POST to Expo Push API. No environment gating — callers must authorize."""
    if not messages:
        return {"ok": True, "sent": 0}
    try:
        resp = requests.post(
            EXPO_PUSH_URL,
            json=messages,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=15,
        )
        body = resp.json() if resp.content else {}
        return {
            "ok": resp.ok,
            "status_code": resp.status_code,
            "body": body,
            "sent": len(messages) if resp.ok else 0,
        }
    except Exception as exc:
        logger.warning("Expo push send failed: %s", exc)
        return {"ok": False, "error": str(exc), "sent": 0}


def send_expo_push_messages(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not messages:
        return {"ok": True, "sent": 0}
    if _testing_push_blocked():
        logger.info("Skipping Expo push from testing environment (%s messages)", len(messages))
        return {"ok": True, "sent": 0, "skipped": True, "reason": "testing_push_blocked"}
    return _post_expo_push_http(messages)


def _is_testing_origin_device(dev: Optional[dict]) -> bool:
    return str((dev or {}).get("data_origin") or "").strip().lower() == "testing"


def _same_ci(left, right) -> bool:
    return (left or "").strip().casefold() == (right or "").strip().casefold()


def filter_request_lifecycle_devices(
    devices: List[dict],
    group_doc: Optional[dict],
    *,
    excluded_dealers: Optional[set] = None,
    require_testing_origin: bool = False,
    exclude_device_id: str = "",
    exclude_requester: bool = True,
) -> List[dict]:
    """Apply fail-closed recipient filters for request lifecycle pushes."""
    brand, _, _ = request_push_scope(group_doc)
    req_dealer = ((group_doc or {}).get("requesting_dealer") or "").strip().casefold()
    req_branch = ((group_doc or {}).get("requesting_branch") or "").strip().casefold()
    excluded = {((d or "").strip().casefold()) for d in (excluded_dealers or set()) if d}
    out: List[dict] = []
    for dev in devices or []:
        if exclude_device_id and (dev.get("device_id") or "") == exclude_device_id:
            continue
        if require_testing_origin and not _is_testing_origin_device(dev):
            continue
        if not is_eligible_request_device(dev, brand):
            continue
        if not is_expo_push_token(dev.get("push_token") or ""):
            continue
        if exclude_requester and (req_dealer or req_branch):
            if (
                (dev.get("dealer_name") or "").strip().casefold() == req_dealer
                and (dev.get("branch") or "").strip().casefold() == req_branch
            ):
                continue
        dealer_key = (dev.get("dealer_name") or "").strip().casefold()
        if dealer_key and dealer_key in excluded:
            continue
        out.append(dev)
    return out


async def clear_device_not_registered(db, *, push_token: str = "", device_id: str = "") -> int:
    """Disable push on DeviceNotRegistered tokens. Returns matched count."""
    query = {}
    if device_id:
        query["device_id"] = device_id
    elif push_token:
        query["push_token"] = push_token
    else:
        return 0
    result = await db.mobile_devices.update_many(
        query,
        {
            "$set": {
                "push_enabled": False,
                "push_token": "",
                "push_disabled_reason": "DeviceNotRegistered",
                "push_disabled_at": _now().isoformat(),
            }
        },
    )
    return int(getattr(result, "modified_count", None) or getattr(result, "matched_count", 0) or 0)


async def _cleanup_device_not_registered_from_result(
    db,
    messages: List[Dict[str, Any]],
    devices: List[dict],
    result: Dict[str, Any],
) -> int:
    """Inspect Expo ticket responses and clear dead tokens."""
    body = result.get("body") or {}
    tickets = body.get("data") if isinstance(body, dict) else None
    if not isinstance(tickets, list):
        return 0
    token_to_device = {
        (d.get("push_token") or ""): d for d in devices if d.get("push_token")
    }
    cleared = 0
    for idx, ticket in enumerate(tickets):
        if not isinstance(ticket, dict):
            continue
        if str(ticket.get("status") or "").lower() != "error":
            continue
        details = ticket.get("details") or {}
        err = str(details.get("error") or ticket.get("message") or "")
        if "DeviceNotRegistered" not in err:
            continue
        token = ""
        if idx < len(messages):
            token = (messages[idx] or {}).get("to") or ""
        device_id = (token_to_device.get(token) or {}).get("device_id") or ""
        try:
            cleared += await clear_device_not_registered(db, push_token=token, device_id=device_id)
        except Exception as exc:
            logger.warning("DeviceNotRegistered cleanup failed: %s", exc)
    return cleared


async def send_approved_request_lifecycle_push(
    db,
    messages: List[Dict[str, Any]],
    *,
    lifecycle_type: str,
    devices: List[dict],
) -> Dict[str, Any]:
    """Fail-closed chokepoint for request ringing while PUSH_TEST_MODE=true.

    Only approved lifecycle types may send. In APP_ENV=testing, recipients must
    be data_origin=testing (production/mirrored devices never receive). When
    PUSH_TEST_MODE blocks the generic path, this chokepoint may still call Expo
    for the filtered batch only.
    """
    kind = (lifecycle_type or "").strip()
    if kind not in APPROVED_REQUEST_LIFECYCLE_TYPES:
        logger.info("Blocked non-lifecycle push type=%s count=%s", kind, len(messages or []))
        return {
            "ok": True,
            "sent": 0,
            "skipped": True,
            "reason": "lifecycle_type_blocked",
            "lifecycle_type": kind,
        }
    if not messages:
        return {"ok": True, "sent": 0, "lifecycle_type": kind}

    try:
        from . import testing_runtime
    except ImportError:
        import testing_runtime

    require_testing = testing_runtime.is_testing_env()
    # Caller pre-filters brand/requester/exclusions; chokepoint enforces origin + session flags.
    if require_testing:
        filtered_devices = [
            d for d in (devices or [])
            if _is_testing_origin_device(d)
            and (d.get("status") or "") == "active"
            and d.get("session_active") is True
            and d.get("push_enabled") is True
            and is_expo_push_token(d.get("push_token") or "")
        ]
    else:
        filtered_devices = [
            d for d in (devices or [])
            if (d.get("status") or "") == "active"
            and d.get("session_active") is True
            and d.get("push_enabled") is True
            and is_expo_push_token(d.get("push_token") or "")
        ]
    token_ok = {(d.get("push_token") or "") for d in filtered_devices if d.get("push_token")}
    filtered_messages = [m for m in messages if (m.get("to") or "") in token_ok]
    # Defense in depth: ensure every message carries an approved type.
    safe_messages = []
    for msg in filtered_messages:
        data = dict(msg.get("data") or {})
        data_type = str(data.get("type") or kind)
        if data_type not in APPROVED_REQUEST_LIFECYCLE_TYPES:
            continue
        data["type"] = data_type
        cloned = dict(msg)
        cloned["data"] = data
        safe_messages.append(cloned)
    if not safe_messages:
        return {
            "ok": True,
            "sent": 0,
            "skipped": True,
            "reason": "no_eligible_testing_devices" if require_testing else "no_eligible_devices",
            "lifecycle_type": kind,
        }

    if require_testing and _testing_push_blocked():
        # Approved bypass: Testing may ring only filtered testing-origin devices.
        result = _post_expo_push_http(safe_messages)
        result["lifecycle_bypass"] = True
    else:
        result = send_expo_push_messages(safe_messages)

    try:
        cleared = await _cleanup_device_not_registered_from_result(
            db, safe_messages, filtered_devices, result,
        )
        if cleared:
            result["cleared_tokens"] = cleared
    except Exception as exc:
        logger.warning("post-send DeviceNotRegistered sweep failed: %s", exc)
    result["lifecycle_type"] = kind
    result["sent"] = 0 if result.get("skipped") else len(safe_messages)
    return result


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


async def _load_excluded_dealers(db, request_group_key: str) -> set:
    try:
        from mobile_request_cycle import excluded_dealer_keys
        return await excluded_dealer_keys(db, request_group_key)
    except Exception:
        try:
            from mobile_request_claim import dismissed_dealer_keys
            return await dismissed_dealer_keys(db, request_group_key)
        except Exception:
            return set()


async def _resolve_lifecycle_devices(
    db,
    group_doc: dict,
    *,
    exclude_device_id: str = "",
    exclude_requester: bool = True,
) -> List[dict]:
    brand, _, _ = request_push_scope(group_doc)
    if not brand:
        return []
    request_group_key = (group_doc or {}).get("id") or (group_doc or {}).get("request_number") or ""
    devices = await db.mobile_devices.find(
        eligible_request_device_query(brand),
        {
            "_id": 0,
            "device_id": 1,
            "push_token": 1,
            "mobile_user_id": 1,
            "brand_name": 1,
            "dealer_name": 1,
            "branch": 1,
            "session_active": 1,
            "push_enabled": 1,
            "status": 1,
            "data_origin": 1,
        },
    ).to_list(500)
    excluded = await _load_excluded_dealers(db, request_group_key)
    try:
        from . import testing_runtime
    except ImportError:
        import testing_runtime
    return filter_request_lifecycle_devices(
        devices,
        group_doc,
        excluded_dealers=excluded,
        require_testing_origin=testing_runtime.is_testing_env(),
        exclude_device_id=exclude_device_id,
        exclude_requester=exclude_requester,
    )


async def notify_branch_request_push(db, group_doc: dict, kind: str = "new"):
    """High-priority request push to ALL eligible dealers under the brand.

    Reminder rounds are disabled for the Pick/Reject flow — only kind=new
    (and explicit reopen) deliver. Never raises. Uses the approved lifecycle
    chokepoint so Testing can ring with PUSH_TEST_MODE=true.
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
        data["actions"] = "pick_reject"
        data["can_snooze"] = False
        try:
            from mobile_request_claim import CONFIRM_WINDOW_SECONDS as _confirm_secs
        except ImportError:
            _confirm_secs = 50 * 60
        data["confirm_window_seconds"] = int(_confirm_secs)
        title = data.get("requestNumber") or display_number
        body = f"round={data.get('round')} {data.get('branchName') or ''} items={data.get('totalItems')} qty={data.get('totalQuantity')}"

        # Persist ring cycle before broadcast so restarts keep the TTL.
        ring_deadline = ""
        cycle_number = 1
        try:
            from mobile_request_cycle import RING_TTL_SECONDS, get_cycle, start_or_reset_cycle
            existing = await get_cycle(db, request_group_key)
            if not existing:
                cycle = await start_or_reset_cycle(
                    db, {**(group_doc or {}), "id": request_group_key}, cycle_number=1,
                )
            elif str(existing.get("status") or "") != "ringing":
                # Release / reopen after pick — resume ringing, keep exclusions.
                cycle = await start_or_reset_cycle(
                    db,
                    {**(group_doc or {}), "id": request_group_key},
                    cycle_number=int(existing.get("cycle_number") or 1),
                )
            else:
                cycle = existing
            ring_deadline = str((cycle or {}).get("ring_deadline_at") or "")
            cycle_number = int((cycle or {}).get("cycle_number") or 1)
            data["ring_deadline_at"] = ring_deadline
            data["ring_ttl_seconds"] = str(RING_TTL_SECONDS)
            data["cycle_number"] = str(cycle_number)
        except Exception as exc:
            logger.warning("start request cycle failed: %s", exc)
            data["ring_ttl_seconds"] = "50"

        devices = await _resolve_lifecycle_devices(db, group_doc)
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
        for msg in batch:
            msg.setdefault("data", {})
            msg["data"]["actions"] = "pick_reject"
            msg["data"]["can_snooze"] = False
            msg["data"]["confirm_window_seconds"] = str(int(_confirm_secs))
            msg["data"]["type"] = "branch_request"
            if ring_deadline:
                msg["data"]["ring_deadline_at"] = ring_deadline
            msg["data"]["ring_ttl_seconds"] = data.get("ring_ttl_seconds") or "50"
            msg["data"]["cycle_number"] = str(cycle_number)
        result = await send_approved_request_lifecycle_push(
            db, batch, lifecycle_type="branch_request", devices=devices,
        )
        status = "sent" if result.get("ok") and not result.get("skipped") else (
            "skipped" if result.get("skipped") else "failed"
        )
        detail = str(result.get("reason") or result.get("error") or result.get("body") or "")[:500]
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
        return {
            "ok": result.get("ok"),
            "sent": result.get("sent", 0),
            "kind": "new",
            "broadcast": "brand_dealers",
            "skipped": result.get("skipped"),
            "reason": result.get("reason"),
        }
    except Exception as exc:
        logger.warning("branch request push failed: %s", exc)
        return {"ok": False, "error": str(exc)[:300]}


def build_request_cycle_renotify_message(token: str, group_doc: dict) -> dict:
    msg = build_branch_request_push_message(token, group_doc, "new")
    msg.setdefault("data", {})
    msg["data"]["type"] = "request_cycle_renotify"
    msg["data"]["actions"] = "pick_reject"
    msg["data"]["can_snooze"] = False
    try:
        from mobile_request_claim import CONFIRM_WINDOW_SECONDS as _confirm_secs
    except ImportError:
        _confirm_secs = 50 * 60
    msg["data"]["confirm_window_seconds"] = str(int(_confirm_secs))
    return msg


async def notify_request_cycle_renotify_push(db, group_doc: dict, *, exclude_device_id: str = ""):
    """Re-ring remaining dealers after ring TTL / confirm miss (lifecycle type)."""
    try:
        devices = await _resolve_lifecycle_devices(
            db, group_doc, exclude_device_id=exclude_device_id,
        )
        if not devices:
            return {"ok": True, "sent": 0, "skipped": True}
        batch = [
            build_request_cycle_renotify_message(dev.get("push_token"), group_doc)
            for dev in devices if dev.get("push_token")
        ]
        return await send_approved_request_lifecycle_push(
            db, batch, lifecycle_type="request_cycle_renotify", devices=devices,
        )
    except Exception as exc:
        logger.warning("request_cycle_renotify push failed: %s", exc)
        return {"ok": False, "error": str(exc)[:300]}


async def renotify_after_confirm_timeout(db, group_doc: dict, *, exclude_device_id: str = ""):
    """Re-open the request for remaining eligible dealers after a 50s confirm miss."""
    try:
        result = await notify_request_cycle_renotify_push(
            db, group_doc, exclude_device_id=exclude_device_id,
        )
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


def build_request_closed_push_message(token: str, group_doc: dict, *, reason: str = "picked") -> dict:
    request_number = (group_doc or {}).get("request_number") or ""
    request_group_key = (group_doc or {}).get("id") or request_number
    return {
        "to": token,
        "priority": "high",
        "_contentAvailable": True,
        "data": {
            "type": "request_closed",
            "requestId": request_group_key,
            "request_group_key": request_group_key,
            "request_number": request_number,
            "reason": reason or "picked",
        },
    }


def build_requester_notify_push_message(
    token: str,
    group_doc: dict,
    *,
    outcome: str,
    detail: str = "",
) -> dict:
    request_number = (group_doc or {}).get("request_number") or ""
    request_group_key = (group_doc or {}).get("id") or request_number
    return {
        "to": token,
        "priority": "high",
        "_contentAvailable": True,
        "data": {
            "type": "request_requester_notify",
            "requestId": request_group_key,
            "request_group_key": request_group_key,
            "request_number": request_number,
            "outcome": outcome or "",
            "detail": detail or "",
            "screen": "request",
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
        {
            "_id": 0,
            "device_id": 1,
            "push_token": 1,
            "mobile_user_id": 1,
            "brand_name": 1,
            "dealer_name": 1,
            "branch": 1,
            "session_active": 1,
            "push_enabled": 1,
            "status": 1,
            "data_origin": 1,
        },
    ).to_list(100)
    return [
        dev for dev in devices
        if is_eligible_request_device(dev, brand, dealer, branch)
    ]


async def notify_request_picked_push(db, group_doc: dict, *, picked_by_name: str, exclude_device_id: str):
    """Stop ringing on other devices: request_picked (+ request_closed). Never raises."""
    try:
        brand, _, _ = request_push_scope(group_doc)
        if not brand:
            return {"ok": False, "error": "missing_scope"}
        request_group_key = (group_doc or {}).get("id") or (group_doc or {}).get("request_number") or ""
        try:
            from mobile_request_cycle import mark_cycle_picked
            await mark_cycle_picked(
                db,
                request_group_key,
                dealer_name=(group_doc or {}).get("supplying_dealer") or "",
            )
        except Exception as exc:
            logger.warning("mark_cycle_picked failed: %s", exc)

        devices = await _resolve_lifecycle_devices(
            db, group_doc, exclude_device_id=exclude_device_id, exclude_requester=True,
        )
        # Also clear other devices on the picker's own branch (legacy same-branch path).
        dealer = (group_doc or {}).get("supplying_dealer") or ""
        branch = (group_doc or {}).get("supplying_branch") or ""
        if dealer and branch:
            same_branch = [
                d for d in await _active_branch_devices(db, brand, dealer, branch)
                if d.get("device_id") != exclude_device_id
            ]
            seen = {d.get("device_id") for d in devices}
            for d in same_branch:
                if d.get("device_id") not in seen:
                    devices.append(d)
                    seen.add(d.get("device_id"))

        if not devices:
            return {"ok": True, "sent": 0, "skipped": True}
        picked_batch = [
            build_request_picked_push_message(dev.get("push_token"), group_doc, picked_by_name)
            for dev in devices if dev.get("push_token")
        ]
        closed_batch = [
            build_request_closed_push_message(dev.get("push_token"), group_doc, reason="picked")
            for dev in devices if dev.get("push_token")
        ]
        picked_result = await send_approved_request_lifecycle_push(
            db, picked_batch, lifecycle_type="request_picked", devices=devices,
        )
        closed_result = await send_approved_request_lifecycle_push(
            db, closed_batch, lifecycle_type="request_closed", devices=devices,
        )
        sent = int(picked_result.get("sent") or 0) + int(closed_result.get("sent") or 0)
        return {
            "ok": bool(picked_result.get("ok")) and bool(closed_result.get("ok")),
            "sent": sent,
            "picked": picked_result,
            "closed": closed_result,
        }
    except Exception as exc:
        logger.warning("request_picked push failed: %s", exc)
        return {"ok": False, "error": str(exc)[:300]}


async def notify_request_closed_push(
    db,
    group_doc: dict,
    *,
    reason: str = "closed",
    exclude_device_id: str = "",
):
    """Tell remaining devices to stop ringing for this request. Never raises."""
    try:
        devices = await _resolve_lifecycle_devices(
            db, group_doc, exclude_device_id=exclude_device_id,
        )
        if not devices:
            return {"ok": True, "sent": 0, "skipped": True}
        batch = [
            build_request_closed_push_message(dev.get("push_token"), group_doc, reason=reason)
            for dev in devices if dev.get("push_token")
        ]
        return await send_approved_request_lifecycle_push(
            db, batch, lifecycle_type="request_closed", devices=devices,
        )
    except Exception as exc:
        logger.warning("request_closed push failed: %s", exc)
        return {"ok": False, "error": str(exc)[:300]}


async def notify_requester_request_outcome_push(
    db,
    group_doc: dict,
    *,
    outcome: str,
    detail: str = "",
):
    """Notify the requesting dealer/branch (completion / all-rejected / max cycles)."""
    try:
        brand = (
            (group_doc or {}).get("requesting_brand")
            or (group_doc or {}).get("supplying_brand")
            or (group_doc or {}).get("brand_name")
            or ""
        ).strip()
        req_dealer = ((group_doc or {}).get("requesting_dealer") or "").strip()
        req_branch = ((group_doc or {}).get("requesting_branch") or "").strip()
        if not brand or not req_dealer or not req_branch:
            return {"ok": False, "error": "missing_requester_scope"}
        devices = await db.mobile_devices.find(
            eligible_request_device_query(brand, req_dealer, req_branch),
            {
                "_id": 0,
                "device_id": 1,
                "push_token": 1,
                "mobile_user_id": 1,
                "brand_name": 1,
                "dealer_name": 1,
                "branch": 1,
                "session_active": 1,
                "push_enabled": 1,
                "status": 1,
                "data_origin": 1,
            },
        ).to_list(100)
        try:
            from . import testing_runtime
        except ImportError:
            import testing_runtime
        devices = filter_request_lifecycle_devices(
            devices,
            group_doc,
            require_testing_origin=testing_runtime.is_testing_env(),
            exclude_requester=False,
        )
        # Keep only the requesting dealer+branch.
        devices = [
            d for d in devices
            if _same_ci(d.get("dealer_name"), req_dealer) and _same_ci(d.get("branch"), req_branch)
        ]
        if not devices:
            return {"ok": True, "sent": 0, "skipped": True, "reason": "no_requester_devices"}
        batch = [
            build_requester_notify_push_message(
                dev.get("push_token"), group_doc, outcome=outcome, detail=detail,
            )
            for dev in devices if dev.get("push_token")
        ]
        return await send_approved_request_lifecycle_push(
            db, batch, lifecycle_type="request_requester_notify", devices=devices,
        )
    except Exception as exc:
        logger.warning("requester outcome push failed: %s", exc)
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
