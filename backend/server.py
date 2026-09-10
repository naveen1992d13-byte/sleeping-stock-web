"""
Backend server for NMTS / Sleeping Stock web application.
Handles authentication, product management, order desk, and more.
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set
from http.client import HTTPException as HTTPClientException

from fastapi import FastAPI, Depends, HTTPException, status, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from passlib.context import CryptContext
from pydantic import BaseModel, Field, ConfigDict, field_validator
import jwt
import requests
import socketio
from bson import ObjectId
import logging.config
import boto3
from botocore.exceptions import ClientError

# Import shared modules
import part_category as pc
from notifications import send_request_pdf_email
from email_format import build_request_email_subject, build_request_email_body

# ==================== CONFIG & INIT ====================

logger = logging.getLogger(__name__)
logging.getLogger("motor").setLevel(logging.WARNING)

SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "10080"))

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer()

# FastAPI setup
app = FastAPI(title="Sleeping Stock API", version="1.0.0")
api_router = app.router

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Socket.IO
sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins="*",
    ping_timeout=60,
    ping_interval=25,
)
socket_app = socketio.ASGIApp(sio, app)

# MongoDB
MONGO_URL = os.getenv("MONGO_URL", "mongodb://localhost:27017")
mongo_client: AsyncIOMotorClient = None
db: AsyncIOMotorDatabase = None

async def connect_to_mongo():
    global mongo_client, db
    mongo_client = AsyncIOMotorClient(MONGO_URL)
    db = mongo_client["sleeping_stock"]
    logger.info("Connected to MongoDB")

async def close_mongo():
    if mongo_client:
        mongo_client.close()
        logger.info("Closed MongoDB connection")

app.add_event_handler("startup", connect_to_mongo)
app.add_event_handler("shutdown", close_mongo)

# S3
REAL_S3 = os.getenv("REAL_S3", "false").lower() == "true"
S3_BUCKET = os.getenv("S3_BUCKET", "sleeping-stock-uploads")
S3_REGION = os.getenv("S3_REGION", "us-east-1")

try:
    if REAL_S3:
        s3_client = boto3.client("s3", region_name=S3_REGION)
        s3_client.head_bucket(Bucket=S3_BUCKET)
        logger.info("Object storage: backend=REAL S3 real_s3=True")
    else:
        s3_client = None
        logger.info("Object storage: backend=LOCAL FALLBACK real_s3=False")
except Exception as e:
    s3_client = None
    logger.warning(f"S3 check failed, using local fallback: {e}")

# ==================== MODELS ====================

class User(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    username: str
    email: str
    hashed_password: str
    role: str  # master, admin, user
    brand: str = "All Brands"
    group: str = "All Dealers"
    location: str = "All Branches"
    is_active: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class UserCreate(BaseModel):
    username: str
    email: str
    password: str
    role: str
    brand: str = "All Brands"
    group: str = "All Dealers"
    location: str = "All Branches"

    @field_validator("role")
    @classmethod
    def validate_role(cls, v):
        if v not in ["master", "admin", "user"]:
            raise ValueError("Invalid role")
        return v

class UserUpdate(BaseModel):
    email: Optional[str] = None
    role: Optional[str] = None
    brand: Optional[str] = None
    group: Optional[str] = None
    location: Optional[str] = None
    is_active: Optional[bool] = None

class PasswordChange(BaseModel):
    old_password: str
    new_password: str

class ActivityLog(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    action: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    is_read: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class UploadLog(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    file_name: str
    uploaded_by: str
    status: str
    rows_processed: int
    rows_imported: int
    errors: List[str] = []
    upload_date: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

# ==================== AUTH HELPERS ====================

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        token = credentials.credentials
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except jwt.JWTError:
        raise credentials_exception
    
    user = await db.users.find_one({"id": user_id}, {"_id": 0})
    if user is None:
        raise credentials_exception
    return UserResponse(**user)

# ==================== SOCKET.IO ====================

connected_users = {}  # {user_id: sid}

@sio.event
async def connect(sid, environ):
    logging.info(f"Client connected: {sid}")

@sio.event
async def register(sid, data):
    user_id = data.get("user_id")
    if user_id:
        connected_users[user_id] = sid
        logging.info(f"User {user_id} registered with socket {sid}")

# ==================== PRODUCT HUB HELPERS ====================

def _nmts_date_key() -> str:
    """Return today's date in YYYYMMDD format (for Product Hub active status)."""
    return datetime.now(timezone.utc).strftime("%Y%m%d")

def _is_all_scope(v: Optional[str]) -> bool:
    """Check if value is an 'All' scope marker."""
    if not v:
        return True
    return str(v).startswith("All ")

def _is_all_part_type(v: Optional[str]) -> bool:
    """Check if Part Type / category is 'All' or equivalent."""
    if not v:
        return True
    s = str(v).strip()
    return s in ("All", "All Categories", "All Part Types")

def _apply_role_scope_v2(query: dict, current_user: "UserResponse", brand: Optional[str] = None, dealer: Optional[str] = None, branch: Optional[str] = None):
    """Apply user role-based scope to a Mongo query (modifies in place)."""
    # Brand scope
    if not _is_all_scope(brand):
        query["brand_name"] = brand
    elif current_user.role != "master":
        query["brand_name"] = current_user.brand
    
    # Dealer/Group scope
    if not _is_all_scope(dealer):
        query["dealer_name"] = dealer
    elif current_user.role != "master":
        query["dealer_name"] = current_user.group
    
    # Branch scope
    if not _is_all_scope(branch):
        query["branch"] = branch
    elif current_user.role == "user":
        query["branch"] = current_user.location

def _apply_category_filter(query: dict, category: Optional[str]):
    """Filter query by Part Type / category (supports normalized aliases)."""
    if _is_all_part_type(category):
        return
    normalized = pc.normalize_part_type(category)
    if normalized:
        query["normalized_part_type"] = normalized

def _apply_stock_status_filter(query: dict, stock_status: Optional[str]):
    """Filter query by stock availability."""
    if not stock_status or stock_status == "all":
        return
    if stock_status == "available":
        query["available_qty_number"] = {"$gt": 0}
    elif stock_status == "zero":
        query["available_qty_number"] = {"$lte": 0}

def _product_hub_active_query(current_user: "UserResponse", brand: Optional[str] = None, dealer: Optional[str] = None, branch: Optional[str] = None) -> dict:
    """Build base Product Hub query (Published + Active today)."""
    query = {"publish_status": "Published", "is_active_today": True, "active_date_key": _nmts_date_key()}
    _apply_role_scope_v2(query, current_user, brand, dealer, branch)
    return query

class UserResponse(BaseModel):
    """Response model for user (without password)."""
    model_config = ConfigDict(extra="ignore")
    id: str
    username: str
    email: str
    role: str
    brand: str
    group: str
    location: str
    is_active: bool
    created_at: datetime

# ==================== PRODUCT ROUTES ====================

@api_router.get("/products")
async def get_products(current_user: UserResponse = Depends(get_current_user)):
    """Get all products."""
    products = await db.products.find({}, {"_id": 0}).to_list(1000)
    return products

@api_router.get("/products/low-stock")
async def get_low_stock(threshold: int = 10, current_user: UserResponse = Depends(get_current_user)):
    """Get low-stock products."""
    products = await db.products.find({"quantity": {"$lte": threshold}}, {"_id": 0}).to_list(1000)
    return products

@api_router.get("/products/sleeping-stock")
async def get_sleeping_stock(days: int = 90, current_user: UserResponse = Depends(get_current_user)):
    """Get products with no activity in X days."""
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)
    all_products = await db.products.find({}, {"_id": 0}).to_list(1000)
    
    sleeping = []
    for product in all_products:
        recent_order = await db.orders.find_one({
            "product_id": product["id"],
            "created_at": {"$gte": cutoff_date.isoformat()}
        })
        if not recent_order:
            sleeping.append(product)
    
    return sleeping

@api_router.post("/products")
async def create_product(product_data: dict, current_user: UserResponse = Depends(get_current_user)):
    """Create a new product."""
    if current_user.role not in ["master", "admin"]:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    product_data["id"] = str(uuid.uuid4())
    product_data["created_at"] = datetime.now(timezone.utc).isoformat()
    
    result = await db.products.insert_one(product_data)
    return {"id": str(result.inserted_id)}

@api_router.get("/products/active")
async def get_active_products(
    brand: Optional[str] = None,
    dealer: Optional[str] = None,
    branch: Optional[str] = None,
    current_user: UserResponse = Depends(get_current_user)
):
    """Get active products (for Product Hub and Analytics)."""
    query = _product_hub_active_query(current_user, brand, dealer, branch)
    products = await db.products.find(query, {"_id": 0}).to_list(10000)
    return products

# ==================== PRODUCT HUB ROUTES ====================

@api_router.get("/product-hub/part-types")
async def product_hub_part_types(
    brand: Optional[str] = None,
    dealer: Optional[str] = None,
    branch: Optional[str] = None,
    search: Optional[str] = None,
    stock_status: Optional[str] = None,
    current_user: UserResponse = Depends(get_current_user)
) -> dict:
    """
    Get distinct normalized Part Types present in scoped Product Hub data.
    Returns: { "part_types": ["All", "OE Parts", "Accessories", "Others"], "available_types": [...] }
    where available_types excludes "All" and only includes types found in the current scope.
    Respects Brand/Dealer/Branch scope and role-based access.
    """
    query = _product_hub_active_query(current_user, brand, dealer, branch)
    _apply_stock_status_filter(query, stock_status)
    
    if search:
        safe_search = re.escape(search.strip())
        search_clause = {
            "$or": [
                {"part_number": {"$regex": safe_search, "$options": "i"}},
                {"item_name": {"$regex": safe_search, "$options": "i"}},
            ]
        }
        if query:
            query["$and"] = [query, search_clause]
        else:
            query.update(search_clause)
    
    # Aggregate to get distinct normalized part types
    pipeline = [
        {"$match": query},
        {"$group": {
            "_id": "$normalized_part_type",
        }},
        {"$sort": {"_id": 1}},
    ]
    
    results = await db.products.aggregate(pipeline, allowDiskUse=True).to_list(None)
    found_types = sorted({r["_id"] for r in results if r.get("_id")})
    
    # Normalize to canonical labels
    available_types = []
    for raw_type in found_types:
        normalized = pc.normalize_part_type(raw_type)
        if normalized and normalized not in available_types:
            available_types.append(normalized)
    
    # Always include "All" at the start
    all_types = ["All"] + sorted(available_types)
    
    return {
        "part_types": all_types,
        "available_types": sorted(available_types),
    }

@api_router.get("/product-hub/summary")
async def product_hub_summary(
    brand: Optional[str] = None,
    dealer: Optional[str] = None,
    branch: Optional[str] = None,
    search: Optional[str] = None,
    category: Optional[str] = None,
    stock_status: Optional[str] = None,
    current_user: UserResponse = Depends(get_current_user)
) -> dict:
    """
    Fast summary cards: Total Item, Total Available Item, Total Available Quantity, Total Value.
    """
    date_key = _nmts_date_key()
    search = (search or "").strip()
    needs_product_scan = bool(search) or (not _is_all_part_type(category)) or (
        (stock_status or "all").strip().lower() not in {"", "all"}
    )
    
    if not needs_product_scan:
        batch_query = {"active_date_key": date_key}
        if not _is_all_scope(brand):
            batch_query["brand_name"] = brand
        elif current_user.role != "master":
            batch_query["brand_name"] = current_user.brand
        if not _is_all_scope(dealer):
            batch_query["dealer_name"] = dealer
        elif current_user.role != "master":
            batch_query["dealer_name"] = current_user.group
        if not _is_all_scope(branch):
            batch_query["branch"] = branch
        elif current_user.role == "user":
            batch_query["branch"] = current_user.location
        
        rows = await db.batch_summaries.find(batch_query, {"_id": 0}).to_list(10000)
        summary = {
            "totalItem": sum(int(r.get("total_item", 0)) for r in rows),
            "totalAvailableItem": sum(int(r.get("available_item", 0)) for r in rows),
            "totalAvailableQty": sum(float(r.get("available_qty", 0)) for r in rows),
            "totalValue": sum(float(r.get("total_value", 0)) for r in rows),
        }
        if rows and (summary["totalItem"] > 0 or summary["totalAvailableQty"] > 0 or summary["totalValue"] > 0):
            return summary
        needs_product_scan = True
    
    query = _product_hub_active_query(current_user, brand, dealer, branch)
    _apply_category_filter(query, category)
    _apply_stock_status_filter(query, stock_status)
    
    if search:
        safe_search = re.escape(search)
        search_clause = {
            "$or": [
                {"part_number": {"$regex": safe_search, "$options": "i"}},
                {"item_name": {"$regex": safe_search, "$options": "i"}},
            ]
        }
        if "$and" in query:
            query["$and"].append(search_clause)
        elif any(k.startswith("$") for k in query.keys()):
            existing = {k: v for k, v in list(query.items())}
            query.clear()
            query["$and"] = [existing, search_clause]
        else:
            query.update(search_clause)
    
    pipeline = [
        {"$match": query},
        {"$group": {
            "_id": None,
            "total_item": {"$sum": 1},
            "total_available_item": {"$sum": {"$cond": [{"$gt": [{"$toDouble": {"$ifNull": ["$available_qty_number", 0]}}, 0]}, 1, 0]}},
            "total_available_qty": {"$sum": {"$toDouble": {"$ifNull": ["$available_qty_number", 0]}}},
            "total_value": {"$sum": {"$toDouble": {"$ifNull": ["$total_value_number", 0]}}},
        }},
    ]
    
    result = await db.products.aggregate(pipeline, allowDiskUse=True).to_list(1)
    row = result[0] if result else {}
    
    return {
        "totalItem": row.get("total_item", 0),
        "totalAvailableItem": row.get("total_available_item", 0),
        "totalAvailableQty": row.get("total_available_qty", 0.0),
        "totalValue": row.get("total_value", 0.0),
    }

@api_router.get("/product-hub/records")
async def product_hub_records(
    page: int = 1,
    page_size: int = 300,
    brand: Optional[str] = None,
    dealer: Optional[str] = None,
    branch: Optional[str] = None,
    search: Optional[str] = None,
    category: Optional[str] = None,
    stock_status: Optional[str] = None,
    current_user: UserResponse = Depends(get_current_user)
) -> dict:
    """
    Get paginated Product Hub records with full details.
    """
    page = max(1, page)
    page_size = max(1, min(page_size, 1000))
    skip = (page - 1) * page_size
    
    query = _product_hub_active_query(current_user, brand, dealer, branch)
    _apply_category_filter(query, category)
    _apply_stock_status_filter(query, stock_status)
    
    if search:
        safe_search = re.escape(search.strip())
        search_clause = {
            "$or": [
                {"part_number": {"$regex": safe_search, "$options": "i"}},
                {"item_name": {"$regex": safe_search, "$options": "i"}},
            ]
        }
        if query:
            query["$and"] = [query, search_clause]
        else:
            query.update(search_clause)
    
    total = await db.products.count_documents(query)
    total_pages = (total + page_size - 1) // page_size if total > 0 else 1
    
    records = await db.products.find(query, {"_id": 0}).skip(skip).limit(page_size).to_list(page_size)
    
    return {
        "records": records,
        "total": total,
        "page": page,
        "pageSize": page_size,
        "totalPages": total_pages,
    }

# ==================== PLACEHOLDER ROUTES (minimal) ====================

@api_router.get("/auth/login")
async def login():
    """Placeholder for login."""
    return {"message": "Use POST /auth/login"}

@api_router.post("/auth/login")
async def login_user(username: str, password: str):
    """Authenticate and return JWT token."""
    user = await db.users.find_one({"username": username}, {"_id": 0})
    if not user or not verify_password(password, user.get("hashed_password", "")):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    token = create_access_token({"sub": user["id"]})
    return {"access_token": token, "token_type": "bearer"}

@api_router.get("/auth/me")
async def get_me(current_user: UserResponse = Depends(get_current_user)):
    """Get current user info."""
    return current_user

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(socket_app, host="0.0.0.0", port=8000)
