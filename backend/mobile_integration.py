"""Sleeping Stock Mobile production integration router.
Uses the existing FastAPI app and MongoDB database; no second backend is created.
"""
from fastapi import APIRouter, Depends, HTTPException, Header
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, List
import hashlib, secrets, re, os, uuid


def build_mobile_router(db, get_current_user, hash_password, verify_password):
    router = APIRouter(prefix="/mobile/v2", tags=["Sleeping Stock Mobile"])
    now = lambda: datetime.now(timezone.utc)

    def clean(v, limit=200): return re.sub(r"[\x00-\x1f]", "", str(v or "")).strip()[:limit]
    def phone(v):
        p = re.sub(r"\D", "", str(v or ""))
        if len(p) == 12 and p.startswith("91"): p = p[2:]
        if len(p) != 10: raise HTTPException(400, "Enter a valid 10-digit mobile number")
        return p
    def token_hash(v): return hashlib.sha256(str(v).encode()).hexdigest()
    def branch_code(v):
        words = re.findall(r"[A-Za-z0-9]+", str(v or "").upper())
        code = "".join(w[0] for w in words) if len(words) > 1 else (words[0][:3] if words else "BRN")
        return (code + "BRN")[:3]
    def public_user(d):
        if not d: return d
        return {k:v for k,v in d.items() if k not in {"_id","password_hash","temporary_password"}}
    def public_device(d):
        if not d: return d
        return {k:v for k,v in d.items() if k not in {"_id","session_token_hash"}}

    async def audit(action, actor=None, mobile_user=None, device=None, details=None):
        await db.mobile_audit_logs.insert_one({
            "id": str(uuid.uuid4()), "action": action,
            "actor_user_id": getattr(actor, "id", "") if actor else "mobile",
            "actor_name": getattr(actor, "username", "") if actor else (mobile_user or {}).get("name", ""),
            "actor_role": getattr(actor, "role", "mobile") if actor else "mobile",
            "mobile_user_id": (mobile_user or {}).get("mobile_user_id", ""),
            "device_id": (device or {}).get("device_id", ""),
            "details": details or {}, "created_at": now(),
        })

    def scope_filter(mu):
        return {"brand_name": mu["brand_name"], "dealer_name": mu["dealer_name"], "branch": mu["branch_name"]}

    async def require_manager(current_user, brand, dealer, branch):
        role = (current_user.role or "").lower()
        brand, dealer, branch = clean(brand), clean(dealer), clean(branch)
        if role != "master":
            if current_user.brand and current_user.brand.casefold() != brand.casefold(): raise HTTPException(403,"Brand outside permitted scope")
            if current_user.group and current_user.group.casefold() != dealer.casefold(): raise HTTPException(403,"Dealer outside permitted scope")
            if role == "user":
                branch = current_user.location
                if not branch: raise HTTPException(400,"User has no assigned Branch")
            elif not branch or branch.lower().startswith("all "): raise HTTPException(400,"Select a specific Branch")
        if not all([brand,dealer,branch]): raise HTTPException(400,"Brand, Dealer and a specific Branch are required")
        return brand,dealer,branch

    async def mobile_auth(authorization: Optional[str] = Header(None), x_device_id: Optional[str] = Header(None)):
        if not authorization or not authorization.lower().startswith("bearer "): raise HTTPException(401,"Mobile device session required")
        raw = authorization.split(" ",1)[1].strip()
        session = await db.mobile_sessions.find_one({"session_token_hash":token_hash(raw),"status":"active"},{"_id":0})
        if not session: raise HTTPException(401,"Device session expired or invalid")
        device = await db.mobile_devices.find_one({"device_id":session["device_id"]},{"_id":0})
        mu = await db.mobile_users.find_one({"mobile_user_id":session["mobile_user_id"]},{"_id":0})
        if not device or device.get("status") != "active" or not mu or mu.get("status") != "active": raise HTTPException(403,"Mobile user or device is inactive")
        if x_device_id and x_device_id != device["device_id"]: raise HTTPException(401,"Device mismatch")
        await db.mobile_devices.update_one({"device_id":device["device_id"]},{"$set":{"last_active":now()}})
        return {"session":session,"device":device,"user":mu}

    class MobileUserCreate(BaseModel):
        name: str; mobile_number: str; brand_name: str; dealer_name: str; branch_name: str
        password: Optional[str] = None
    class PasswordReset(BaseModel): password: Optional[str] = None
    class StatusBody(BaseModel): status: str
    class PairingCreate(BaseModel): mobile_user_id: str; brand_name: str; dealer_name: str; branch_name: str
    class PairDevice(BaseModel):
        pairing_code: str; mobile_user_id: str; password: str; device_id: str
        device_name: str="Android Device"; android_info: dict={}; push_token: str=""; app_version: str="1.0.0"
    class PushBody(BaseModel): push_token: str
    class SkipBody(BaseModel): reason: str=""
    class PartDecision(BaseModel):
        part_id: str=""; part_number: str; accepted_quantity: float; remarks: str=""; status: str=""
    class ResponseBody(BaseModel): parts: List[PartDecision]
    class VerificationBody(BaseModel):
        part_number: str; physical_quantity: float; location: str=""; remark: str=""; entry_method: str="MANUAL"
    class MultiSearch(BaseModel): part_numbers: List[str]
    class VersionBody(BaseModel):
        version_name: str; version_code: int; apk_filename: str=""; apk_path: str=""; release_notes: str=""
        minimum_supported_version: str="1.0.0"; mandatory_update: bool=False

    @router.post("/users")
    async def create_user(p:MobileUserCreate, current_user=Depends(get_current_user)):
        brand,dealer,branch=await require_manager(current_user,p.brand_name,p.dealer_name,p.branch_name)
        mobile=phone(p.mobile_number)
        if await db.mobile_users.find_one({"mobile_number":mobile,"branch_name":branch,"status":{"$ne":"removed"}}): raise HTTPException(409,"Mobile user already exists for this Branch")
        date=now().strftime("%y%m%d"); code=branch_code(branch); cid=f"mobile_user_{code}_{date}"
        counter=await db.counters.find_one_and_update({"_id":cid},{"$inc":{"seq":1}},upsert=True,return_document=ReturnDocument.AFTER)
        muid=f"MU{code}{date}{int(counter['seq']):04d}"; temp=p.password or (secrets.token_urlsafe(8)+"aA1!")
        doc={"id":str(uuid.uuid4()),"mobile_user_id":muid,"name":clean(p.name,100),"mobile_number":mobile,"password_hash":hash_password(temp),
             "brand_id":brand,"brand_name":brand,"dealer_id":dealer,"dealer_name":dealer,"branch_id":branch,"branch_name":branch,"status":"active",
             "created_by_user_id":current_user.user_id or current_user.id,"created_by_name":current_user.username,"created_by_role":current_user.role,
             "created_at":now(),"updated_at":now(),"paired_device_count":0,"active_device_count":0,"last_active":None}
        await db.mobile_users.insert_one(doc); await audit("MOBILE_USER_CREATED",current_user,doc)
        return {**public_user(doc),"temporary_password":temp}

    @router.get("/users")
    async def list_users(brand_name:str="",dealer_name:str="",branch_name:str="",current_user=Depends(get_current_user)):
        role=(current_user.role or "").lower(); q={"status":{"$ne":"removed"}}
        if role=="master":
            if brand_name:q["brand_name"]=brand_name
            if dealer_name:q["dealer_name"]=dealer_name
            if branch_name and not branch_name.lower().startswith("all "):q["branch_name"]=branch_name
        else:
            q["brand_name"]=current_user.brand;q["dealer_name"]=current_user.group
            if role=="user":q["branch_name"]=current_user.location
            elif branch_name and not branch_name.lower().startswith("all "):q["branch_name"]=branch_name
        return [public_user(x) async for x in db.mobile_users.find(q,{"_id":0,"password_hash":0}).sort("created_at",-1)]

    @router.get("/users/{muid}")
    async def read_user(muid:str,current_user=Depends(get_current_user)):
        d=await db.mobile_users.find_one({"mobile_user_id":muid},{"_id":0,"password_hash":0})
        if not d: raise HTTPException(404,"Mobile user not found")
        return public_user(d)

    @router.put("/users/{muid}/status")
    async def user_status(muid:str,p:StatusBody,current_user=Depends(get_current_user)):
        status=clean(p.status).lower()
        if status not in {"active","inactive"}:raise HTTPException(400,"Invalid status")
        d=await db.mobile_users.find_one_and_update({"mobile_user_id":muid},{"$set":{"status":status,"updated_at":now()}},return_document=ReturnDocument.AFTER)
        if not d:raise HTTPException(404,"Mobile user not found")
        if status!="active": await db.mobile_sessions.update_many({"mobile_user_id":muid},{"$set":{"status":"revoked","revoked_at":now()}})
        await audit("MOBILE_USER_STATUS_CHANGED",current_user,d,details={"status":status});return public_user(d)

    @router.post("/users/{muid}/reset-password")
    async def reset_password(muid:str,p:PasswordReset,current_user=Depends(get_current_user)):
        pwd=p.password or (secrets.token_urlsafe(8)+"aA1!")
        d=await db.mobile_users.find_one_and_update({"mobile_user_id":muid},{"$set":{"password_hash":hash_password(pwd),"updated_at":now()}},return_document=ReturnDocument.AFTER)
        if not d:raise HTTPException(404,"Mobile user not found")
        await audit("MOBILE_PASSWORD_RESET",current_user,d);return {"mobile_user_id":muid,"temporary_password":pwd}

    @router.post("/pairing")
    async def pairing(p:PairingCreate,current_user=Depends(get_current_user)):
        brand,dealer,branch=await require_manager(current_user,p.brand_name,p.dealer_name,p.branch_name)
        mu=await db.mobile_users.find_one({"mobile_user_id":clean(p.mobile_user_id),"status":"active"},{"_id":0})
        if not mu:raise HTTPException(404,"Active mobile user not found")
        if (mu["brand_name"],mu["dealer_name"],mu["branch_name"])!=(brand,dealer,branch):raise HTTPException(403,"Pairing scope must match the mobile user's Branch")
        code="-".join([secrets.token_hex(2).upper() for _ in range(3)]); exp=now()+timedelta(minutes=10)
        doc={"id":str(uuid.uuid4()),"pairing_code":code,"mobile_user_id":mu["mobile_user_id"],"brand_name":brand,"dealer_name":dealer,"branch_name":branch,
             "created_by_user_id":current_user.user_id or current_user.id,"created_by_name":current_user.username,"created_by_role":current_user.role,"created_at":now(),"expires_at":exp,"used":False,"status":"active"}
        await db.mobile_pairing_codes.insert_one(doc); await audit("PAIRING_CODE_CREATED",current_user,mu,details={"expires_at":exp.isoformat()})
        return {"pairing_code":code,"qr_payload":f"SLEEPINGSTOCK://PAIR/{code}","expires_at":exp}

    @router.post("/pair")
    async def pair(p:PairDevice):
        code=clean(p.pairing_code).upper().replace("SLEEPINGSTOCK://PAIR/","")
        pc=await db.mobile_pairing_codes.find_one_and_update({"pairing_code":code,"used":False,"status":"active","expires_at":{"$gt":now()}},{"$set":{"used":True,"status":"used","used_at":now()}},return_document=ReturnDocument.BEFORE)
        if not pc:raise HTTPException(400,"Pairing code is invalid, expired, or already used")
        mu=await db.mobile_users.find_one({"mobile_user_id":p.mobile_user_id,"status":"active"},{"_id":0})
        if not mu or mu["mobile_user_id"]!=pc["mobile_user_id"] or not verify_password(p.password,mu["password_hash"]):raise HTTPException(401,"Invalid Mobile User ID or password")
        existing=await db.mobile_devices.find_one({"device_id":clean(p.device_id,200),"status":{"$ne":"removed"}})
        if existing:raise HTTPException(409,"This device is already paired")
        raw=secrets.token_urlsafe(48); device={"id":str(uuid.uuid4()),"device_id":clean(p.device_id,200),"mobile_user_id":mu["mobile_user_id"],"device_name":clean(p.device_name,100),
          "android_info":p.android_info,"push_token":clean(p.push_token,500),"paired_at":now(),"last_active":now(),"app_version":clean(p.app_version,30),"status":"active",
          "brand_name":pc["brand_name"],"dealer_name":pc["dealer_name"],"branch_name":pc["branch_name"],"session_token_hash":token_hash(raw)}
        await db.mobile_devices.insert_one(device); await db.mobile_sessions.insert_one({"id":str(uuid.uuid4()),"session_token_hash":token_hash(raw),"device_id":device["device_id"],"mobile_user_id":mu["mobile_user_id"],"status":"active","created_at":now(),"last_active":now()})
        await db.mobile_users.update_one({"mobile_user_id":mu["mobile_user_id"]},{"$inc":{"paired_device_count":1,"active_device_count":1},"$set":{"last_active":now(),"updated_at":now()}})
        await audit("DEVICE_PAIRED",None,mu,device);return {"session_token":raw,"device":public_device(device),"mobile_user":public_user(mu)}

    @router.get("/session")
    async def validate(ctx=Depends(mobile_auth)): return {"valid":True,"device":public_device(ctx["device"]),"mobile_user":public_user(ctx["user"])}

    @router.get("/devices")
    async def devices(mobile_user_id:str="",current_user=Depends(get_current_user)):
        q={};
        if mobile_user_id:q["mobile_user_id"]=mobile_user_id
        return [public_device(x) async for x in db.mobile_devices.find(q,{"_id":0,"session_token_hash":0}).sort("paired_at",-1)]

    @router.put("/devices/{device_id}/status")
    async def device_status(device_id:str,p:StatusBody,current_user=Depends(get_current_user)):
        st=clean(p.status).lower()
        if st not in {"active","inactive"}:raise HTTPException(400,"Invalid device status")
        d=await db.mobile_devices.find_one_and_update({"device_id":device_id},{"$set":{"status":st,"updated_at":now()}},return_document=ReturnDocument.AFTER)
        if not d:raise HTTPException(404,"Device not found")
        if st!="active":await db.mobile_sessions.update_many({"device_id":device_id},{"$set":{"status":"revoked","revoked_at":now()}})
        await audit("DEVICE_STATUS_CHANGED",current_user,device=d,details={"status":st});return public_device(d)

    @router.delete("/devices/{device_id}")
    async def remove_device(device_id:str,current_user=Depends(get_current_user)):
        d=await db.mobile_devices.find_one_and_update({"device_id":device_id},{"$set":{"status":"removed","removed_at":now()}},return_document=ReturnDocument.AFTER)
        if not d:raise HTTPException(404,"Device not found")
        await db.mobile_sessions.update_many({"device_id":device_id},{"$set":{"status":"revoked","revoked_at":now()}});await audit("DEVICE_REMOVED",current_user,device=d);return {"removed":True}

    @router.post("/push-token")
    async def push(p:PushBody,ctx=Depends(mobile_auth)):
        await db.mobile_devices.update_one({"device_id":ctx["device"]["device_id"]},{"$set":{"push_token":clean(p.push_token,500),"updated_at":now()}});return {"registered":True}

    @router.get("/config")
    async def config(app_version:str="1.0.0",ctx=Depends(mobile_auth)):
        v=await db.mobile_app_versions.find_one({}, {"_id":0},sort=[("version_code",-1)]) or {"version_name":"1.0.0","version_code":1,"minimum_supported_version":"1.0.0","mandatory_update":False}
        setting=await db.mobile_settings.find_one({"key":"notification_interval_minutes"},{"_id":0}) or {"value":30}
        return {"app_name":"Sleeping Stock Mobile","package":"in.sleepingstock.mobile","notification_interval_minutes":setting["value"],"latest_version":v}

    @router.post("/versions")
    async def add_version(p:VersionBody,current_user=Depends(get_current_user)):
        if current_user.role!="master":raise HTTPException(403,"Master Admin only")
        doc={**p.model_dump(),"release_date":now(),"created_by":current_user.id};await db.mobile_app_versions.insert_one(doc);return {k:v for k,v in doc.items() if k!="_id"}

    @router.get("/versions/latest")
    async def latest_version(current_user=Depends(get_current_user)):
        return await db.mobile_app_versions.find_one({}, {"_id":0},sort=[("version_code",-1)]) or {"version_name":"1.0.0","version_code":1,"mandatory_update":False}

    @router.get("/notifications")
    async def notifications(ctx=Depends(mobile_auth)):
        mu=ctx["user"]
        q={"supplying_branch":mu["branch_name"],"supplying_dealer":mu["dealer_name"],"status":{"$nin":["Completed","Cancelled"]}}
        rows=[x async for x in db.order_requests.find(q,{"_id":0}).sort("created_at",-1).limit(100)]
        for r in rows:
            a=await db.mobile_notification_actions.find_one({"request_id":r.get("id"),"mobile_user_id":mu["mobile_user_id"]},{"_id":0})
            r["skip_count"]=(a or {}).get("skip_count",0);r["skip_allowed"]=r["skip_count"]<2 and not r.get("mobile_accepted_by")
        return rows

    @router.post("/notifications/{request_id}/skip")
    async def skip(request_id:str,p:SkipBody,ctx=Depends(mobile_auth)):
        mu=ctx["user"]
        req=await db.order_requests.find_one({"id":request_id,"supplying_branch":mu["branch_name"],"supplying_dealer":mu["dealer_name"]},{"_id":0})
        if not req:raise HTTPException(404,"Request not available for this Branch")
        action=await db.mobile_notification_actions.find_one_and_update({"request_id":request_id,"mobile_user_id":mu["mobile_user_id"]},{"$inc":{"skip_count":1},"$set":{"last_skipped_at":now(),"reason":clean(p.reason)}},upsert=True,return_document=ReturnDocument.AFTER)
        if action["skip_count"]>2:
            await db.mobile_notification_actions.update_one({"_id":action["_id"]},{"$inc":{"skip_count":-1}});raise HTTPException(400,"Skip is not allowed after two skips")
        return {"skip_count":action["skip_count"],"skip_allowed":action["skip_count"]<2}

    @router.post("/notifications/{request_id}/accept")
    async def accept(request_id:str,ctx=Depends(mobile_auth)):
        mu=ctx["user"]; dev=ctx["device"]
        req=await db.order_requests.find_one_and_update({"id":request_id,"supplying_branch":mu["branch_name"],"supplying_dealer":mu["dealer_name"],"mobile_accepted_by":{"$exists":False}},
          {"$set":{"mobile_accepted_by":mu["mobile_user_id"],"mobile_accepted_device_id":dev["device_id"],"mobile_accepted_at":now()}},return_document=ReturnDocument.AFTER)
        if not req:
            existing=await db.order_requests.find_one({"id":request_id},{"_id":0});raise HTTPException(409,f"Already accepted by {existing.get('mobile_accepted_by','another user') if existing else 'another user'}")
        return req

    @router.post("/notifications/{request_id}/response")
    async def response(request_id:str,p:ResponseBody,ctx=Depends(mobile_auth)):
        mu=ctx["user"]; req=await db.order_requests.find_one({"id":request_id,"mobile_accepted_by":mu["mobile_user_id"]},{"_id":0})
        if not req:raise HTTPException(403,"Accept this request before responding")
        requested=float(req.get("quantity",req.get("requested_qty",0)) or 0); accepted=0
        decisions=[]
        for x in p.parts:
            aq=float(x.accepted_quantity)
            if aq<0:raise HTTPException(400,"Accepted quantity cannot be negative")
            rq=requested if len(p.parts)==1 else float(next((i.get("quantity",i.get("requested_qty",0)) for i in req.get("items",[]) if i.get("part_number")==x.part_number),0))
            if aq>rq:raise HTTPException(400,f"Accepted quantity exceeds requested quantity for {x.part_number}")
            if aq<rq and not clean(x.remarks):raise HTTPException(400,f"Remark is required for partial/rejected part {x.part_number}")
            st="Accepted" if aq==rq else ("Partially Accepted" if aq>0 else "Rejected")
            decisions.append({**x.model_dump(),"status":st,"remarks":clean(x.remarks)});accepted+=aq
        overall="Accepted" if accepted==requested else ("Partially Accepted" if accepted>0 else "Rejected")
        await db.order_requests.update_one({"id":request_id},{"$set":{"mobile_part_decisions":decisions,"accepted_qty":accepted,"status":overall,"updated_at":now()}})
        return {"request_id":request_id,"status":overall,"accepted_quantity":accepted}

    @router.post("/stock-verifications")
    async def verify_stock(p:VerificationBody,ctx=Depends(mobile_auth)):
        mu,dev=ctx["user"],ctx["device"]; pn=clean(p.part_number,100).upper()
        if not pn or p.physical_quantity<0:raise HTTPException(400,"Valid Part Number and Physical Quantity are required")
        q={**scope_filter(mu),"part_number":{"$regex":f"^{re.escape(pn)}$","$options":"i"},"publish_status":"published","is_active_today":True}
        product=await db.products.find_one(q,{"_id":0}) or await db.products.find_one({**scope_filter(mu),"part_number":{"$regex":f"^{re.escape(pn)}$","$options":"i"},"publish_status":"published"},{"_id":0})
        if not product:raise HTTPException(404,"Part not found in paired Branch")
        doc={"id":str(uuid.uuid4()),"part_number":pn,"part_name":product.get("part_name",product.get("item_name","")),"system_quantity":float(product.get("quantity",0) or 0),
          "physical_quantity":p.physical_quantity,"location":clean(p.location),"remark":clean(p.remark,500),"entry_method":p.entry_method if p.entry_method in {"MANUAL","CAMERA_OCR"} else "MANUAL",
          "verified_user":mu["name"],"mobile_user_id":mu["mobile_user_id"],"device_id":dev["device_id"],"brand_name":mu["brand_name"],"dealer_name":mu["dealer_name"],"branch":mu["branch_name"],"verified_at":now()}
        await db.stock_verification_history.insert_one(doc);return {k:v for k,v in doc.items() if k!="_id"}

    @router.get("/stock-verifications")
    async def verification_history(ctx=Depends(mobile_auth)):
        mu=ctx["user"];return [x async for x in db.stock_verification_history.find({"mobile_user_id":mu["mobile_user_id"]},{"_id":0}).sort("verified_at",-1).limit(200)]

    async def search_parts(numbers,mu):
        nums=[clean(x,100).upper() for x in numbers if clean(x,100)][:100]
        q={**scope_filter(mu),"part_number":{"$in":nums},"publish_status":"published","$or":[{"is_active_today":True},{"active_status":"active"},{"status":"active"}]}
        return [x async for x in db.products.find(q,{"_id":0,"part_number":1,"part_name":1,"item_name":1,"quantity":1,"location":1,"loc":1,"part_category":1,"category":1,"price":1,"mav":1,"updated_at":1,"created_at":1})]

    @router.get("/stock-search/{part_number}")
    async def stock_search(part_number:str,ctx=Depends(mobile_auth)):return await search_parts([part_number],ctx["user"])
    @router.post("/stock-search")
    async def multi_search(p:MultiSearch,ctx=Depends(mobile_auth)):return await search_parts(p.part_numbers,ctx["user"])

    return router


async def ensure_mobile_indexes(db):
    await db.mobile_users.create_index("mobile_user_id",unique=True)
    await db.mobile_users.create_index([("mobile_number",1),("branch_name",1)])
    await db.mobile_users.create_index([("brand_name",1),("dealer_name",1),("branch_name",1)])
    await db.mobile_pairing_codes.create_index("pairing_code",unique=True)
    await db.mobile_pairing_codes.create_index("expires_at",expireAfterSeconds=0)
    await db.mobile_devices.create_index("device_id",unique=True)
    await db.mobile_devices.create_index("push_token",sparse=True)
    await db.mobile_sessions.create_index("session_token_hash",unique=True)
    await db.mobile_notification_actions.create_index([("request_id",1),("mobile_user_id",1)],unique=True)
    await db.mobile_app_versions.create_index("version_code",unique=True)
    await db.stock_verification_history.create_index([("branch",1),("part_number",1),("verified_at",-1)])
    await db.mobile_audit_logs.create_index([("mobile_user_id",1),("created_at",-1)])
