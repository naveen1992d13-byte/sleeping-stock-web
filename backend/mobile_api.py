"""
Sleeping Stock Mobile — backend API module.

Adds mobile-companion-app support (pairing, device sessions, branch-scoped
notifications, stock verification, stock search, app versioning) on top of
the existing NMTS backend, without touching any existing route.

Mount pattern matches reports_center.py:
    import mobile_api
    mobile_api.init_mobile_api(db, get_current_user, UserResponse, pwd_context)
    api_router.include_router(mobile_api.router)

Security model (Part 9 / Part 21 of spec):
- A mobile device session is bound at pairing time to exactly one
  Brand + Dealer + Branch. Every mobile-facing endpoint resolves scope
  from the authenticated device session ONLY — never from client input.
- Pairing codes are one-time-use, expire in 10 minutes, and are consumed
  atomically (findOneAndUpdate) to prevent race-condition double-use.
- Passwords and session tokens are stored only as bcrypt/SHA-256 hashes.
"""
import os
import re
import uuid
import random
import string
import hashlib
import logging
import json
import base64
from io import BytesIO
import qrcode
from datetime import datetime, timezone, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status, Header, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, ConfigDict
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

logger = logging.getLogger("nmts.mobile")

router = APIRouter(prefix="/mobile", tags=["Sleeping Stock Mobile"])

# Populated by init_mobile_api()
db = None
get_current_user = None
UserResponse = None
pwd_context = None
request_center_transition = None
notify_request_status_change = None
_mobile_web_security = HTTPBearer()


async def _web_current_user(credentials: HTTPAuthorizationCredentials = Depends(_mobile_web_security)):
    """Resolve the website user after init_mobile_api() binds server.py auth.

    Route decorators are evaluated while this module is imported, so they must
    depend on this stable callable instead of the initially-None global
    get_current_user variable.
    """
    if get_current_user is None:
        raise HTTPException(status_code=500, detail="Mobile API authentication is not initialized")
    return await get_current_user(credentials)


PAIRING_CODE_TTL_MINUTES = 10
DEFAULT_NOTIFICATION_INTERVAL_MINUTES = 30
SKIP_LIMIT = 2  # first two alerts may be skipped; third cannot


def init_mobile_api(_db, _get_current_user, _UserResponse, _pwd_context, _request_center_transition=None, _notify_request_status_change=None):
    global db, get_current_user, UserResponse, pwd_context, request_center_transition, notify_request_status_change
    db = _db
    get_current_user = _get_current_user
    UserResponse = _UserResponse
    pwd_context = _pwd_context
    request_center_transition = _request_center_transition
    notify_request_status_change = _notify_request_status_change


class _MobileActingUser:
    """Lets a mobile action flow through server.py's existing
    _request_center_transition()/_notify_request_status_change() exactly as
    a web Admin approving/rejecting would — same validation, same
    stock_reservations/order_activity bookkeeping, same notification
    dispatch. `role='admin'` + `group=dealer_name` is what makes
    `_request_center_transition`'s `is_supplier` check pass, which is
    correct here: every request this mobile user can see is already
    filtered to their paired supplying Dealer/Branch."""

    def __init__(self, mobile_user_id, name, dealer_name, brand_name):
        self.id = f"mobile:{mobile_user_id}"
        self.username = f"{name} (Mobile)"
        self.role = "admin"
        self.group = dealer_name
        self.brand = brand_name
        self.email = ""
        self.phone = ""


# ==================== HELPERS ====================

def _now():
    return datetime.now(timezone.utc)


def _now_iso():
    return _now().isoformat()


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _new_raw_token() -> str:
    return uuid.uuid4().hex + uuid.uuid4().hex


def _branch_code(branch_name: str) -> str:
    """Normalize a branch name into a 3-letter code for the Mobile User ID."""
    cleaned = re.sub(r"[^A-Za-z]", "", branch_name or "")
    code = cleaned[:3].upper()
    return code.ljust(3, "X") if code else "GEN"


async def generate_mobile_user_id(branch_name: str) -> str:
    """
    Format: MU{BRANCH_CODE}{YYMMDD}{4-digit daily serial}
    Uses an atomic counter document (findOneAndUpdate $inc) keyed by
    branch+date so concurrent creations never collide, per Part 6.
    """
    code = _branch_code(branch_name)
    date_key = _now().strftime("%y%m%d")
    counter_key = f"mobile_user:{code}:{date_key}"
    counter = await db.counters.find_one_and_update(
        {"_id": counter_key},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    seq = counter["seq"]
    if seq > 9999:
        # Extremely unlikely (9999 mobile users onboarded in one branch in
        # one day) but guard against overflow rather than truncate silently.
        raise HTTPException(500, "Daily mobile user serial exhausted for this branch")
    return f"MU{code}{date_key}{seq:04d}"


def _generate_manual_code() -> str:
    return "".join(random.choices(string.digits, k=6))




def _public_api_base_url(request: Request) -> str:
    """Return the externally reachable API base URL embedded in pairing QR codes.

    PUBLIC_API_BASE_URL is preferred for production. When it is not set, the
    current request origin is used, which works with HTTPS Codespaces/preview
    URLs without rebuilding the mobile app.
    """
    configured = (os.getenv("PUBLIC_API_BASE_URL") or "").strip().rstrip("/")
    if configured:
        if not configured.lower().startswith("https://"):
            raise HTTPException(500, "PUBLIC_API_BASE_URL must use HTTPS")
        return configured

    forwarded_proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "https").split(",")[0].strip()
    forwarded_host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(",")[0].strip()
    if not forwarded_host:
        raise HTTPException(500, "Unable to determine the public NMTS server URL")
    if forwarded_proto != "https":
        raise HTTPException(500, "Pairing QR requires an HTTPS public backend URL")
    return f"https://{forwarded_host}/api".rstrip("/")

def _require_scope_selected(brand, dealer, branch):
    if not (brand and dealer and branch):
        raise HTTPException(400, "Brand, Dealer and Branch must all be selected before generating a pairing code")


def _scoped_query_for_user(current_user, brand=None, dealer=None, branch=None) -> dict:
    """
    Mirrors the existing NMTS role-scoping pattern (master: full access,
    admin: fixed brand/dealer + optional branch, user: fixed branch).
    """
    q = {}
    if current_user.role == "master":
        if brand:
            q["brand_name"] = brand
        if dealer:
            q["dealer_name"] = dealer
        if branch:
            q["branch"] = branch
    elif current_user.role == "admin":
        q["brand_name"] = current_user.brand
        q["dealer_name"] = current_user.group
        if branch:
            q["branch"] = branch
    else:  # user
        q["brand_name"] = current_user.brand
        q["dealer_name"] = current_user.group
        q["branch"] = current_user.location
    return q


# ==================== MODELS ====================

class MobileUserCreate(BaseModel):
    name: str
    mobile_number: str
    brand_name: Optional[str] = None
    dealer_name: Optional[str] = None
    branch: Optional[str] = None


class MobileUserResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    mobile_user_id: str
    name: str
    mobile_number: str
    brand_name: str
    dealer_name: str
    branch: str
    status: str
    created_by_user_id: str
    created_by_name: str
    created_by_role: str
    created_at: str
    updated_at: Optional[str] = None
    paired_device_count: int = 0
    active_device_count: int = 0
    last_active_at: Optional[str] = None


class PairingGenerateRequest(BaseModel):
    mobile_user_id: str
    branch: Optional[str] = None  # required for admin; ignored (forced) for user role


class PairingVerifyRequest(BaseModel):
    mobile_user_id: str
    pairing_code: str
    pairing_token: Optional[str] = None
    device_user_name: str
    device_user_mobile: str
    device_name: str
    device_info: Optional[str] = None
    app_version: Optional[str] = None
    push_token: Optional[str] = None


class DeviceStatusUpdate(BaseModel):
    status: str  # active, inactive, removed


class NotificationActionRequest(BaseModel):
    request_group_key: str


class PartResponseItem(BaseModel):
    order_request_id: str
    part_number: str
    accepted_qty: float
    remark: Optional[str] = None


class RequestPartResponse(BaseModel):
    request_group_key: str
    parts: List[PartResponseItem]


class StockVerificationSubmit(BaseModel):
    part_number: str
    physical_qty: float
    location: Optional[str] = None
    remark: Optional[str] = None
    entry_method: str  # MANUAL or CAMERA_OCR
    client_id: Optional[str] = None  # offline-queue idempotency key (Part 22)


class AppVersionUpsert(BaseModel):
    version_name: str
    version_code: int
    apk_filename: str
    apk_path: str
    release_notes: Optional[str] = ""
    min_supported_version_code: int = 1
    mandatory: bool = False


class NotificationIntervalUpdate(BaseModel):
    interval_minutes: int


# ==================== DEVICE SESSION AUTH ====================

async def get_device_session(authorization: Optional[str] = Header(None)):
    """
    Resolves a mobile device session from 'Authorization: Bearer <token>'.
    Scope (brand/dealer/branch) is read from the stored session — the
    mobile client's own claims about its branch are never trusted.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Missing device session token")
    raw_token = authorization.split(" ", 1)[1].strip()
    token_hash = _hash_token(raw_token)

    session = await db.mobile_sessions.find_one({"session_token_hash": token_hash}, {"_id": 0})
    if not session:
        raise HTTPException(401, "Invalid or expired device session")

    device = await db.mobile_devices.find_one({"device_id": session["device_id"]}, {"_id": 0})
    if not device or device.get("status") != "active":
        raise HTTPException(403, "Device is inactive or removed — please re-pair")

    mobile_user = await db.mobile_users.find_one({"mobile_user_id": device["mobile_user_id"]}, {"_id": 0})
    if not mobile_user or mobile_user.get("status") != "active":
        raise HTTPException(403, "Mobile user is inactive")

    await db.mobile_devices.update_one(
        {"device_id": device["device_id"]}, {"$set": {"last_active_at": _now_iso()}}
    )
    await db.mobile_users.update_one(
        {"mobile_user_id": mobile_user["mobile_user_id"]}, {"$set": {"last_active_at": _now_iso()}}
    )

    return {
        "device": device,
        "mobile_user": mobile_user,
        "brand_name": device["brand_name"],
        "dealer_name": device["dealer_name"],
        "branch": device["branch"],
    }


# ==================== MOBILE USER MANAGEMENT (WEB SIDE) ====================

@router.post("/users", response_model=MobileUserResponse)
async def create_mobile_user(payload: MobileUserCreate, current_user: UserResponse = Depends(_web_current_user)):
    if current_user.role == "master":
        brand, dealer, branch = payload.brand_name, payload.dealer_name, payload.branch
        _require_scope_selected(brand, dealer, branch)
    elif current_user.role == "admin":
        brand, dealer = current_user.brand, current_user.group
        branch = payload.branch
        _require_scope_selected(brand, dealer, branch)
    else:
        brand, dealer, branch = current_user.brand, current_user.group, current_user.location
        _require_scope_selected(brand, dealer, branch)

    if not re.match(r"^[0-9+][0-9+\-\s]{6,14}$", payload.mobile_number or ""):
        raise HTTPException(400, "Invalid mobile number")

    mobile_user_id = await generate_mobile_user_id(branch)
    doc = {
        "mobile_user_id": mobile_user_id,
        "name": payload.name,
        "mobile_number": payload.mobile_number,
        "brand_name": brand,
        "dealer_name": dealer,
        "branch": branch,
        "status": "active",
        "created_by_user_id": current_user.id,
        "created_by_name": current_user.username,
        "created_by_role": current_user.role,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "paired_device_count": 0,
        "active_device_count": 0,
        "last_active_at": None,
    }
    await db.mobile_users.insert_one(dict(doc))
    await _audit(current_user, "create_mobile_user", mobile_user_id, {"branch": branch})

    response = MobileUserResponse(**doc)
    return response


@router.get("/users", response_model=List[MobileUserResponse])
async def list_mobile_users(
    brand_name: Optional[str] = None,
    dealer_name: Optional[str] = None,
    branch: Optional[str] = None,
    current_user: UserResponse = Depends(_web_current_user),
):
    q = _scoped_query_for_user(current_user, brand_name, dealer_name, branch)
    rows = await db.mobile_users.find(q, {"_id": 0, "password_hash": 0}).sort("created_at", -1).to_list(5000)
    return rows


@router.get("/users/{mobile_user_id}", response_model=MobileUserResponse)
async def get_mobile_user(mobile_user_id: str, current_user: UserResponse = Depends(_web_current_user)):
    row = await db.mobile_users.find_one({"mobile_user_id": mobile_user_id}, {"_id": 0, "password_hash": 0})
    if not row:
        raise HTTPException(404, "Mobile user not found")
    return row


@router.put("/users/{mobile_user_id}/status")
async def set_mobile_user_status(mobile_user_id: str, payload: DeviceStatusUpdate, current_user: UserResponse = Depends(_web_current_user)):
    if payload.status not in ("active", "inactive"):
        raise HTTPException(400, "status must be active or inactive")
    result = await db.mobile_users.update_one(
        {"mobile_user_id": mobile_user_id}, {"$set": {"status": payload.status, "updated_at": _now_iso()}}
    )
    if result.matched_count == 0:
        raise HTTPException(404, "Mobile user not found")
    if payload.status == "inactive":
        # Immediately kill every active session for this mobile user.
        await db.mobile_sessions.delete_many({"mobile_user_id": mobile_user_id})
        await db.mobile_devices.update_many(
            {"mobile_user_id": mobile_user_id, "status": "active"},
            {"$set": {"status": "inactive"}},
        )
    await _audit(current_user, f"mobile_user_{payload.status}", mobile_user_id, {})
    return {"message": f"Mobile user set to {payload.status}"}


# ==================== PAIRING (WEB generates code, MOBILE consumes it) ====================

@router.post("/pairing/generate")
async def generate_pairing_code(request: Request, payload: PairingGenerateRequest, current_user: UserResponse = Depends(_web_current_user)):
    mobile_user = await db.mobile_users.find_one({"mobile_user_id": payload.mobile_user_id}, {"_id": 0})
    if not mobile_user:
        raise HTTPException(404, "Mobile user not found")
    if mobile_user.get("status") != "active":
        raise HTTPException(400, "Mobile user is inactive")

    if current_user.role == "master":
        branch = payload.branch or mobile_user["branch"]
    elif current_user.role == "admin":
        if mobile_user["brand_name"] != current_user.brand or mobile_user["dealer_name"] != current_user.group:
            raise HTTPException(403, "Mobile user is outside your Brand/Dealer scope")
        branch = payload.branch
        _require_scope_selected(current_user.brand, current_user.group, branch)
    else:
        if mobile_user["branch"] != current_user.location:
            raise HTTPException(403, "Mobile user is outside your assigned Branch")
        branch = current_user.location

    raw_code = _generate_manual_code()
    raw_pairing_token = _new_raw_token()
    pairing_doc = {
        "pairing_code": raw_code,
        "pairing_token_hash": _hash_token(raw_pairing_token),
        "mobile_user_id": mobile_user["mobile_user_id"],
        "brand_name": mobile_user["brand_name"],
        "dealer_name": mobile_user["dealer_name"],
        "branch": branch,
        "created_by_user_id": current_user.id,
        "created_by_name": current_user.username,
        "created_at": _now_iso(),
        # Stored as a real BSON datetime (not ISO string) so the Mongo TTL
        # index in ensure_mobile_indexes() can expire it automatically.
        "expires_at": _now() + timedelta(minutes=PAIRING_CODE_TTL_MINUTES),
        "used": False,
        "used_at": None,
    }
    # Invalidate any earlier unused code for this mobile user first.
    await db.mobile_pairing_codes.update_many(
        {"mobile_user_id": mobile_user["mobile_user_id"], "used": False},
        {"$set": {"used": True, "used_at": _now_iso(), "invalidated_reason": "superseded"}},
    )
    await db.mobile_pairing_codes.insert_one(dict(pairing_doc))
    await _audit(current_user, "generate_pairing_code", mobile_user["mobile_user_id"], {"branch": branch})

    expires_at_iso = pairing_doc["expires_at"].isoformat()
    api_base_url = _public_api_base_url(request)
    qr_payload = {
        "issuer": "NMTS_SLEEPING_STOCK_PAIRING",
        "version": 2,
        "api_base_url": api_base_url,
        "mobile_user_id": mobile_user["mobile_user_id"],
        "pairing_code": raw_code,
        "pairing_token": raw_pairing_token,
        "expires_at": expires_at_iso,
    }
    qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_H, box_size=12, border=4)
    qr.add_data(json.dumps(qr_payload, separators=(",", ":")))
    qr.make(fit=True)
    qr_image = qr.make_image(fill_color="black", back_color="white")
    qr_buffer = BytesIO()
    qr_image.save(qr_buffer, format="PNG")
    qr_code_data_url = "data:image/png;base64," + base64.b64encode(qr_buffer.getvalue()).decode("ascii")
    return {
        "pairing_code": raw_code,
        "pairing_token": raw_pairing_token,
        "expires_at": expires_at_iso,
        "api_base_url": api_base_url,
        "qr_payload": qr_payload,
        "qr_code_data_url": qr_code_data_url,
    }


@router.post("/pairing/verify")
async def verify_pairing_and_register_device(payload: PairingVerifyRequest):
    """Called by the mobile app (unauthenticated — this IS the login).

    All validation happens before the one-time code is atomically consumed.
    QR pairing additionally requires the random token embedded only in the
    website-generated QR payload. Manual-code pairing remains supported by
    omitting pairing_token.
    """
    now = _now()
    lookup = {
        "mobile_user_id": payload.mobile_user_id.strip().upper(),
        "pairing_code": payload.pairing_code.strip(),
        "used": False,
    }
    pairing_doc = await db.mobile_pairing_codes.find_one(lookup, {"_id": 0})
    if not pairing_doc:
        raise HTTPException(400, "Invalid or already-used pairing code")

    expires_at = pairing_doc["expires_at"]
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if now > expires_at:
        raise HTTPException(400, "Pairing code has expired — request a new code")

    # When the request came from QR scanning, prove that it contains the
    # unguessable token generated and stored by this NMTS website.
    if payload.pairing_token:
        expected_hash = pairing_doc.get("pairing_token_hash") or ""
        if not expected_hash or _hash_token(payload.pairing_token) != expected_hash:
            raise HTTPException(400, "This is not a valid NMTS pairing QR code")

    mobile_user = await db.mobile_users.find_one(
        {"mobile_user_id": lookup["mobile_user_id"]}, {"_id": 0}
    )
    if not mobile_user or mobile_user.get("status") != "active":
        raise HTTPException(403, "Mobile user is inactive")
    if not payload.device_user_name.strip():
        raise HTTPException(400, "Device user name is required")
    if not re.match(r"^[0-9+][0-9+\-\s]{6,14}$", payload.device_user_mobile or ""):
        raise HTTPException(400, "Invalid device user mobile number")

    # Consume only after every validation has passed. The filter closes the
    # race window: exactly one concurrent request can change used=False.
    consume_filter = dict(lookup)
    if payload.pairing_token:
        consume_filter["pairing_token_hash"] = pairing_doc["pairing_token_hash"]
    consumed = await db.mobile_pairing_codes.find_one_and_update(
        consume_filter,
        {"$set": {"used": True, "used_at": now.isoformat()}},
        return_document=ReturnDocument.AFTER,
    )
    if not consumed:
        raise HTTPException(409, "Pairing code was already used by another device")

    device_id = str(uuid.uuid4())
    raw_session_token = _new_raw_token()
    device_doc = {
        "device_id": device_id,
        "mobile_user_id": mobile_user["mobile_user_id"],
        "device_user_name": payload.device_user_name.strip(),
        "device_user_mobile": payload.device_user_mobile.strip(),
        "device_name": payload.device_name,
        "device_info": payload.device_info or "",
        "push_token": payload.push_token or "",
        "paired_at": now.isoformat(),
        "last_active_at": now.isoformat(),
        "app_version": payload.app_version or "",
        "status": "active",
        "brand_name": pairing_doc["brand_name"],
        "dealer_name": pairing_doc["dealer_name"],
        "branch": pairing_doc["branch"],
        "session_token_hash": _hash_token(raw_session_token),
    }
    await db.mobile_devices.insert_one(dict(device_doc))
    await db.mobile_sessions.insert_one({
        "session_token_hash": device_doc["session_token_hash"],
        "device_id": device_id,
        "mobile_user_id": mobile_user["mobile_user_id"],
        "created_at": now.isoformat(),
    })
    await db.mobile_users.update_one(
        {"mobile_user_id": mobile_user["mobile_user_id"]},
        {"$inc": {"paired_device_count": 1, "active_device_count": 1}},
    )
    await _audit(None, "device_paired", mobile_user["mobile_user_id"], {"device_id": device_id}, actor_override=mobile_user["mobile_user_id"])

    return {
        "session_token": raw_session_token,
        "device_id": device_id,
        "mobile_user_id": mobile_user["mobile_user_id"],
        "name": payload.device_user_name.strip(),
        "device_user_name": payload.device_user_name.strip(),
        "device_user_mobile": payload.device_user_mobile.strip(),
        "brand_name": device_doc["brand_name"],
        "dealer_name": device_doc["dealer_name"],
        "branch": device_doc["branch"],
    }


@router.get("/session/validate")
async def validate_session(session=Depends(get_device_session)):
    return {
        "mobile_user_id": session["mobile_user"]["mobile_user_id"],
        "name": session["device"].get("device_user_name") or session["mobile_user"]["name"],
        "device_user_name": session["device"].get("device_user_name") or session["mobile_user"]["name"],
        "device_user_mobile": session["device"].get("device_user_mobile") or session["mobile_user"].get("mobile_number", ""),
        "brand_name": session["brand_name"],
        "dealer_name": session["dealer_name"],
        "branch": session["branch"],
        "device_status": session["device"]["status"],
    }


class PushTokenRegisterRequest(BaseModel):
    push_token: str


@router.put("/devices/push-token")
async def register_push_token(payload: PushTokenRegisterRequest, session=Depends(get_device_session)):
    """Called by the mobile app whenever Expo issues a new/updated push token."""
    await db.mobile_devices.update_one(
        {"device_id": session["device"]["device_id"]},
        {"$set": {"push_token": payload.push_token, "push_token_updated_at": _now_iso()}},
    )
    return {"message": "Push token registered"}


# ==================== DEVICE MANAGEMENT (WEB SIDE) ====================

@router.get("/devices")
async def list_devices(mobile_user_id: Optional[str] = None, current_user: UserResponse = Depends(_web_current_user)):
    q = {}
    if mobile_user_id:
        q["mobile_user_id"] = mobile_user_id
    else:
        scope = _scoped_query_for_user(current_user)
        q = scope
    rows = await db.mobile_devices.find(q, {"_id": 0, "session_token_hash": 0}).sort("paired_at", -1).to_list(5000)
    return rows


@router.put("/devices/{device_id}/status")
async def set_device_status(device_id: str, payload: DeviceStatusUpdate, current_user: UserResponse = Depends(_web_current_user)):
    if payload.status not in ("active", "inactive", "removed"):
        raise HTTPException(400, "Invalid status")
    device = await db.mobile_devices.find_one({"device_id": device_id})
    if not device:
        raise HTTPException(404, "Device not found")

    was_active = device.get("status") == "active"
    await db.mobile_devices.update_one({"device_id": device_id}, {"$set": {"status": payload.status}})

    if payload.status == "removed":
        # Removal is permanent and irreversible — hard-invalidate the
        # session so the only way back in is a brand new pairing code,
        # matching Part 10's rule that re-pairing is required only on
        # removal (not on a simple inactivate/reactivate toggle).
        await db.mobile_sessions.delete_many({"device_id": device_id})
    # 'inactive' intentionally does NOT delete the session row: the device
    # session lookup in get_device_session() already blocks any non-active
    # device with a 403, and reactivating restores access immediately
    # without forcing the user to re-pair.

    if payload.status != "active" and was_active:
        await db.mobile_users.update_one(
            {"mobile_user_id": device["mobile_user_id"]}, {"$inc": {"active_device_count": -1}}
        )
    elif payload.status == "active" and not was_active:
        await db.mobile_users.update_one(
            {"mobile_user_id": device["mobile_user_id"]}, {"$inc": {"active_device_count": 1}}
        )
    await _audit(current_user, f"device_{payload.status}", device["mobile_user_id"], {"device_id": device_id})
    return {"message": f"Device set to {payload.status}"}


# ==================== APP VERSIONING / APK METADATA ====================

@router.post("/app-versions")
async def upsert_app_version(payload: AppVersionUpsert, current_user: UserResponse = Depends(_web_current_user)):
    if current_user.role != "master":
        raise HTTPException(403, "Only Master Admin can publish app versions")
    doc = payload.model_dump()
    doc["release_date"] = _now_iso()
    doc["published_by"] = current_user.username
    await db.mobile_app_versions.update_one(
        {"version_code": doc["version_code"]}, {"$set": doc}, upsert=True
    )
    await _audit(current_user, "publish_app_version", "-", {"version_code": doc["version_code"]})
    return {"message": "App version published", "version": doc}


@router.get("/app-versions/latest")
async def latest_app_version():
    row = await db.mobile_app_versions.find({}, {"_id": 0}).sort("version_code", -1).limit(1).to_list(1)
    if not row:
        raise HTTPException(404, "No app version published yet")
    return row[0]


@router.get("/app-versions")
async def list_app_versions(current_user: UserResponse = Depends(_web_current_user)):
    rows = await db.mobile_app_versions.find({}, {"_id": 0}).sort("version_code", -1).to_list(200)
    return rows


# ==================== NOTIFICATIONS (BRANCH-SCOPED, MOBILE SIDE) ====================
#
# order_requests is a PER-LINE-ITEM collection (one document per part, not
# per request) — many rows share the same request_number/request_group_id.
# "Accept" therefore locks the whole GROUP atomically (via a dedicated
# unique-indexed lock collection, since a single find_one_and_update can't
# atomically claim N sibling documents at once), and "respond" applies each
# line's decision through server.py's own _request_center_transition() —
# the exact same function the web Approve/Reject buttons use — so stock
# reservations, order_activity audit rows, request_headers sync, and email/
# WhatsApp notifications all fire identically regardless of whether the
# decision came from the web or from Sleeping Stock Mobile.

@router.get("/settings/notification-interval")
async def get_notification_interval():
    row = await db.mobile_settings.find_one({"_id": "notification_interval"})
    return {"interval_minutes": row["interval_minutes"] if row else DEFAULT_NOTIFICATION_INTERVAL_MINUTES}


@router.put("/settings/notification-interval")
async def set_notification_interval(payload: NotificationIntervalUpdate, current_user: UserResponse = Depends(_web_current_user)):
    if current_user.role != "master":
        raise HTTPException(403, "Only Master Admin can change the notification interval")
    if payload.interval_minutes < 1:
        raise HTTPException(400, "interval_minutes must be >= 1")
    await db.mobile_settings.update_one(
        {"_id": "notification_interval"}, {"$set": {"interval_minutes": payload.interval_minutes}}, upsert=True
    )
    return {"message": "Notification interval updated", "interval_minutes": payload.interval_minutes}


def _group_key_for(line: dict) -> str:
    return line.get("request_group_id") or line.get("request_number")


@router.get("/notifications")
async def list_branch_notifications(session=Depends(get_device_session)):
    """Pending request line items for the device's bound Branch only —
    scope is never taken from the client — grouped into one card per
    request_number/request_group_id with aggregated totals and a parts list,
    matching what Part 13 of the spec expects the mobile UI to show."""
    branch = session["branch"]
    dealer = session["dealer_name"]
    mobile_user_id = session["mobile_user"]["mobile_user_id"]

    lines = await db.order_requests.find(
        {"supplying_dealer": dealer, "supplying_branch": branch, "status": "Requested"},
        {"_id": 0},
    ).sort("requested_at", -1).to_list(2000)

    groups = {}
    for line in lines:
        key = _group_key_for(line)
        if not key:
            continue
        group = groups.setdefault(key, {
            "request_group_key": key,
            "request_number": line.get("request_number"),
            "requesting_dealer": line.get("requesting_dealer"),
            "requesting_branch": line.get("requesting_branch"),
            "requested_at": line.get("requested_at"),
            "total_items": 0,
            "total_quantity": 0.0,
            "total_value": 0.0,
            "parts": [],
        })
        if line.get("requested_at") and (not group["requested_at"] or line["requested_at"] < group["requested_at"]):
            group["requested_at"] = line["requested_at"]
        group["total_items"] += 1
        group["total_quantity"] += float(line.get("requested_qty") or 0)
        group["total_value"] += float(line.get("value_at_request") or 0)
        group["parts"].append({
            "order_request_id": line["id"],
            "part_number": line.get("part_number"),
            "description": line.get("description"),
            "requested_qty": line.get("requested_qty"),
            "available_qty_at_request": line.get("available_qty_at_request"),
            "loc": line.get("loc_at_request"),
            "purchase_aging_days": line.get("purchase_aging_days_at_request"),
            "sales_aging_days": line.get("sales_aging_days_at_request"),
        })

    group_keys = list(groups.keys())
    locks = await db.mobile_request_group_locks.find({"request_group_key": {"$in": group_keys}}, {"_id": 0}).to_list(2000)
    lock_by_key = {l["request_group_key"]: l for l in locks}
    actions = await db.mobile_notification_actions.find(
        {"request_id": {"$in": group_keys}, "mobile_user_id": mobile_user_id}, {"_id": 0}
    ).to_list(2000)
    action_by_key = {a["request_id"]: a for a in actions}

    results = []
    for key, group in groups.items():
        lock = lock_by_key.get(key)
        action = action_by_key.get(key, {})
        results.append({
            **group,
            "my_skip_count": action.get("skip_count", 0),
            "skip_allowed": action.get("skip_count", 0) < SKIP_LIMIT,
            "accepted_by_mobile_user_id": lock.get("mobile_user_id") if lock else None,
            "accepted_by_device_user_name": lock.get("device_user_name") if lock else None,
            "accepted_by_device_user_mobile": lock.get("device_user_mobile") if lock else None,
            "accepted_by_device_name": lock.get("device_name") if lock else None,
            "accepted_by_another": bool(lock) and lock.get("device_id") != session["device"]["device_id"],
            "accepted_by_me": bool(lock) and lock.get("device_id") == session["device"]["device_id"],
        })
    results.sort(key=lambda r: r["requested_at"] or "", reverse=True)
    return results


@router.post("/notifications/accept")
async def accept_notification(payload: NotificationActionRequest, session=Depends(get_device_session)):
    mobile_user_id = session["mobile_user"]["mobile_user_id"]
    device_id = session["device"]["device_id"]
    device_user_name = session["device"].get("device_user_name") or session["mobile_user"]["name"]
    device_user_mobile = session["device"].get("device_user_mobile") or session["mobile_user"].get("mobile_number", "")
    branch = session["branch"]
    dealer = session["dealer_name"]

    # Confirm at least one live "Requested" line still exists in this
    # group, in this device's scope, before claiming the lock — prevents
    # locking a stale/foreign group_key the client might send.
    exists = await db.order_requests.find_one(
        {"$or": [{"request_group_id": payload.request_group_key}, {"request_number": payload.request_group_key}],
         "supplying_dealer": dealer, "supplying_branch": branch, "status": "Requested"},
        {"_id": 0, "id": 1},
    )
    if not exists:
        raise HTTPException(400, "Request is not available to accept")

    # Atomic group-level lock: insert_one against a unique index is the
    # only way to atomically claim N sibling line-item documents at once —
    # the FIRST accepter's insert succeeds, every other accepter's insert
    # hits the unique constraint and is rejected as a clean 409.
    try:
        await db.mobile_request_group_locks.insert_one({
            "request_group_key": payload.request_group_key,
            "mobile_user_id": mobile_user_id,
            "device_id": device_id,
            "device_user_name": device_user_name,
            "device_user_mobile": device_user_mobile,
            "device_name": session["device"].get("device_name", ""),
            "brand_name": session["brand_name"],
            "dealer_name": dealer,
            "branch": branch,
            "accepted_at": _now_iso(),
        })
    except DuplicateKeyError:
        raise HTTPException(409, "Already accepted by another user")

    await _audit(None, "mobile_accept_request", mobile_user_id, {"request_group_key": payload.request_group_key})
    return {"message": "Request accepted", "request_group_key": payload.request_group_key}


@router.post("/notifications/skip")
async def skip_notification(payload: NotificationActionRequest, session=Depends(get_device_session)):
    mobile_user_id = session["mobile_user"]["mobile_user_id"]
    action = await db.mobile_notification_actions.find_one(
        {"request_id": payload.request_group_key, "mobile_user_id": mobile_user_id}
    )
    current_skips = action.get("skip_count", 0) if action else 0
    if current_skips >= SKIP_LIMIT:
        raise HTTPException(400, "Skip limit reached — you must accept or reject this request")

    await db.mobile_notification_actions.update_one(
        {"request_id": payload.request_group_key, "mobile_user_id": mobile_user_id},
        {
            "$set": {"last_skipped_at": _now_iso(), "branch": session["branch"]},
            "$inc": {"skip_count": 1},
        },
        upsert=True,
    )
    return {"message": "Skipped", "skip_count": current_skips + 1, "skip_allowed_remaining": max(0, SKIP_LIMIT - (current_skips + 1))}


@router.post("/notifications/respond")
async def submit_part_response(payload: RequestPartResponse, session=Depends(get_device_session)):
    mobile_user = session["mobile_user"]
    mobile_user_id = mobile_user["mobile_user_id"]

    lock = await db.mobile_request_group_locks.find_one({"request_group_key": payload.request_group_key})
    if not lock or lock.get("device_id") != session["device"]["device_id"]:
        raise HTTPException(403, "This device did not pick this request")

    if not payload.parts:
        raise HTTPException(400, "At least one part response is required")

    lines_by_id = {}
    for part in payload.parts:
        line = await db.order_requests.find_one({"id": part.order_request_id}, {"_id": 0})
        if not line or _group_key_for(line) != payload.request_group_key:
            raise HTTPException(404, f"Part {part.part_number} is not part of this request")
        if line.get("status") != "Requested":
            raise HTTPException(400, f"Part {part.part_number} has already been decided")
        requested_qty = float(line.get("requested_qty") or 0)
        if part.accepted_qty < 0:
            raise HTTPException(400, f"Accepted quantity for {part.part_number} cannot be negative")
        if part.accepted_qty > requested_qty:
            raise HTTPException(400, f"Accepted quantity for {part.part_number} cannot exceed the requested quantity")
        # Fully accepted (accepted_qty == requested_qty): remark optional.
        # Partially accepted or rejected: remark is mandatory (Part 14).
        if part.accepted_qty < requested_qty and not (part.remark and part.remark.strip()):
            raise HTTPException(400, f"Remark is mandatory for {part.part_number} (partial or rejected)")
        lines_by_id[part.order_request_id] = line

    acting_user = _MobileActingUser(mobile_user_id, session["device"].get("device_user_name") or mobile_user["name"], session["dealer_name"], session["brand_name"])
    acting_user.phone = session["device"].get("device_user_mobile") or mobile_user.get("mobile_number", "")

    results = []
    for part in payload.parts:
        requested_qty = float(lines_by_id[part.order_request_id].get("requested_qty") or 0)
        target_status = "Rejected" if part.accepted_qty <= 0 else "Approved"
        remark = part.remark or ""
        updated, changed = await request_center_transition(
            part.order_request_id, target_status, remark, acting_user, accepted_qty=part.accepted_qty
        )
        if changed and notify_request_status_change:
            if target_status == "Rejected":
                event = "Request Rejected"
            elif part.accepted_qty < requested_qty:
                event = "Request Partially Accepted"
            else:
                event = "Request Accepted"
            await notify_request_status_change(updated, event)
        results.append({
            "order_request_id": part.order_request_id,
            "part_number": part.part_number,
            "status": updated.get("status"),
            "accepted_qty": part.accepted_qty,
        })

    await _audit(None, "mobile_submit_response", mobile_user_id, {"request_group_key": payload.request_group_key, "results": results})
    return {"message": "Response submitted", "results": results}

# ==================== STOCK VERIFICATION (MOBILE) ====================

@router.post("/stock-verification")
async def submit_stock_verification(payload: StockVerificationSubmit, session=Depends(get_device_session)):
    if payload.entry_method not in ("MANUAL", "CAMERA_OCR"):
        raise HTTPException(400, "entry_method must be MANUAL or CAMERA_OCR")
    # Remark is optional for both entry methods per Part 15 — no mandatory check.

    if payload.client_id:
        existing = await db.stock_verification_history.find_one(
            {"device_id": session["device"]["device_id"], "client_id": payload.client_id},
            {"_id": 0, "id": 1},
        )
        if existing:
            # Already recorded from an earlier sync attempt whose response was
            # lost — return success again instead of inserting a duplicate.
            return {"message": "Verification recorded", "id": existing["id"], "duplicate": True}

    system_record = await db.products.find_one(
        {
            "part_number": payload.part_number,
            "brand_name": session["brand_name"],
            "dealer_name": session["dealer_name"],
            "branch": session["branch"],
        },
        {"_id": 0, "part_name": 1, "quantity": 1},
    )

    doc = {
        "id": str(uuid.uuid4()),
        "client_id": payload.client_id,
        "part_number": payload.part_number,
        "part_name": (system_record or {}).get("part_name", ""),
        "system_quantity": (system_record or {}).get("quantity"),
        "physical_quantity": payload.physical_qty,
        "location": payload.location or "",
        "remark": payload.remark or "",
        "entry_method": payload.entry_method,
        "verified_user": session["mobile_user"]["name"],
        "mobile_user_id": session["mobile_user"]["mobile_user_id"],
        "device_id": session["device"]["device_id"],
        "brand_name": session["brand_name"],
        "dealer_name": session["dealer_name"],
        "branch": session["branch"],
        "verified_at": _now_iso(),
    }
    await db.stock_verification_history.insert_one(dict(doc))
    return {"message": "Verification recorded", "id": doc["id"], "duplicate": False}


@router.get("/stock-verification/history")
async def stock_verification_history(
    part_number: Optional[str] = None,
    limit: int = 200,
    session=Depends(get_device_session),
):
    q = {"brand_name": session["brand_name"], "dealer_name": session["dealer_name"], "branch": session["branch"]}
    if part_number:
        q["part_number"] = part_number
    rows = await db.stock_verification_history.find(q, {"_id": 0}).sort("verified_at", -1).limit(min(limit, 1000)).to_list(1000)
    return rows


# ==================== STOCK SEARCH (MOBILE) ====================

@router.get("/stock-search")
async def stock_search(part_numbers: str, session=Depends(get_device_session)):
    """part_numbers: comma or newline separated list — supports single or multi search."""
    parts = [p.strip() for p in re.split(r"[,\n]", part_numbers) if p.strip()]
    if not parts:
        raise HTTPException(400, "Provide at least one part number")

    q = {
        "brand_name": session["brand_name"],
        "dealer_name": session["dealer_name"],
        "branch": session["branch"],
        "part_number": {"$in": parts},
        "publish_status": "published",
        "is_active_today": True,
    }
    rows = await db.products.find(q, {"_id": 0}).to_list(2000)
    found_parts = {r["part_number"] for r in rows}
    not_found = [p for p in parts if p not in found_parts]
    return {"results": rows, "not_found": not_found}


# ==================== AUDIT LOG ====================

async def _audit(current_user, action: str, target: str, details: dict, actor_override: Optional[str] = None):
    await db.mobile_audit_logs.insert_one({
        "id": str(uuid.uuid4()),
        "action": action,
        "target": target,
        "details": details,
        "actor_user_id": (current_user.id if current_user else actor_override),
        "actor_name": (current_user.username if current_user else actor_override),
        "actor_role": (current_user.role if current_user else "mobile_device"),
        "created_at": _now_iso(),
    })


# ==================== INDEXES ====================

async def ensure_mobile_indexes():
    await db.mobile_users.create_index([("mobile_user_id", 1)], unique=True)
    await db.mobile_users.create_index([("mobile_number", 1)])
    await db.mobile_users.create_index([("brand_name", 1), ("dealer_name", 1), ("branch", 1)])
    await db.mobile_users.create_index([("created_by_user_id", 1)])

    # NOTE: mobile_devices is a pre-existing collection name (the removed legacy
    # pairing system used it with a different document shape — keyed by
    # `user_id`, no `device_id`/`session_token_hash` fields). Those legacy
    # documents are left in place untouched as historical data, but a plain
    # unique index on a field they don't have would fail (multiple docs
    # missing the field all count as null, which collides under a unique
    # index). Restricting the index to documents that actually have the
    # field avoids that entirely.
    await db.mobile_devices.create_index(
        [("device_id", 1)], unique=True, partialFilterExpression={"device_id": {"$type": "string"}}
    )
    await db.mobile_devices.create_index([("mobile_user_id", 1)])
    await db.mobile_devices.create_index(
        [("session_token_hash", 1)], unique=True, partialFilterExpression={"session_token_hash": {"$type": "string"}}
    )
    await db.mobile_devices.create_index([("push_token", 1)])

    await db.mobile_sessions.create_index([("session_token_hash", 1)], unique=True)
    await db.mobile_sessions.create_index([("device_id", 1)])

    await db.mobile_pairing_codes.create_index([("mobile_user_id", 1), ("pairing_code", 1)])
    await db.mobile_pairing_codes.create_index([("expires_at", 1)], expireAfterSeconds=0)

    await db.mobile_notification_actions.create_index([("request_id", 1), ("mobile_user_id", 1)], unique=True)

    await db.mobile_request_group_locks.create_index([("request_group_key", 1)], unique=True)

    await db.mobile_app_versions.create_index([("version_code", 1)], unique=True)

    await db.stock_verification_history.create_index([("part_number", 1)])
    await db.stock_verification_history.create_index([("verified_at", -1)])
    await db.stock_verification_history.create_index(
        [("brand_name", 1), ("dealer_name", 1), ("branch", 1), ("verified_at", -1)]
    )
    await db.stock_verification_history.create_index(
        [("device_id", 1), ("client_id", 1)],
        unique=True,
        partialFilterExpression={"client_id": {"$type": "string"}},
    )

    await db.mobile_audit_logs.create_index([("created_at", -1)])
    await db.mobile_audit_logs.create_index([("target", 1)])

    logger.info("Sleeping Stock Mobile indexes verified")
