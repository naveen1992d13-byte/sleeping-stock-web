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
from zoneinfo import ZoneInfo
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status, Header, Request, UploadFile, File, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, ConfigDict
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError, OperationFailure

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
    branch_key = re.sub(r"[^A-Za-z0-9]", "", (branch_name or "").upper())[:32] or "GEN"
    counter_key = f"mobile_user:{code}:{date_key}:{branch_key}"
    for _attempt in range(8):
        counter = await db.counters.find_one_and_update(
            {"_id": counter_key},
            {"$inc": {"seq": 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        seq = counter["seq"]
        if seq > 9999:
            raise HTTPException(500, "Daily mobile user serial exhausted for this branch")
        candidate = f"MU{code}{date_key}{seq:04d}"
        if not await db.mobile_users.find_one({"mobile_user_id": candidate}, {"_id": 1}):
            return candidate
    raise HTTPException(500, "Unable to allocate a unique Mobile User ID — try again")


def _generate_manual_code() -> str:
    return "".join(random.choices(string.digits, k=6))


def _normalize_mobile_number(value: str) -> str:
    """Store and compare Indian mobile numbers in a single 10-digit form."""
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if not re.fullmatch(r"[6-9][0-9]{9}", digits):
        raise HTTPException(400, "Enter a valid 10-digit mobile number")
    return digits


def _scope_ci(value: str) -> dict:
    return {"$regex": f"^{re.escape((value or '').strip())}$", "$options": "i"}


def _is_aggregate_scope(value: str) -> bool:
    v = (value or "").strip()
    if not v or v.upper() == "N/A":
        return True
    return v.lower().startswith("all ")


def _normalize_pairing_code(value: str) -> str:
    """Pairing codes are six digits; tolerate stray whitespace from QR/manual entry."""
    return re.sub(r"\D", "", (value or "").strip())


def _dedupe_api_base_path(url: str) -> str:
    base = (url or "").strip().rstrip("/")
    while base.lower().endswith("/api/api"):
        base = base[:-4]
    return base


async def _resolve_canonical_pairing_scope(brand: str, dealer: str, branch: str) -> tuple:
    """Resolve Brand + Dealer + Branch to exact master-data names (generic for all dealers)."""
    brand = (brand or "").strip()
    dealer = (dealer or "").strip()
    branch = (branch or "").strip()
    if _is_aggregate_scope(brand) or _is_aggregate_scope(dealer) or _is_aggregate_scope(branch):
        raise HTTPException(
            400,
            "Select a specific Brand, Dealer and Branch before pairing. "
            "'All Branches' cannot be used for mobile pairing.",
        )
    if not (brand and dealer and branch):
        raise HTTPException(400, "Brand, Dealer and Branch must all be selected before generating a pairing code")

    brand_doc = await db.brands.find_one({"name": _scope_ci(brand)}, {"_id": 0, "name": 1})
    if not brand_doc:
        raise HTTPException(400, f"Invalid brand selected: {brand}")

    dealer_doc = await db.dealers.find_one({"name": _scope_ci(dealer)}, {"_id": 0, "name": 1, "brand": 1, "brand_name": 1})
    if not dealer_doc:
        dealer_doc = await db.groups.find_one({"name": _scope_ci(dealer)}, {"_id": 0, "name": 1})
    if not dealer_doc:
        raise HTTPException(400, f"Invalid dealer selected: {dealer}")

    canonical_dealer = (dealer_doc.get("name") or dealer).strip()
    branch_doc = await db.branches.find_one(
        {"dealer": _scope_ci(canonical_dealer), "name": _scope_ci(branch)},
        {"_id": 0, "name": 1, "brand": 1, "brand_name": 1, "dealer": 1},
    )
    if not branch_doc:
        branch_doc = await db.branches.find_one(
            {"dealer": canonical_dealer, "name": _scope_ci(branch)},
            {"_id": 0, "name": 1, "brand": 1, "brand_name": 1, "dealer": 1},
        )
    if not branch_doc:
        raise HTTPException(400, f"Invalid branch '{branch}' for dealer '{canonical_dealer}'")

    canonical_brand = (brand_doc.get("name") or brand).strip()
    branch_brand = (branch_doc.get("brand") or branch_doc.get("brand_name") or "").strip()
    if branch_brand and branch_brand.casefold() != canonical_brand.casefold():
        raise HTTPException(400, "Selected branch does not belong to the selected brand")

    canonical_branch = (branch_doc.get("name") or branch).strip()
    return canonical_brand, canonical_dealer, canonical_branch


def _public_api_base_url(request: Request) -> str:
    """Return the externally reachable API base URL embedded in pairing QR codes.

    PUBLIC_API_BASE_URL is preferred for production. When it is not set, the
    current request origin is used, which works with HTTPS Codespaces/preview
    URLs without rebuilding the mobile app.
    """
    configured = _dedupe_api_base_path(os.getenv("PUBLIC_API_BASE_URL") or "")
    if configured:
        if not configured.lower().startswith("https://"):
            raise HTTPException(500, "PUBLIC_API_BASE_URL must use HTTPS")
        if not configured.lower().endswith("/api"):
            configured = f"{configured}/api"
        return configured

    forwarded_proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "https").split(",")[0].strip()
    forwarded_host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(",")[0].strip()
    if not forwarded_host:
        raise HTTPException(500, "Unable to determine the public NMTS server URL")
    if forwarded_proto != "https":
        raise HTTPException(500, "Pairing QR requires an HTTPS public backend URL")
    return _dedupe_api_base_path(f"https://{forwarded_host}/api")

def _require_scope_selected(brand, dealer, branch):
    if _is_aggregate_scope(brand) or _is_aggregate_scope(dealer) or _is_aggregate_scope(branch):
        raise HTTPException(
            400,
            "Select a specific Brand, Dealer and Branch before pairing. "
            "'All Branches' cannot be used for mobile pairing.",
        )
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
    pairing_type: str = "NEW"  # NEW or REPAIR
    mobile_user_id: Optional[str] = None
    brand_name: Optional[str] = None
    dealer_name: Optional[str] = None
    branch: Optional[str] = None


class PairingVerifyRequest(BaseModel):
    mobile_user_id: Optional[str] = None
    pairing_type: Optional[str] = None
    pairing_code: str
    pairing_token: Optional[str] = None
    device_user_name: str
    device_user_mobile: str
    device_name: str
    device_info: Optional[str] = None
    app_version: Optional[str] = None
    push_token: Optional[str] = None


class MobileUserBranchUpdate(BaseModel):
    brand_name: Optional[str] = None
    dealer_name: Optional[str] = None
    branch: str


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
    damage_qty: Optional[float] = 0
    verification_type: Optional[str] = "physical"  # physical | auto
    # Accepted for mobile offline-queue compatibility; backend remains session-authoritative.
    part_name: Optional[str] = None
    verification_session_id: Optional[str] = None
    is_new_part: Optional[bool] = None


class StockVerificationBatchSubmit(BaseModel):
    """Mobile offline-queue bulk sync payload (see NMTS-Mobile BACKEND_API_CONTRACT.md)."""
    items: List[StockVerificationSubmit] = []


class MobileUserAttendanceUpdate(BaseModel):
    attendance_date: Optional[str] = None  # YYYY-MM-DD IST; default today
    status: str  # active | inactive for daily Auto Perpetual attendance


async def _assert_mobile_user_access(current_user, mobile_user_id: str) -> dict:
    row = await db.mobile_users.find_one({"mobile_user_id": mobile_user_id}, {"_id": 0})
    if not row or row.get("deleted_at"):
        raise HTTPException(404, "Mobile user not found")
    q = _scoped_query_for_user(current_user)
    for key in ("brand_name", "dealer_name", "branch"):
        if key in q and row.get(key) != q[key]:
            raise HTTPException(403, "Mobile user is outside your permitted scope")
    return row


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

    brand, dealer, branch = await _resolve_canonical_pairing_scope(brand, dealer, branch)

    normalized_mobile = _normalize_mobile_number(payload.mobile_number)
    existing = await db.mobile_users.find_one({"normalized_mobile_number": normalized_mobile}, {"_id": 0})
    if existing:
        raise HTTPException(status_code=409, detail={
            "code": "MOBILE_USER_ALREADY_EXISTS",
            "message": "This mobile number already has a Mobile User ID. Use Re-pair.",
            "mobile_user_id": existing.get("mobile_user_id"),
            "re_pair_required": True,
        })

    mobile_user_id = await generate_mobile_user_id(branch)
    doc = {
        "mobile_user_id": mobile_user_id,
        "name": payload.name,
        "mobile_number": normalized_mobile,
        "normalized_mobile_number": normalized_mobile,
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
    q["deleted_at"] = {"$exists": False}
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
    await _assert_mobile_user_access(current_user, mobile_user_id)
    if current_user.role not in ("master", "admin", "user"):
        raise HTTPException(403, "Not allowed")
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


@router.delete("/users/{mobile_user_id}")
async def delete_mobile_user(mobile_user_id: str, current_user: UserResponse = Depends(_web_current_user)):
    if current_user.role != "master":
        raise HTTPException(403, "Only Master Admin can delete mobile users")
    row = await _assert_mobile_user_access(current_user, mobile_user_id)
    now = _now_iso()
    await db.mobile_users.update_one(
        {"mobile_user_id": mobile_user_id},
        {"$set": {"status": "deleted", "deleted_at": now, "updated_at": now, "active_device_count": 0}},
    )
    await db.mobile_sessions.delete_many({"mobile_user_id": mobile_user_id})
    await db.mobile_devices.update_many(
        {"mobile_user_id": mobile_user_id},
        {"$set": {"status": "removed", "updated_at": now}},
    )
    await _audit(current_user, "mobile_user_deleted", mobile_user_id, {"name": row.get("name")})
    return {"message": "Mobile user archived. Verification history is preserved."}


@router.put("/users/{mobile_user_id}/attendance")
async def set_mobile_user_attendance(
    mobile_user_id: str,
    payload: MobileUserAttendanceUpdate,
    current_user: UserResponse = Depends(_web_current_user),
):
    if current_user.role not in ("master", "admin", "user"):
        raise HTTPException(403, "Not allowed")
    if payload.status not in ("active", "inactive"):
        raise HTTPException(400, "status must be active or inactive")
    row = await _assert_mobile_user_access(current_user, mobile_user_id)
    india = _now().astimezone(ZoneInfo("Asia/Kolkata"))
    attendance_date = payload.attendance_date or india.strftime("%Y-%m-%d")
    await db.mobile_user_attendance.update_one(
        {"attendance_date": attendance_date, "mobile_user_id": mobile_user_id},
        {
            "$set": {
                "status": payload.status,
                "brand_name": row["brand_name"],
                "dealer_name": row["dealer_name"],
                "branch": row["branch"],
                "updated_at": _now_iso(),
                "updated_by": current_user.id,
            },
            "$setOnInsert": {"created_at": _now_iso()},
        },
        upsert=True,
    )
    return {"message": f"Attendance for {attendance_date} set to {payload.status}"}


@router.get("/users/attendance/today")
async def list_today_attendance(
    brand_name: str,
    dealer_name: str,
    branch: str,
    current_user: UserResponse = Depends(_web_current_user),
):
    q = _scoped_query_for_user(current_user, brand_name, dealer_name, branch)
    if q.get("brand_name") and q["brand_name"] != brand_name:
        raise HTTPException(403, "Brand outside scope")
    if q.get("dealer_name") and q["dealer_name"] != dealer_name:
        raise HTTPException(403, "Dealer outside scope")
    if q.get("branch") and q["branch"] != branch:
        raise HTTPException(403, "Branch outside scope")
    india = _now().astimezone(ZoneInfo("Asia/Kolkata"))
    attendance_date = india.strftime("%Y-%m-%d")
    rows = await db.mobile_user_attendance.find(
        {"attendance_date": attendance_date, "branch": branch},
        {"_id": 0, "mobile_user_id": 1, "status": 1},
    ).to_list(500)
    return {"attendance_date": attendance_date, "records": {r["mobile_user_id"]: r["status"] for r in rows}}


@router.put("/users/{mobile_user_id}/branch")
async def change_mobile_user_branch(mobile_user_id: str, payload: MobileUserBranchUpdate, current_user: UserResponse = Depends(_web_current_user)):
    user_doc = await db.mobile_users.find_one({"mobile_user_id": mobile_user_id}, {"_id": 0})
    if not user_doc:
        raise HTTPException(404, "Mobile user not found")

    if current_user.role == "master":
        brand = payload.brand_name or user_doc.get("brand_name")
        dealer = payload.dealer_name or user_doc.get("dealer_name")
        branch = payload.branch
    elif current_user.role == "admin":
        if user_doc.get("brand_name") != current_user.brand or user_doc.get("dealer_name") != current_user.group:
            raise HTTPException(403, "Mobile user is outside your Brand/Dealer scope")
        brand, dealer, branch = current_user.brand, current_user.group, payload.branch
    else:
        raise HTTPException(403, "Only Master/Admin can change a mobile user's branch")
    _require_scope_selected(brand, dealer, branch)
    brand, dealer, branch = await _resolve_canonical_pairing_scope(brand, dealer, branch)

    await db.mobile_users.update_one({"mobile_user_id": mobile_user_id}, {"$set": {
        "brand_name": brand, "dealer_name": dealer, "branch": branch,
        "updated_at": _now_iso(), "branch_changed_at": _now_iso(),
        "branch_changed_by": current_user.id,
    }})
    await db.mobile_sessions.delete_many({"mobile_user_id": mobile_user_id})
    # Any unused Re-pair QR generated for the old scope must stop working.
    await db.mobile_pairing_codes.update_many(
        {"mobile_user_id": mobile_user_id, "pairing_type": "REPAIR", "used": False},
        {"$set": {"used": True, "used_at": _now_iso(), "invalidated_reason": "branch_changed"}},
    )
    await db.mobile_devices.update_many(
        {"mobile_user_id": mobile_user_id, "status": "active"},
        {"$set": {"status": "inactive", "inactive_reason": "branch_changed", "updated_at": _now_iso()}},
    )
    await db.mobile_users.update_one({"mobile_user_id": mobile_user_id}, {"$set": {"active_device_count": 0}})
    await _audit(current_user, "mobile_user_branch_changed", mobile_user_id, {"brand_name": brand, "dealer_name": dealer, "branch": branch})
    return {"message": "Branch updated. Generate a Re-pair QR for this user.", "mobile_user_id": mobile_user_id, "branch": branch}


# ==================== PAIRING (WEB generates code, MOBILE consumes it) ====================

@router.post("/pairing/generate")
async def generate_pairing_code(request: Request, payload: PairingGenerateRequest, current_user: UserResponse = Depends(_web_current_user)):
    pairing_type = (payload.pairing_type or "NEW").strip().upper()
    if pairing_type not in ("NEW", "REPAIR"):
        raise HTTPException(400, "pairing_type must be NEW or REPAIR")

    mobile_user = None
    if pairing_type == "REPAIR":
        if not payload.mobile_user_id:
            raise HTTPException(400, "Mobile User ID is required for Re-pair")
        mobile_user = await db.mobile_users.find_one({"mobile_user_id": payload.mobile_user_id.strip().upper()}, {"_id": 0})
        if not mobile_user:
            raise HTTPException(404, "Mobile user not found")
        if mobile_user.get("status") != "active":
            raise HTTPException(400, "Mobile user is inactive")

    if current_user.role == "master":
        brand = payload.brand_name or (mobile_user or {}).get("brand_name")
        dealer = payload.dealer_name or (mobile_user or {}).get("dealer_name")
        branch = payload.branch or (mobile_user or {}).get("branch")
    elif current_user.role == "admin":
        brand, dealer = current_user.brand, current_user.group
        branch = payload.branch or (mobile_user or {}).get("branch")
    else:
        brand, dealer, branch = current_user.brand, current_user.group, current_user.location
        if pairing_type == "REPAIR" and mobile_user.get("branch") != branch:
            raise HTTPException(403, "Mobile user is outside your assigned Branch")
    _require_scope_selected(brand, dealer, branch)
    brand, dealer, branch = await _resolve_canonical_pairing_scope(brand, dealer, branch)

    if current_user.role == "admin" and mobile_user:
        if (
            (mobile_user.get("brand_name") or "").casefold() != brand.casefold()
            or (mobile_user.get("dealer_name") or "").casefold() != dealer.casefold()
        ):
            raise HTTPException(403, "Mobile user is outside your Brand/Dealer scope")

    if pairing_type == "REPAIR" and mobile_user:
        if (
            (mobile_user.get("brand_name") or "").casefold() != brand.casefold()
            or (mobile_user.get("dealer_name") or "").casefold() != dealer.casefold()
        ):
            raise HTTPException(403, "Mobile user is outside the selected Brand/Dealer scope")

    raw_code = _generate_manual_code()
    raw_pairing_token = _new_raw_token()
    target_id = mobile_user.get("mobile_user_id") if mobile_user else None
    pairing_doc = {
        "pairing_type": pairing_type,
        "pairing_code": raw_code,
        "pairing_token_hash": _hash_token(raw_pairing_token),
        "mobile_user_id": target_id,
        "brand_name": brand,
        "dealer_name": dealer,
        "branch": branch,
        "created_by_user_id": current_user.id,
        "created_by_name": current_user.username,
        "created_at": _now_iso(),
        "expires_at": _now() + timedelta(minutes=PAIRING_CODE_TTL_MINUTES),
        "used": False,
        "used_at": None,
    }
    invalidate_query = {"used": False}
    if pairing_type == "REPAIR":
        invalidate_query.update({"pairing_type": "REPAIR", "mobile_user_id": target_id})
    else:
        invalidate_query.update({"pairing_type": "NEW", "brand_name": brand, "dealer_name": dealer, "branch": branch})
    await db.mobile_pairing_codes.update_many(invalidate_query, {"$set": {"used": True, "used_at": _now_iso(), "invalidated_reason": "superseded"}})
    for _attempt in range(6):
        try:
            await db.mobile_pairing_codes.insert_one(dict(pairing_doc))
            break
        except DuplicateKeyError:
            pairing_doc["pairing_code"] = _generate_manual_code()
            raw_code = pairing_doc["pairing_code"]
            raw_pairing_token = _new_raw_token()
            pairing_doc["pairing_token_hash"] = _hash_token(raw_pairing_token)
    else:
        raise HTTPException(500, "Unable to allocate a unique pairing code — try again")
    await _audit(current_user, f"generate_{pairing_type.lower()}_pairing_code", target_id or branch, {"branch": branch})

    expires_at_iso = pairing_doc["expires_at"].isoformat()
    api_base_url = _public_api_base_url(request)
    qr_payload = {
        "issuer": "NMTS_SLEEPING_STOCK_PAIRING",
        "version": 3,
        "pairing_type": pairing_type,
        "api_base_url": api_base_url,
        "mobile_user_id": target_id,
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
    return {"pairing_type": pairing_type, "mobile_user_id": target_id, "pairing_code": raw_code, "pairing_token": raw_pairing_token, "expires_at": expires_at_iso, "api_base_url": api_base_url, "qr_payload": qr_payload, "qr_code_data_url": "data:image/png;base64," + base64.b64encode(qr_buffer.getvalue()).decode("ascii")}


@router.post("/pairing/verify")
async def verify_pairing_and_register_device(payload: PairingVerifyRequest):
    now = _now()
    normalized_mobile = _normalize_mobile_number(payload.device_user_mobile)
    if not payload.device_user_name.strip():
        raise HTTPException(400, "Device user name is required")

    pairing_code = _normalize_pairing_code(payload.pairing_code)
    if not pairing_code:
        raise HTTPException(400, "Pairing code is required")

    # QR pairing uses the high-entropy token as the primary lookup key. A short
    # 6-digit code can legitimately collide across branches, so looking up by
    # code alone could select another branch's record.
    if payload.pairing_token:
        lookup = {
            "pairing_token_hash": _hash_token(payload.pairing_token),
            "pairing_code": pairing_code,
            "used": False,
        }
        pairing_doc = await db.mobile_pairing_codes.find_one(lookup, {"_id": 0})
    else:
        # Backward-compatible manual-code path. Reject an ambiguous code rather
        # than pairing the device to an arbitrary branch.
        matches = await db.mobile_pairing_codes.find(
            {"pairing_code": pairing_code, "used": False}, {"_id": 0}
        ).limit(2).to_list(2)
        if len(matches) > 1:
            raise HTTPException(409, "Pairing code is ambiguous. Scan the QR code instead.")
        pairing_doc = matches[0] if matches else None
    if not pairing_doc:
        raise HTTPException(400, "Invalid or already-used pairing code")
    expires_at = pairing_doc["expires_at"]
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if now > expires_at:
        raise HTTPException(400, "Pairing code has expired — request a new code")
    if payload.pairing_token:
        expected_hash = pairing_doc.get("pairing_token_hash") or ""
        if not expected_hash or _hash_token(payload.pairing_token) != expected_hash:
            raise HTTPException(400, "This is not a valid NMTS pairing QR code")

    scope_brand, scope_dealer, scope_branch = await _resolve_canonical_pairing_scope(
        pairing_doc.get("brand_name"),
        pairing_doc.get("dealer_name"),
        pairing_doc.get("branch"),
    )
    pairing_doc = {
        **pairing_doc,
        "brand_name": scope_brand,
        "dealer_name": scope_dealer,
        "branch": scope_branch,
    }

    pairing_type = (pairing_doc.get("pairing_type") or payload.pairing_type or ("REPAIR" if pairing_doc.get("mobile_user_id") else "NEW")).upper()
    mobile_user = None
    if pairing_type == "NEW":
        existing = await db.mobile_users.find_one({"normalized_mobile_number": normalized_mobile}, {"_id": 0})
        if not existing:
            existing = await db.mobile_users.find_one({"mobile_number": normalized_mobile}, {"_id": 0})
        if existing:
            raise HTTPException(status_code=409, detail={"code": "MOBILE_USER_ALREADY_EXISTS", "message": "Mobile User ID already created for this mobile number. Ask Admin to generate a Re-pair QR.", "mobile_user_id": existing.get("mobile_user_id"), "re_pair_required": True})
    else:
        target_id = (pairing_doc.get("mobile_user_id") or payload.mobile_user_id or "").strip().upper()
        mobile_user = await db.mobile_users.find_one({"mobile_user_id": target_id}, {"_id": 0})
        if not mobile_user or mobile_user.get("status") != "active":
            raise HTTPException(403, "Mobile user is inactive or not found")
        stored_mobile = mobile_user.get("normalized_mobile_number") or _normalize_mobile_number(mobile_user.get("mobile_number", ""))
        if stored_mobile != normalized_mobile:
            raise HTTPException(409, "Entered mobile number does not match this Re-pair QR")

    consume_filter = {"pairing_code": pairing_doc["pairing_code"], "used": False}
    if payload.pairing_token:
        consume_filter["pairing_token_hash"] = pairing_doc["pairing_token_hash"]
    consumed = await db.mobile_pairing_codes.find_one_and_update(
        consume_filter,
        {"$set": {"used": True, "used_at": now.isoformat()}},
        return_document=ReturnDocument.AFTER,
    )
    if not consumed:
        raise HTTPException(409, "Pairing code was already used by another device")

    try:
        if pairing_type == "NEW":
            mobile_user_id = await generate_mobile_user_id(pairing_doc["branch"])
            mobile_user = {
                "mobile_user_id": mobile_user_id, "name": payload.device_user_name.strip(),
                "mobile_number": normalized_mobile, "normalized_mobile_number": normalized_mobile,
                "brand_name": pairing_doc["brand_name"], "dealer_name": pairing_doc["dealer_name"], "branch": pairing_doc["branch"],
                "status": "active", "created_by_user_id": pairing_doc.get("created_by_user_id", ""),
                "created_by_name": pairing_doc.get("created_by_name", ""), "created_by_role": "pairing",
                "created_at": now.isoformat(), "updated_at": now.isoformat(), "paired_device_count": 0, "active_device_count": 0, "last_active_at": None,
            }
            try:
                await db.mobile_users.insert_one(dict(mobile_user))
            except DuplicateKeyError:
                existing = await db.mobile_users.find_one(
                    {"$or": [{"normalized_mobile_number": normalized_mobile}, {"mobile_user_id": mobile_user["mobile_user_id"]}]},
                    {"_id": 0},
                )
                raise HTTPException(status_code=409, detail={
                    "code": "MOBILE_USER_ALREADY_EXISTS",
                    "message": "Mobile User ID already created. Use Re-pair.",
                    "mobile_user_id": (existing or {}).get("mobile_user_id"),
                    "re_pair_required": True,
                })
        else:
            target_id = mobile_user["mobile_user_id"]
            await db.mobile_sessions.delete_many({"mobile_user_id": target_id})
            await db.mobile_devices.update_many({"mobile_user_id": target_id, "status": "active"}, {"$set": {"status": "inactive", "inactive_reason": "repaired", "updated_at": now.isoformat()}})
            await db.mobile_users.update_one({"mobile_user_id": target_id}, {"$set": {"active_device_count": 0, "updated_at": now.isoformat()}})

        device_id = str(uuid.uuid4())
        raw_session_token = _new_raw_token()
        device_doc = {"device_id": device_id, "mobile_user_id": mobile_user["mobile_user_id"], "device_user_name": payload.device_user_name.strip(), "device_user_mobile": normalized_mobile, "device_name": payload.device_name, "device_info": payload.device_info or "", "push_token": payload.push_token or "", "paired_at": now.isoformat(), "last_active_at": now.isoformat(), "app_version": payload.app_version or "", "status": "active", "brand_name": pairing_doc["brand_name"], "dealer_name": pairing_doc["dealer_name"], "branch": pairing_doc["branch"], "session_token_hash": _hash_token(raw_session_token)}
        await db.mobile_devices.insert_one(dict(device_doc))
        await db.mobile_sessions.insert_one({"session_token_hash": device_doc["session_token_hash"], "device_id": device_id, "mobile_user_id": mobile_user["mobile_user_id"], "created_at": now.isoformat()})
        await db.mobile_users.update_one({"mobile_user_id": mobile_user["mobile_user_id"]}, {"$inc": {"paired_device_count": 1}, "$set": {"active_device_count": 1, "last_active_at": now.isoformat(), "updated_at": now.isoformat()}})
    except HTTPException:
        await db.mobile_pairing_codes.update_one(
            {"pairing_token_hash": pairing_doc["pairing_token_hash"], "pairing_code": pairing_doc["pairing_code"]},
            {"$set": {"used": False, "used_at": None, "invalidated_reason": "verify_failed_rollback"}},
        )
        raise
    except Exception:
        await db.mobile_pairing_codes.update_one(
            {"pairing_token_hash": pairing_doc["pairing_token_hash"], "pairing_code": pairing_doc["pairing_code"]},
            {"$set": {"used": False, "used_at": None, "invalidated_reason": "verify_failed_rollback"}},
        )
        raise

    await _audit(None, "device_repaired" if pairing_type == "REPAIR" else "device_paired", mobile_user["mobile_user_id"], {"device_id": device_id}, actor_override=mobile_user["mobile_user_id"])
    return {"session_token": raw_session_token, "device_id": device_id, "mobile_user_id": mobile_user["mobile_user_id"], "name": payload.device_user_name.strip(), "device_user_name": payload.device_user_name.strip(), "device_user_mobile": normalized_mobile, "brand_name": device_doc["brand_name"], "dealer_name": device_doc["dealer_name"], "branch": device_doc["branch"], "pairing_type": pairing_type}


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
async def list_devices(
    mobile_user_id: Optional[str] = None,
    brand_name: Optional[str] = None,
    dealer_name: Optional[str] = None,
    branch: Optional[str] = None,
    current_user: UserResponse = Depends(_web_current_user),
):
    if mobile_user_id:
        owner = await db.mobile_users.find_one({"mobile_user_id": mobile_user_id}, {"_id": 0})
        if not owner:
            raise HTTPException(404, "Mobile user not found")
        allowed = _scoped_query_for_user(current_user, owner.get("brand_name"), owner.get("dealer_name"), owner.get("branch"))
        if any(owner.get(k) != v for k, v in allowed.items()):
            raise HTTPException(403, "Mobile user is outside your scope")
        q = {"mobile_user_id": mobile_user_id}
    else:
        q = _scoped_query_for_user(current_user, brand_name, dealer_name, branch)
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


_APK_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mobile_apk_storage")


@router.post("/app-versions/upload")
async def upload_apk_file(
    file: UploadFile = File(...),
    current_user: UserResponse = Depends(_web_current_user),
):
    if current_user.role != "master":
        raise HTTPException(403, "Only Master Admin can upload APK files")
    if not file.filename or not file.filename.lower().endswith(".apk"):
        raise HTTPException(400, "Only .apk files are allowed")
    os.makedirs(_APK_DIR, exist_ok=True)
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", file.filename)
    dest = os.path.join(_APK_DIR, safe_name)
    content = await file.read()
    if len(content) < 1024:
        raise HTTPException(400, "APK file is too small")
    with open(dest, "wb") as fh:
        fh.write(content)
    stored = {}
    try:
        try:
            import file_objects
        except ImportError:
            from . import file_objects
        stored = await file_objects.store_bytes(
            module="mobile/apk",
            relative_key=safe_name,
            data=content,
            original_filename=safe_name,
            content_type="application/vnd.android.package-archive",
        )
    except Exception:
        stored = {}
    version_code = int(_now().timestamp())
    public_token = str(uuid.uuid4()).replace("-", "")
    doc = {
        "version_name": safe_name.replace(".apk", ""),
        "version_code": version_code,
        "apk_filename": safe_name,
        "apk_path": dest,
        "storage_provider": stored.get("storage_provider"),
        "storage_key": stored.get("storage_key"),
        "sha256": stored.get("sha256"),
        "archived_at": stored.get("archived_at"),
        "content_type": "application/vnd.android.package-archive",
        "public_download_token": public_token,
        "release_notes": "",
        "min_supported_version_code": 1,
        "mandatory": False,
        "release_date": _now_iso(),
        "published_by": current_user.username,
    }
    await db.mobile_app_versions.update_one({"version_code": version_code}, {"$set": doc}, upsert=True)
    await _audit(current_user, "upload_apk", safe_name, {"version_code": version_code})
    return {"message": "APK uploaded", "version": doc}


@router.get("/app-versions/download/latest")
async def download_latest_apk(current_user: UserResponse = Depends(_web_current_user)):
    row = await db.mobile_app_versions.find({}, {"_id": 0}).sort("version_code", -1).limit(1).to_list(1)
    if not row:
        raise HTTPException(404, "No APK published")
    meta = row[0]
    try:
        import file_objects
    except ImportError:
        from . import file_objects
    if not file_objects.meta_has_readable_bytes(meta):
        raise HTTPException(404, "APK file missing on server")
    return file_objects.streaming_response_from_meta(
        meta,
        filename=meta.get("apk_filename") or "sleeping-stock.apk",
    )


@router.get("/app-versions/download-link/latest")
async def latest_apk_download_link(request: Request, current_user: UserResponse = Depends(_web_current_user)):
    row = await db.mobile_app_versions.find({}, {"_id": 0, "public_download_token": 1}).sort("version_code", -1).limit(1).to_list(1)
    base = _public_api_base_url(request)
    if row and row[0].get("public_download_token"):
        return {"download_url": f"{base}/mobile/app-versions/public/{row[0]['public_download_token']}"}
    return {"download_url": f"{base}/mobile/app-versions/download/latest"}


@router.get("/app-versions/public/{token}")
async def download_apk_public(token: str):
    """Shareable APK URL (no auth). Token is issued when Master uploads an APK."""
    meta = await db.mobile_app_versions.find_one({"public_download_token": token}, {"_id": 0})
    if not meta:
        raise HTTPException(404, "Download link invalid or expired")
    try:
        import file_objects
    except ImportError:
        from . import file_objects
    if not file_objects.meta_has_readable_bytes(meta):
        raise HTTPException(404, "APK file missing on server")
    return file_objects.streaming_response_from_meta(
        meta,
        filename=meta.get("apk_filename") or "sleeping-stock.apk",
    )


# ==================== AUTO PERPETUAL (WEB) ====================

@router.post("/auto-perpetual/generate")
async def generate_auto_perpetual(
    brand_name: str,
    dealer_name: str,
    branch: str,
    recalc_pending: bool = False,
    current_user: UserResponse = Depends(_web_current_user),
):
    if current_user.role not in ("master", "admin", "user"):
        raise HTTPException(403, "Not allowed")
    q = _scoped_query_for_user(current_user, brand_name, dealer_name, branch)
    if q.get("brand_name") and q["brand_name"] != brand_name:
        raise HTTPException(403, "Brand outside scope")
    if q.get("dealer_name") and q["dealer_name"] != dealer_name:
        raise HTTPException(403, "Dealer outside scope")
    if q.get("branch") and q["branch"] != branch:
        raise HTTPException(403, "Branch outside scope")
    from auto_perpetual import generate_auto_perpetual_for_branch, ist_date_key, resolve_branch_inventory_date_key

    active_date_key = await resolve_branch_inventory_date_key(
        db, brand_name=brand_name, dealer_name=dealer_name, branch=branch
    )
    result = await generate_auto_perpetual_for_branch(
        db,
        brand_name=brand_name,
        dealer_name=dealer_name,
        branch=branch,
        actor_user_id=current_user.id,
        recalc_pending=recalc_pending,
        active_date_key=active_date_key,
    )
    return result


@router.get("/auto-perpetual/suggestions")
async def list_auto_suggestions(
    brand_name: str,
    dealer_name: str,
    branch: str,
    limit: int = 20,
    current_user: UserResponse = Depends(_web_current_user),
):
    rows = await db.auto_perpetual_suggestions.find(
        {"brand_name": brand_name, "dealer_name": dealer_name, "branch": branch},
        {"_id": 0, "items": 0},
    ).sort("created_at", -1).limit(min(limit, 50)).to_list(50)
    return rows


@router.get("/auto-perpetual/suggestions/{suggestion_id}")
async def get_auto_suggestion_detail(
    suggestion_id: str,
    current_user: UserResponse = Depends(_web_current_user),
):
    row = await db.auto_perpetual_suggestions.find_one({"id": suggestion_id}, {"_id": 0})
    if not row:
        raise HTTPException(404, "Suggestion not found")
    items = sorted(
        row.get("items") or [],
        key=lambda r: (r.get("system_location") or "").upper(),
    )
    row["items"] = items
    return row


@router.post("/auto-perpetual/suggestions/{suggestion_id}/send")
async def send_auto_suggestion(
    suggestion_id: str,
    current_user: UserResponse = Depends(_web_current_user),
):
    from auto_perpetual_suggestions import send_suggestion_to_mobile
    from mobile_push import notify_auto_perpetual_assignments

    async def _notify(**kwargs):
        await notify_auto_perpetual_assignments(db, **kwargs)

    return await send_suggestion_to_mobile(
        db,
        suggestion_id=suggestion_id,
        actor_user_id=current_user.id,
        notify_fn=_notify,
    )


@router.get("/auto-perpetual/user-performance")
async def auto_perpetual_user_performance(
    brand_name: str,
    dealer_name: str,
    branch: str,
    month: Optional[str] = None,
    current_user: UserResponse = Depends(_web_current_user),
):
    q = _scoped_query_for_user(current_user, brand_name, dealer_name, branch)
    if q.get("brand_name") and q["brand_name"] != brand_name:
        raise HTTPException(403, "Brand outside scope")
    from auto_perpetual import user_performance_summary

    return await user_performance_summary(db, brand_name=brand_name, dealer_name=dealer_name, branch=branch, month=month)


@router.get("/auto-perpetual/summary")
async def auto_perpetual_summary(
    brand_name: str,
    dealer_name: str,
    branch: str,
    current_user: UserResponse = Depends(_web_current_user),
):
    q = _scoped_query_for_user(current_user, brand_name, dealer_name, branch)
    if q.get("brand_name") and q["brand_name"] != brand_name:
        raise HTTPException(403, "Brand outside scope")
    from auto_perpetual import branch_monthly_summary

    return await branch_monthly_summary(db, brand_name=brand_name, dealer_name=dealer_name, branch=branch)


@router.get("/auto-perpetual/assignments/today")
async def auto_perpetual_assignments_today(
    brand_name: str,
    dealer_name: str,
    branch: str,
    current_user: UserResponse = Depends(_web_current_user),
):
    from auto_perpetual import ist_date_key

    rows = await db.auto_perpetual_assignments.find(
        {
            "allocation_date": ist_date_key(),
            "brand_name": brand_name,
            "dealer_name": dealer_name,
            "branch": branch,
        },
        {"_id": 0},
    ).sort("mobile_user_id", 1).to_list(5000)
    return rows


@router.get("/auto-perpetual/tasks")
async def auto_perpetual_tasks_device(session=Depends(get_device_session)):
    from auto_perpetual import get_or_create_auto_daily_session, ist_date_key

    mu = session["mobile_user"]["mobile_user_id"]
    allocation_date = ist_date_key()
    # Always use the authoritative daily get-or-create session. Do not trust
    # per-assignment session_id stamps — those can diverge across parts.
    session_id = await get_or_create_auto_daily_session(
        db,
        mobile_user_id=mu,
        brand_name=session["brand_name"],
        dealer_name=session["dealer_name"],
        branch=session["branch"],
        device_id=session["device"]["device_id"],
    )
    rows = await db.auto_perpetual_assignments.find(
        {
            "allocation_date": allocation_date,
            "mobile_user_id": mu,
            "status": "pending",
        },
        {"_id": 0},
    ).to_list(500)
    rows.sort(key=lambda r: (r.get("loc") or r.get("part_number") or "").upper())
    # Normalize assignment stamps to today's single session for consistency.
    if rows:
        await db.auto_perpetual_assignments.update_many(
            {
                "allocation_date": allocation_date,
                "mobile_user_id": mu,
            },
            {"$set": {"session_id": session_id}},
        )
    assigned = await db.auto_perpetual_assignments.count_documents(
        {"allocation_date": allocation_date, "mobile_user_id": mu}
    )
    completed = await db.auto_perpetual_assignments.count_documents(
        {"allocation_date": allocation_date, "mobile_user_id": mu, "status": "completed"}
    )
    enriched = []
    for row in rows:
        product, _ = await find_scoped_product(row.get("part_number", ""), session["brand_name"], session["dealer_name"], session["branch"])
        enriched.append(
            {
                **row,
                "session_id": session_id,
                "part_name": resolve_product_part_name(product or {}),
                "system_qty": _mobile_stock_qty(product),
                "system_location": row.get("loc") or _product_pin_location(product or {}),
                "loc": row.get("loc") or _product_pin_location(product or {}),
            }
        )
    return {
        "tasks": enriched,
        "count": len(enriched),
        "session_id": session_id,
        "allocation_date": allocation_date,
        "assigned_count": assigned,
        "completed_count": completed,
    }


@router.post("/auto-perpetual/session/finish")
async def finish_auto_perpetual_session(session=Depends(get_device_session)):
    from auto_perpetual_suggestions import finish_auto_session_for_user

    return await finish_auto_session_for_user(
        db,
        mobile_user_id=session["mobile_user"]["mobile_user_id"],
        brand_name=session["brand_name"],
        dealer_name=session["dealer_name"],
        branch=session["branch"],
    )


@router.get("/auto-perpetual/session/today")
async def auto_perpetual_session_today(session=Depends(get_device_session)):
    from auto_perpetual import get_or_create_auto_daily_session, ist_date_key

    mu = session["mobile_user"]["mobile_user_id"]
    session_id = await get_or_create_auto_daily_session(
        db,
        mobile_user_id=mu,
        brand_name=session["brand_name"],
        dealer_name=session["dealer_name"],
        branch=session["branch"],
        device_id=session["device"]["device_id"],
    )
    return {"session_id": session_id, "allocation_date": ist_date_key()}


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
            await notify_request_status_change(updated, event, getattr(acting_user, "id", "") or "")
        results.append({
            "order_request_id": part.order_request_id,
            "part_number": part.part_number,
            "status": updated.get("status"),
            "accepted_qty": part.accepted_qty,
        })

    await _audit(None, "mobile_submit_response", mobile_user_id, {"request_group_key": payload.request_group_key, "results": results})
    return {"message": "Response submitted", "results": results}

# ==================== STOCK VERIFICATION (MOBILE) ====================

_PUBLISHED_STATUS_CANONICAL = "Published"


def _published_status_filter() -> dict:
    """Canonical publish flag is 'Published'; accept lowercase only for legacy rows."""
    return {"$in": [_PUBLISHED_STATUS_CANONICAL, "published"]}


def _normalize_part_number(part_number: str) -> str:
    return re.sub(r"\s+", "", str(part_number or "").strip()).upper()


def resolve_product_part_name(product: dict) -> str:
    if not product:
        return ""
    return str(
        product.get("part_name")
        or product.get("item_name")
        or product.get("description")
        or product.get("part_description")
        or ""
    ).strip()


def _product_pin_location(product: dict) -> str:
    if not product:
        return ""
    return str(
        product.get("loc")
        or product.get("LOC")
        or product.get("bin_location")
        or product.get("pin_location")
        or product.get("rack_location")
        or product.get("location")
        or ""
    ).strip()


async def find_scoped_product(part_number: str, brand_name: str, dealer_name: str, branch: str):
    """Lookup current Product Hub stock for verification (same day-scope as Product Hub)."""
    from auto_perpetual import inventory_date_key

    clean_part = _normalize_part_number(part_number)
    if not clean_part:
        return None, clean_part
    query = {
        "brand_name": brand_name,
        "dealer_name": dealer_name,
        "branch": branch,
        "publish_status": "Published",
        "is_active_today": True,
        "active_date_key": inventory_date_key(),
        "part_number": {"$regex": f"^{re.escape(clean_part)}$", "$options": "i"},
    }
    product = await db.products.find_one(query, {"_id": 0}, sort=[("part_number", 1)])
    return product, clean_part


def build_perpetual_lookup_payload(
    product: dict, clean_part: str, brand_name: str, dealer_name: str, branch: str
) -> dict:
    pin = _product_pin_location(product)
    try:
        mav = float(
            product.get("mav")
            or product.get("MAV")
            or product.get("mav_value")
            or product.get("unit_value")
            or product.get("value")
            or 0
        )
    except (TypeError, ValueError):
        mav = 0.0
    return {
        "part_number": product.get("part_number") or clean_part,
        "part_name": resolve_product_part_name(product),
        "system_quantity": _mobile_stock_qty(product),
        "mav": mav,
        "pin_location": pin,
        "branch": product.get("branch") or branch,
        "brand": product.get("brand_name") or brand_name,
        "dealer": product.get("dealer_name") or dealer_name,
    }


def _mobile_stock_qty(product: dict) -> float:
    for field in ("available_qty_number", "available_quantity", "available_qty", "quantity"):
        value = (product or {}).get(field)
        if value not in (None, ""):
            try:
                return float(value)
            except (TypeError, ValueError):
                pass
    return 0.0


def _mobile_entry_method(value: str) -> str:
    normalized = re.sub(r"[^A-Z0-9]+", "_", str(value or "MANUAL").strip().upper()).strip("_")
    aliases = {"CAMERA": "CAMERA_OCR", "OCR": "CAMERA_OCR", "CAMERAOCR": "CAMERA_OCR", "MANUAL_ENTRY": "MANUAL"}
    normalized = aliases.get(normalized, normalized)
    if normalized not in {"MANUAL", "CAMERA_OCR"}:
        # Keep the current APK backward compatible: an old/unknown value must
        # not block offline verification uploads.
        normalized = "MANUAL"
    return normalized




async def _get_or_create_mobile_daily_verification_session(
    mobile_user_id: str,
    device_id: str,
    brand_name: str,
    dealer_name: str,
    branch: str,
    verification_type: str = "physical",
) -> str:
    """One daily session per mobile user + brand + dealer + branch + IST day + kind.

    Status transitions must not mint a new MOPS ID for the same daily identity.
    """
    india_now = _now().astimezone(ZoneInfo("Asia/Kolkata"))
    verification_date = india_now.strftime("%Y-%m-%d")
    date_key = india_now.strftime("%y%m%d")
    if (verification_type or "physical").lower() == "auto":
        from auto_perpetual import get_or_create_auto_daily_session

        return await get_or_create_auto_daily_session(
            db,
            mobile_user_id=mobile_user_id,
            brand_name=brand_name,
            dealer_name=dealer_name,
            branch=branch,
            device_id=device_id,
        )

    session_kind = "physical_perpetual"
    day_scope = {
        "session_kind": session_kind,
        "verification_date": verification_date,
        "mobile_user_id": mobile_user_id,
        "brand_id": brand_name,
        "dealer_id": dealer_name,
        "branch_id": branch,
    }
    existing = await db.stock_verification_sessions.find_one(
        day_scope, {"_id": 0, "session_id": 1, "status": 1}, sort=[("created_at", 1)]
    )
    if not existing:
        legacy_scope = {**day_scope, "session_kind": "mobile_daily"}
        existing = await db.stock_verification_sessions.find_one(
            legacy_scope, {"_id": 0, "session_id": 1, "status": 1}, sort=[("created_at", 1)]
        )
    if existing and existing.get("session_id"):
        updates = {
            "updated_at": _now(),
            "device_id": device_id,
            "session_kind": session_kind,
        }
        if existing.get("status") in (None, "", "PENDING", "COMPLETED", "submitted"):
            updates["status"] = "ACTIVE"
        await db.stock_verification_sessions.update_one(
            {"session_id": existing["session_id"]},
            {"$set": updates},
        )
        return existing["session_id"]

    counter = await db.counters.find_one_and_update(
        {"_id": f"mops_verification_session_{date_key}"},
        {"$inc": {"seq": 1}, "$setOnInsert": {"date_key": date_key}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    seq = int(counter.get("seq", 1))
    if seq > 9999:
        raise HTTPException(status_code=500, detail="Daily MOPS verification session serial exhausted")
    session_id = f"MOPS{date_key}{seq:04d}"
    now = _now()
    session_doc = {
        "id": str(uuid.uuid4()),
        "session_id": session_id,
        "session_kind": session_kind,
        "verification_type": "physical",
        "verification_date": verification_date,
        "mobile_user_id": mobile_user_id,
        "brand_id": brand_name,
        "dealer_id": dealer_name,
        "branch_id": branch,
        "brand_name": brand_name,
        "dealer_name": dealer_name,
        "branch": branch,
        "device_id": device_id,
        "status": "ACTIVE",
        "total_items": 0,
        "source": "MOBILE",
        "information_only": True,
        "affects_stock": False,
        "created_at": now,
        "updated_at": now,
    }
    try:
        await db.stock_verification_sessions.insert_one(session_doc)
    except DuplicateKeyError:
        raced = await db.stock_verification_sessions.find_one(
            day_scope, {"_id": 0, "session_id": 1}, sort=[("created_at", 1)]
        )
        if not raced:
            raced = await db.stock_verification_sessions.find_one(
                {**day_scope, "session_kind": "mobile_daily"},
                {"_id": 0, "session_id": 1},
                sort=[("created_at", 1)],
            )
        if raced and raced.get("session_id"):
            return raced["session_id"]
        raise
    return session_id


async def _mirror_mobile_verification_to_web(doc: dict):
    """Idempotently expose a mobile snapshot to website-only correction flows.

    Never invents an incomplete parent session — the get-or-create path must
    have already established the canonical daily session_id.
    """
    session_id = doc.get("session_id")
    if not session_id:
        raise HTTPException(500, "Verification session ID is missing")

    clean_doc = {k: v for k, v in dict(doc).items() if k != "_id"}
    await db.stock_verifications.update_one(
        {"id": clean_doc["id"]}, {"$setOnInsert": clean_doc}, upsert=True
    )

    # Recalculate exact totals rather than incrementing. This makes retries and
    # recovery after a partial write safe and prevents doubled session totals.
    totals = await db.stock_verifications.aggregate([
        {"$match": {"session_id": session_id}},
        {"$group": {
            "_id": None, "total_items": {"$sum": 1},
            "total_shortage_qty": {"$sum": {"$ifNull": ["$shortage_qty", 0]}},
            "total_shortage_value": {"$sum": {"$ifNull": ["$shortage_value", 0]}},
            "total_excess_qty": {"$sum": {"$ifNull": ["$excess_qty", 0]}},
            "total_excess_value": {"$sum": {"$ifNull": ["$excess_value", 0]}},
        }},
    ]).to_list(1)
    summary = totals[0] if totals else {
        "total_items": 0, "total_shortage_qty": 0, "total_shortage_value": 0,
        "total_excess_qty": 0, "total_excess_value": 0,
    }
    summary.pop("_id", None)
    result = await db.stock_verification_sessions.update_one(
        {"session_id": session_id},
        {"$set": {**summary, "updated_at": _now()}},
    )
    if result.matched_count == 0:
        logger.error(
            "Mirror skipped inventing orphan session for missing session_id=%s",
            session_id,
        )


async def _process_stock_verification_submit(payload: StockVerificationSubmit, session: dict) -> dict:
    """Core stock-verification save used by single + batch endpoints.

    Preserves one daily MOPS/AOPS session via
    `_get_or_create_mobile_daily_verification_session` and client_id idempotency.
    """
    # Information-only physical snapshot. Unknown/unlisted parts are valid and
    # must be visible in the website correction and Excel workflows.
    try:
        physical_qty = float(payload.physical_qty or 0)
    except (TypeError, ValueError):
        raise HTTPException(400, "Physical quantity must be a valid number")
    if physical_qty < 0:
        raise HTTPException(400, "Physical quantity cannot be negative")

    entry_method = _mobile_entry_method(payload.entry_method)
    device_id = session["device"]["device_id"]
    mobile_user_id = session["mobile_user"]["mobile_user_id"]
    now = _now()

    if payload.client_id:
        existing = await db.stock_verification_history.find_one(
            {"device_id": device_id, "client_id": payload.client_id}, {"_id": 0}
        )
        if existing:
            if not existing.get("session_id"):
                existing["session_id"] = await _get_or_create_mobile_daily_verification_session(
                    mobile_user_id,
                    device_id,
                    session["brand_name"],
                    session["dealer_name"],
                    session["branch"],
                    verification_type=payload.verification_type or "physical",
                )
            existing.setdefault("source", "MOBILE")
            existing.setdefault("information_only", True)
            existing.setdefault("affects_stock", False)
            await _mirror_mobile_verification_to_web(existing)
            return {
                "success": True, "message": "Verification recorded", "id": existing["id"],
                "verification_id": existing["id"], "duplicate": True,
                "session_id": existing.get("session_id"),
                "system_qty": existing.get("system_quantity", 0),
                "physical_qty": existing.get("physical_quantity", 0),
                "difference_qty": existing.get("difference", 0),
                "verification_status": existing.get("verification_status") or existing.get("quantity_status"),
                "part_found_in_system": existing.get("part_found_in_system", False),
            }

    session_id = await _get_or_create_mobile_daily_verification_session(
        mobile_user_id,
        device_id,
        session["brand_name"],
        session["dealer_name"],
        session["branch"],
        verification_type=payload.verification_type or "physical",
    )

    try:
        damage_qty = max(0.0, float(payload.damage_qty or 0))
    except (TypeError, ValueError):
        damage_qty = 0.0
    vtype = (payload.verification_type or "physical").lower()
    if vtype not in ("physical", "auto", "recheck"):
        vtype = "physical"

    system_record, clean_part = await find_scoped_product(
        payload.part_number, session["brand_name"], session["dealer_name"], session["branch"]
    )
    if not clean_part:
        raise HTTPException(400, "Part number is required")

    assignment = None
    coverage_kind = "monthly"
    if vtype == "auto":
        from auto_perpetual import ist_date_key

        assignment = await db.auto_perpetual_assignments.find_one(
            {
                "allocation_date": ist_date_key(),
                "mobile_user_id": mobile_user_id,
                "part_number": str(clean_part).strip().upper(),
                "status": "pending",
            },
            {"_id": 0},
        )
        if assignment:
            coverage_kind = assignment.get("coverage_kind") or "monthly"
    system_qty = _mobile_stock_qty(system_record)
    difference = physical_qty - system_qty
    part_found = bool(system_record)
    quantity_status = "matched" if difference == 0 else ("shortage" if difference < 0 else "excess")
    verification_status = "NEW_PART_FOUND" if (not part_found and physical_qty > 0) else quantity_status.upper()
    system_location = _product_pin_location(system_record or {})
    physical_location = str(payload.location or "").strip()
    location_status = "matched" if system_location.casefold() == physical_location.casefold() else "mismatch"
    if quantity_status == "matched" and location_status == "matched":
        overall_status = "matched"
    elif quantity_status != "matched" and location_status != "matched":
        overall_status = "quantity_and_location_mismatch"
    elif quantity_status != "matched":
        overall_status = "quantity_mismatch"
    else:
        overall_status = "location_mismatch"
    correction_status = "not_required" if overall_status == "matched" else "pending"
    try:
        mav = float((system_record or {}).get("mav") or (system_record or {}).get("MAV") or (system_record or {}).get("unit_value") or (system_record or {}).get("value") or 0)
    except (TypeError, ValueError):
        mav = 0.0
    shortage_qty = abs(difference) if difference < 0 else 0.0
    excess_qty = difference if difference > 0 else 0.0

    doc = {
        "id": str(uuid.uuid4()), "session_id": session_id, "client_id": payload.client_id,
        "part_number": (system_record or {}).get("part_number") or clean_part,
        "part_name": resolve_product_part_name(system_record or {}),
        "product_id": (system_record or {}).get("id"), "mav": mav,
        "system_quantity": system_qty, "physical_quantity": physical_qty, "difference": difference,
        "shortage_qty": shortage_qty, "excess_qty": excess_qty,
        "shortage_value": shortage_qty * mav, "excess_value": excess_qty * mav,
        "quantity_status": quantity_status, "verification_status": verification_status,
        "part_found_in_system": part_found, "is_new_part": not part_found,
        "system_location": system_location, "pin_location": system_location,
        "physical_location": physical_location, "scanned_location": physical_location,
        "location": physical_location, "location_status": location_status, "overall_status": overall_status,
        "remarks": payload.remark or "", "remark": payload.remark or "", "entry_method": entry_method,
        "verification_method": entry_method,
        "verified_user": session["mobile_user"]["name"], "verified_by": mobile_user_id,
        "verified_by_name": session["mobile_user"]["name"], "mobile_user_id": mobile_user_id,
        "device_id": device_id, "brand_name": session["brand_name"], "dealer_name": session["dealer_name"],
        "branch": session["branch"], "snapshot_at": now, "created_at": now, "verified_at": now,
        "status": "submitted", "correction_status": correction_status, "correction_method": "",
        "correction_remarks": "", "information_only": True, "affects_stock": False, "source": "MOBILE",
        "damage_qty": damage_qty,
        "verification_type": vtype if coverage_kind != "recheck" else "recheck",
        "coverage_kind": coverage_kind,
        "has_damage": damage_qty > 0,
        "assignment_id": (assignment or {}).get("id"),
        "suggestion_number": (assignment or {}).get("suggestion_number"),
        "batch_no": (assignment or {}).get("batch_no"),
        "suggestion_type": (assignment or {}).get("suggestion_type"),
        "qty_result": quantity_status.upper(),
        "location_result": "LOCATION MATCHED" if location_status == "matched" else "LOCATION MISMATCH",
        "final_result": overall_status.upper().replace("_", " "),
    }
    try:
        await db.stock_verification_history.insert_one(dict(doc))
    except DuplicateKeyError:
        existing = await db.stock_verification_history.find_one(
            {"device_id": device_id, "client_id": payload.client_id}, {"_id": 0}
        )
        if existing:
            existing.setdefault("session_id", session_id)
            await _mirror_mobile_verification_to_web(existing)
            return {
                "success": True, "message": "Verification recorded", "id": existing["id"],
                "verification_id": existing["id"], "duplicate": True,
                "system_qty": existing.get("system_quantity", 0),
                "physical_qty": existing.get("physical_quantity", 0),
                "difference_qty": existing.get("difference", 0),
                "verification_status": existing.get("verification_status") or existing.get("quantity_status"),
                "part_found_in_system": existing.get("part_found_in_system", False),
            }
        raise

    await _mirror_mobile_verification_to_web(doc)
    from auto_perpetual import month_key, ist_date_key

    part_key = str(doc["part_number"]).strip().upper()
    if coverage_kind == "monthly" and vtype in ("auto", "physical"):
        await db.auto_perpetual_pool.update_one(
            {
                "month_key": month_key(),
                "branch": session["branch"],
                "part_number": part_key,
                "coverage_kind": "monthly",
            },
            {
                "$set": {
                    "status": "verified",
                    "verified_at": now,
                    "verified_by_mobile_user_id": mobile_user_id,
                    "verified_by_name": session["mobile_user"]["name"],
                    "updated_at": now,
                }
            },
        )
    elif coverage_kind == "recheck":
        await db.auto_perpetual_pool.update_one(
            {
                "month_key": month_key(),
                "branch": session["branch"],
                "part_number": part_key,
                "coverage_kind": "recheck",
            },
            {
                "$set": {
                    "status": "verified",
                    "verified_at": now,
                    "verified_by_mobile_user_id": mobile_user_id,
                    "verified_by_name": session["mobile_user"]["name"],
                    "updated_at": now,
                }
            },
        )
    if vtype == "auto":
        await db.auto_perpetual_assignments.update_many(
            {
                "allocation_date": ist_date_key(),
                "mobile_user_id": mobile_user_id,
                "part_number": part_key,
            },
            {"$set": {"status": "completed", "completed_at": now, "verified_by_mobile_user_id": mobile_user_id}},
        )
        sid = (assignment or {}).get("suggestion_id")
        if sid:
            from auto_perpetual_suggestions import refresh_suggestion_status

            await refresh_suggestion_status(db, sid)
        # Mark work started, but get_or_create_auto_daily_session must still
        # reuse this same session_id for every later part on the IST day.
        await db.stock_verification_sessions.update_one(
            {"session_id": session_id},
            {"$set": {"status": "IN_PROGRESS", "updated_at": now}},
        )
        await db.stock_verification_history.update_one(
            {"id": doc["id"]},
            {"$set": {"month_key": month_key()}},
        )
    return {
        "success": True, "message": "Verification recorded", "id": doc["id"], "verification_id": doc["id"],
        "session_id": session_id,
        "duplicate": False, "system_qty": system_qty, "physical_qty": physical_qty,
        "difference_qty": difference, "verification_status": verification_status,
        "part_found_in_system": part_found,
    }


@router.post("/stock-verification")
async def submit_stock_verification(payload: StockVerificationSubmit, session=Depends(get_device_session)):
    return await _process_stock_verification_submit(payload, session)


@router.post("/stock-verification/batch")
@router.post("/stock-verification/batch/", include_in_schema=False)
async def submit_stock_verification_batch(
    payload: StockVerificationBatchSubmit,
    session=Depends(get_device_session),
):
    """Bulk offline-queue sync — reuses the same single-item save/session logic."""
    results = []
    synced = 0
    failed = 0
    for item in payload.items or []:
        try:
            result = await _process_stock_verification_submit(item, session)
            results.append(
                {
                    "client_id": item.client_id,
                    "success": True,
                    "id": result.get("id") or result.get("verification_id"),
                    "verification_id": result.get("verification_id") or result.get("id"),
                    "duplicate": bool(result.get("duplicate")),
                    "session_id": result.get("session_id"),
                    "system_qty": result.get("system_qty"),
                    "physical_qty": result.get("physical_qty"),
                    "difference_qty": result.get("difference_qty"),
                    "verification_status": result.get("verification_status"),
                    "part_found_in_system": result.get("part_found_in_system"),
                    "message": result.get("message"),
                }
            )
            synced += 1
        except HTTPException as exc:
            detail = exc.detail
            if not isinstance(detail, str):
                detail = str(detail)
            results.append(
                {
                    "client_id": item.client_id,
                    "success": False,
                    "error": detail,
                    "message": detail,
                }
            )
            failed += 1
        except Exception as exc:  # noqa: BLE001 — per-item isolation for offline sync
            results.append(
                {
                    "client_id": item.client_id,
                    "success": False,
                    "error": str(exc),
                    "message": str(exc),
                }
            )
            failed += 1
    return {
        "success": failed == 0,
        "synced": synced,
        "failed": failed,
        "results": results,
    }


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


# ==================== PERPETUAL STOCK LOOKUP (MOBILE DEVICE) ====================

@router.get("/perpetual-stock/device-lookup")
async def perpetual_stock_device_lookup(part_number: str, session=Depends(get_device_session)):
    """Perpetual Stock part snapshot for paired devices. Scope comes only from the device session."""
    product, clean_part = await find_scoped_product(
        part_number, session["brand_name"], session["dealer_name"], session["branch"]
    )
    if not clean_part:
        raise HTTPException(status_code=400, detail="Part number is required")
    if not product:
        raise HTTPException(
            status_code=404,
            detail=f"Part {clean_part} was not found under your paired Brand / Dealer / Branch",
        )
    return build_perpetual_lookup_payload(
        product, clean_part, session["brand_name"], session["dealer_name"], session["branch"]
    )


# ==================== STOCK SEARCH (MOBILE) ====================

def _product_hub_stock_scope(session: dict, today_key: str) -> dict:
    """Same current/active Product Hub filter used by `/product-hub/records`.

    Mirrors server._product_hub_active_query for the paired Brand/Dealer/Branch:
    publish_status=Published + is_active_today=True + active_date_key=IST today.
    Never falls back to history / previous-day / verification data.
    """
    return {
        "brand_name": session["brand_name"],
        "dealer_name": session["dealer_name"],
        "branch": session["branch"],
        "publish_status": "Published",
        "is_active_today": True,
        "active_date_key": today_key,
    }


async def _stock_search_product_hub_scope(session: dict):
    """Authoritative Stock Availability scope = current Product Hub dataset."""
    from auto_perpetual import inventory_date_key

    today_key = inventory_date_key()  # YYYYMMDD in Asia/Kolkata (same as _nmts_date_key)
    scope = _product_hub_stock_scope(session, today_key)
    available = await db.products.find_one(scope, {"_id": 1}) is not None
    message = "" if available else "No stock uploaded today for this branch."
    return scope, today_key, available, message


def _dedupe_product_hub_rows(rows: list) -> list:
    """ONE current Product Hub part_number => ONE Mobile result card."""
    seen = set()
    unique = []
    for row in rows or []:
        key = str((row or {}).get("part_number") or "").strip().upper()
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def _enrich_product_hub_stock_row(row: dict) -> dict:
    """Attach the same aging fields Product Hub computes on read (no qty recalculation)."""
    if not row:
        return row
    try:
        # Lazy import avoids circular import at module load (server imports mobile_api).
        from server import _order_stock_purchase_aging_days, _order_stock_sales_aging_days

        row["purchase_aging_days"] = row.get("purchase_aging_days", _order_stock_purchase_aging_days(row))
        row["sales_aging_days"] = row.get("sales_aging_days", _order_stock_sales_aging_days(row))
        row["purchase_aging"] = row["purchase_aging_days"]
        row["sales_aging"] = row["sales_aging_days"]
    except Exception:
        # Keep search working even if aging helpers are unavailable.
        pass
    # Prefer Product Hub numeric on-hand for Mobile mapping without changing stored data.
    qty = row.get("available_qty_number")
    if qty not in (None, "") and row.get("available_qty") in (None, ""):
        row["available_qty"] = qty
    return row


@router.get("/stock-search")
async def stock_search(
    part_numbers: Optional[str] = Query(
        None,
        description="Newline/comma-separated exact part numbers (Multiple Part Search). Optional for Single Search.",
    ),
    q: Optional[str] = Query(
        None,
        description="Single-search query. With mode=prefix, matches part-number prefix / description. part_numbers NOT required.",
    ),
    mode: str = Query(
        "exact",
        description="exact = Multiple/exact part_numbers; prefix = single Stock Availability partial search.",
    ),
    limit: int = Query(100, ge=1, le=500),
    session=Depends(get_device_session),
):
    """Stock Availability search against current Product Hub data only.

    - mode=prefix + q: Single Search (exact or prefix). part_numbers is NOT required
      (missing part_numbers must never yield HTTP 422).
    - mode=exact + part_numbers: Multiple Part Search (exact matches only).
    Scope matches Product Hub current/active records for the paired Brand/Dealer/Branch.
    """
    scope, today_key, today_available, unavailable_message = await _stock_search_product_hub_scope(session)
    base_meta = {
        "inventory_date": today_key,
        "inventory_date_ist": f"{today_key[0:4]}-{today_key[4:6]}-{today_key[6:8]}",
        "today_upload_available": today_available,
        "message": unavailable_message,
    }

    search_mode = (mode or "exact").strip().lower()
    query_text = (q or "").strip()
    # Single Search: mode=prefix + q (or q alone). Never require part_numbers.
    use_prefix = search_mode == "prefix" or (bool(query_text) and not (part_numbers or "").strip())

    if not today_available:
        # Never fall back to yesterday / previous upload / history.
        if use_prefix:
            return {
                **base_meta,
                "results": [],
                "not_found": [],
                "mode": "prefix",
                "query": query_text or (part_numbers or "").strip(),
            }
        parts = [p.strip() for p in re.split(r"[,\n;\s]+", part_numbers or "") if p.strip()]
        return {
            **base_meta,
            "results": [],
            "not_found": parts,
            "mode": "exact",
        }

    if use_prefix:
        needle = query_text or (part_numbers or "").strip()
        if not needle:
            raise HTTPException(400, "Provide a search query")
        safe = re.escape(needle)
        query = {
            **scope,
            "$or": [
                {"part_number": {"$regex": f"^{safe}", "$options": "i"}},
                {"part_name": {"$regex": safe, "$options": "i"}},
                {"item_name": {"$regex": safe, "$options": "i"}},
                {"description": {"$regex": safe, "$options": "i"}},
            ],
        }
        # Fetch a bit extra before dedupe so prefix caps stay meaningful.
        fetch_cap = max(1, min(int(limit or 100) * 2, 500))
        rows = await db.products.find(query, {"_id": 0}).sort("part_number", 1).limit(fetch_cap).to_list(fetch_cap)
        rows = _dedupe_product_hub_rows(rows)
        upper = needle.upper()
        rows.sort(
            key=lambda r: (
                0 if str(r.get("part_number") or "").upper().startswith(upper) else 1,
                str(r.get("part_number") or "").upper(),
            )
        )
        rows = [_enrich_product_hub_stock_row(r) for r in rows[: max(1, min(int(limit or 100), 500))]]
        return {
            **base_meta,
            "results": rows,
            "not_found": [],
            "mode": "prefix",
            "query": needle,
        }

    parts = [p.strip() for p in re.split(r"[,\n;\s]+", part_numbers or "") if p.strip()]
    if not parts:
        # Exact mode without part_numbers is a client contract mistake — 400, never 422.
        raise HTTPException(400, "Provide at least one part number")

    # Exact match only within current Product Hub scope — do not apply prefix logic here.
    # Include upper/lower variants so Mobile-normalized inputs still match stored casing.
    part_candidates = list({p for p in parts} | {p.upper() for p in parts} | {p.lower() for p in parts})
    query = {**scope, "part_number": {"$in": part_candidates}}
    rows = await db.products.find(query, {"_id": 0}).to_list(2000)
    rows = [_enrich_product_hub_stock_row(r) for r in _dedupe_product_hub_rows(rows)]
    found_upper = {str(r.get("part_number") or "").strip().upper() for r in rows}
    not_found = []
    seen_nf = set()
    for p in parts:
        up = p.upper()
        if up in found_upper or up in seen_nf:
            continue
        not_found.append(p)
        seen_nf.add(up)
    return {**base_meta, "results": rows, "not_found": not_found, "mode": "exact"}


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


# ==================== DAILY SESSION CLEANUP ====================

async def cleanup_duplicate_daily_verification_sessions(database=None) -> dict:
    """Idempotent: one parent session per kind+user+brand+dealer+branch+IST day.

    Canonical strategy: earliest created_at (then session_id) wins.
    Part-level history / stock_verifications rows are re-pointed, never deleted.
    """
    coll_db = database if database is not None else db
    stats = {
        "duplicate_aops_groups": 0,
        "duplicate_mops_groups": 0,
        "orphan_sessions_removed": 0,
        "history_rows_repointed": 0,
        "mirror_rows_repointed": 0,
        "duplicate_sessions_removed": 0,
        "groups_merged": 0,
    }

    pipeline = [
        {
            "$match": {
                "session_kind": {"$in": ["auto_perpetual", "physical_perpetual", "mobile_daily"]},
                "mobile_user_id": {"$type": "string", "$ne": ""},
                "brand_id": {"$type": "string", "$ne": ""},
                "dealer_id": {"$type": "string", "$ne": ""},
                "branch_id": {"$type": "string", "$ne": ""},
                "verification_date": {"$type": "string", "$ne": ""},
            }
        },
        {
            "$group": {
                "_id": {
                    "session_kind": "$session_kind",
                    "verification_date": "$verification_date",
                    "mobile_user_id": "$mobile_user_id",
                    "brand_id": "$brand_id",
                    "dealer_id": "$dealer_id",
                    "branch_id": "$branch_id",
                },
                "sessions": {
                    "$push": {
                        "session_id": "$session_id",
                        "created_at": "$created_at",
                        "status": "$status",
                    }
                },
                "count": {"$sum": 1},
            }
        },
        {"$match": {"count": {"$gt": 1}}},
    ]
    groups = await coll_db.stock_verification_sessions.aggregate(pipeline).to_list(100000)
    for group in groups:
        kind = (group.get("_id") or {}).get("session_kind")
        if kind == "auto_perpetual":
            stats["duplicate_aops_groups"] += 1
        elif kind in ("physical_perpetual", "mobile_daily"):
            stats["duplicate_mops_groups"] += 1

        sessions = list(group.get("sessions") or [])

        def _session_sort_key(s):
            ca = s.get("created_at")
            if ca is None:
                return (1, "", str(s.get("session_id") or ""))
            if hasattr(ca, "isoformat"):
                return (0, ca.isoformat(), str(s.get("session_id") or ""))
            return (0, str(ca), str(s.get("session_id") or ""))

        sessions.sort(key=_session_sort_key)
        canonical = (sessions[0] or {}).get("session_id")
        if not canonical:
            continue
        duplicates = [s.get("session_id") for s in sessions[1:] if s.get("session_id") and s.get("session_id") != canonical]
        if not duplicates:
            continue
        stats["groups_merged"] += 1

        hist = await coll_db.stock_verification_history.update_many(
            {"session_id": {"$in": duplicates}},
            {"$set": {"session_id": canonical}},
        )
        stats["history_rows_repointed"] += int(hist.modified_count or 0)
        mir = await coll_db.stock_verifications.update_many(
            {"session_id": {"$in": duplicates}},
            {"$set": {"session_id": canonical}},
        )
        stats["mirror_rows_repointed"] += int(mir.modified_count or 0)

        totals = await coll_db.stock_verifications.aggregate([
            {"$match": {"session_id": canonical}},
            {"$group": {
                "_id": None,
                "total_items": {"$sum": 1},
                "total_shortage_qty": {"$sum": {"$ifNull": ["$shortage_qty", 0]}},
                "total_shortage_value": {"$sum": {"$ifNull": ["$shortage_value", 0]}},
                "total_excess_qty": {"$sum": {"$ifNull": ["$excess_qty", 0]}},
                "total_excess_value": {"$sum": {"$ifNull": ["$excess_value", 0]}},
            }},
        ]).to_list(1)
        summary = totals[0] if totals else {
            "total_items": 0, "total_shortage_qty": 0, "total_shortage_value": 0,
            "total_excess_qty": 0, "total_excess_value": 0,
        }
        summary.pop("_id", None)
        # Prefer an in-progress/active status from the group when present.
        status_rank = {"IN_PROGRESS": 0, "ACTIVE": 1, "PENDING": 2, "COMPLETED": 3, "submitted": 4}
        best_status = min(
            (s.get("status") or "ACTIVE" for s in sessions),
            key=lambda st: status_rank.get(st, 50),
        )
        await coll_db.stock_verification_sessions.update_one(
            {"session_id": canonical},
            {"$set": {**summary, "status": best_status, "updated_at": _now()}},
        )
        deleted = await coll_db.stock_verification_sessions.delete_many(
            {"session_id": {"$in": duplicates}}
        )
        stats["duplicate_sessions_removed"] += int(deleted.deleted_count or 0)

    # Incomplete/orphan parents: missing identity fields and unused by history/mirror.
    orphan_rows = await coll_db.stock_verification_sessions.find(
        {
            "$or": [
                {"session_kind": {"$exists": False}},
                {"session_kind": None},
                {"session_kind": ""},
                {"mobile_user_id": {"$exists": False}},
                {"mobile_user_id": None},
                {"mobile_user_id": ""},
                {"brand_id": {"$exists": False}},
                {"brand_id": None},
                {"brand_id": ""},
            ]
        },
        {"_id": 0, "session_id": 1},
    ).to_list(100000)
    orphan_ids = []
    for row in orphan_rows:
        sid = row.get("session_id")
        if not sid:
            continue
        still_used = await coll_db.stock_verification_history.find_one({"session_id": sid}, {"_id": 1})
        if still_used:
            continue
        still_used = await coll_db.stock_verifications.find_one({"session_id": sid}, {"_id": 1})
        if still_used:
            continue
        orphan_ids.append(sid)
    if orphan_ids:
        removed = await coll_db.stock_verification_sessions.delete_many({"session_id": {"$in": orphan_ids}})
        stats["orphan_sessions_removed"] += int(removed.deleted_count or 0)

    return stats


# ==================== INDEXES ====================

async def ensure_mobile_indexes():
    from auto_perpetual import ensure_auto_perpetual_indexes

    await ensure_auto_perpetual_indexes(db)
    await db.mobile_users.create_index([("mobile_user_id", 1)], unique=True)
    await db.mobile_users.create_index([("mobile_number", 1)])
    # Backfill normalized numbers for older records before enforcing uniqueness.
    legacy_users = await db.mobile_users.find(
        {"$or": [{"normalized_mobile_number": {"$exists": False}}, {"normalized_mobile_number": ""}]},
        {"_id": 1, "mobile_number": 1},
    ).to_list(100000)
    for legacy_user in legacy_users:
        try:
            normalized = _normalize_mobile_number(legacy_user.get("mobile_number", ""))
            duplicate = await db.mobile_users.find_one({"normalized_mobile_number": normalized, "_id": {"$ne": legacy_user["_id"]}}, {"_id": 1})
            if duplicate:
                logger.error("Legacy duplicate mobile number detected: %s", normalized)
                continue
            await db.mobile_users.update_one({"_id": legacy_user["_id"]}, {"$set": {"normalized_mobile_number": normalized, "mobile_number": normalized}})
        except HTTPException:
            logger.warning("Legacy mobile user has an invalid mobile number: %s", legacy_user.get("_id"))
    try:
        await db.mobile_users.create_index([("normalized_mobile_number", 1)], unique=True, sparse=True, name="uq_mobile_number")
    except (DuplicateKeyError, OperationFailure) as exc:
        logger.error("Cannot create unique mobile-number index: %s", exc)
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
    try:
        await db.mobile_pairing_codes.create_index(
            [("pairing_token_hash", 1)],
            unique=True,
            name="uq_active_pairing_token_hash",
            partialFilterExpression={"used": False, "pairing_token_hash": {"$type": "string"}},
        )
    except (DuplicateKeyError, OperationFailure) as exc:
        logger.warning("Active pairing token index not created: %s", exc)

    await db.mobile_notification_actions.create_index([("request_id", 1), ("mobile_user_id", 1)], unique=True)

    await db.mobile_request_group_locks.create_index([("request_group_key", 1)], unique=True)

    await db.mobile_app_versions.create_index([("version_code", 1)], unique=True)

    await db.stock_verification_history.create_index([("part_number", 1)])
    await db.stock_verification_history.create_index([("verified_at", -1)])
    await db.stock_verification_history.create_index(
        [("brand_name", 1), ("dealer_name", 1), ("branch", 1), ("verified_at", -1)]
    )
    try:
        await db.stock_verification_history.create_index(
            [("device_id", 1), ("client_id", 1)],
            unique=True,
            partialFilterExpression={"client_id": {"$type": "string"}},
            name="uq_stock_verification_history_device_client",
        )
    except (DuplicateKeyError, OperationFailure) as exc:
        logger.error("Cannot create stock verification history device/client index: %s", exc)

    # Collapse duplicate daily parents before enforcing uniqueness.
    try:
        cleanup_stats = await cleanup_duplicate_daily_verification_sessions(db)
        logger.info("Daily verification session cleanup: %s", cleanup_stats)
    except Exception as exc:
        logger.error("Daily verification session cleanup failed: %s", exc)

    for stale_index in (
        "uq_mobile_daily_verification_session",
        "uq_auto_perpetual_daily_session",
        "uq_daily_verification_session_identity",
    ):
        try:
            await db.stock_verification_sessions.drop_index(stale_index)
        except OperationFailure:
            pass

    try:
        # One parent per kind + user + brand + dealer + branch + IST day.
        # Status is intentionally NOT part of uniqueness (status flips must reuse).
        await db.stock_verification_sessions.create_index(
            [
                ("session_kind", 1),
                ("verification_date", 1),
                ("mobile_user_id", 1),
                ("brand_id", 1),
                ("dealer_id", 1),
                ("branch_id", 1),
            ],
            unique=True,
            name="uq_daily_verification_session_identity",
            partialFilterExpression={
                "session_kind": {"$in": ["auto_perpetual", "physical_perpetual", "mobile_daily"]},
                "mobile_user_id": {"$type": "string"},
                "brand_id": {"$type": "string"},
                "dealer_id": {"$type": "string"},
                "branch_id": {"$type": "string"},
                "verification_date": {"$type": "string"},
            },
        )
    except (DuplicateKeyError, OperationFailure) as exc:
        logger.error("Cannot create daily verification session unique index: %s", exc)

    # Scoped part_number index for efficient mobile prefix stock-search (IST today key).
    try:
        await db.products.create_index(
            [
                ("brand_name", 1),
                ("dealer_name", 1),
                ("branch", 1),
                ("publish_status", 1),
                ("active_date_key", 1),
                ("part_number", 1),
            ],
            name="idx_mobile_stock_search_today_part",
        )
    except (DuplicateKeyError, OperationFailure) as exc:
        logger.error("Cannot create mobile stock-search product index: %s", exc)

    await db.mobile_audit_logs.create_index([("created_at", -1)])
    await db.mobile_audit_logs.create_index([("target", 1)])

    logger.info("Sleeping Stock Mobile indexes verified")
