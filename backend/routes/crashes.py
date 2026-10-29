"""Crash reports sent by the app (uncaught JS errors), listed for admins. Public endpoint: size-capped and rate-limited."""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime, timedelta, timezone
import uuid
from core import ADMIN_ONLY, db, now_iso
from routes import auth

router = APIRouter(prefix="/api")
PER_IP_PER_HOUR = 30


class CrashIn(BaseModel):
    message: str = Field(max_length=500)
    stack: str = Field("", max_length=4000)
    fatal: bool = False
    where: str = Field("global", max_length=40)
    at: Optional[str] = Field(None, max_length=40)
    platform: Optional[str] = Field(None, max_length=20)
    os_version: Optional[str] = Field(None, max_length=20)
    model: Optional[str] = Field(None, max_length=80)
    app_version: Optional[str] = Field(None, max_length=20)
    delayed: bool = False


@router.post("/crash-reports")
async def crash_report(body: CrashIn, request: Request):
    ip = auth.client_ip(request)
    since = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    if await db.crash_reports.count_documents({"ip": ip, "received_at": {"$gte": since}}) >= PER_IP_PER_HOUR:
        raise HTTPException(429, "Too many reports")
    await db.crash_reports.insert_one({"id": str(uuid.uuid4()), **body.model_dump(), "ip": ip, "received_at": now_iso()})
    return {"ok": True}


@router.get("/admin/crash-reports", dependencies=ADMIN_ONLY)
async def list_crash_reports():
    return await db.crash_reports.find({}, {"_id": 0, "ip": 0}).sort("received_at", -1).to_list(100)
