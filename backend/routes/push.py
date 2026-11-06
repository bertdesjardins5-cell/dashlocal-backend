import asyncio
import os
from typing import Optional
import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from core import current_user, db, logger, now_iso
from services import realtime

router = APIRouter(prefix="/api")

# ---------------------------------------------------------------- phone alerts
# PUSH_PROVIDER=expo (self-hosting, e.g. Railway): send through Expo's push service to the Expo tokens saved in db.push_devices.
# Otherwise: Emergent managed push relay (default).
PUSH_PROVIDER = os.environ.get("PUSH_PROVIDER", "emergent").strip().lower()
EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"
PUSH_BASE_URL = "https://integrations.emergentagent.com"
_client = httpx.AsyncClient(base_url=PUSH_BASE_URL, headers={"X-Push-Key": os.environ.get("EMERGENT_PUSH_KEY", "placeholder")}, timeout=10.0)


class RegisterPushBody(BaseModel):
    user_id: str
    platform: str  # "android" | "ios"
    device_token: str
    expo_token: Optional[str] = None  # only in builds linked to your own Expo (EAS) project


@router.post("/register-push", status_code=201)
async def register_push(body: RegisterPushBody, u: dict = Depends(current_user)):
    if body.user_id != u["id"]:
        raise HTTPException(403, "You can only register your own phone")
    if body.platform not in ("android", "ios") or not body.device_token.strip():
        raise HTTPException(400, "Unsupported device")
    if body.expo_token and body.expo_token.startswith(("ExponentPushToken[", "ExpoPushToken[")):
        await db.push_devices.update_one({"expo_token": body.expo_token},
                                         {"$set": {"user_id": u["id"], "platform": body.platform, "updated_at": now_iso(), "inactive": False}}, upsert=True)
    if PUSH_PROVIDER == "expo":
        return {"status": "registered" if body.expo_token else "no_expo_token"}
    resp = await _client.post("/api/v1/push/users/register", json=body.model_dump(exclude={"expo_token"}))
    if resp.status_code == 401:
        raise HTTPException(500, "EMERGENT_PUSH_KEY missing or invalid")
    if resp.status_code >= 500:
        raise HTTPException(502, "Push provider unavailable")
    resp.raise_for_status()
    return {"status": "registered"}


async def send_expo_push(recipients: list, data: dict) -> None:
    devices = await db.push_devices.find({"user_id": {"$in": recipients}, "inactive": {"$ne": True}}, {"_id": 0, "expo_token": 1}).to_list(500)
    if not devices:
        return
    msgs = [{"to": d["expo_token"], "title": data["title"], "body": data["message"], "sound": "default",
             "data": {k: v for k, v in data.items() if k not in ("title", "message")}} for d in devices]
    async with httpx.AsyncClient(timeout=10.0) as c:
        resp = await c.post(EXPO_PUSH_URL, json=msgs, headers={"Accept": "application/json"})
    if resp.status_code >= 500:
        raise HTTPException(502, "Push provider unavailable")
    resp.raise_for_status()
    for d, t in zip(devices, resp.json().get("data", [])):  # stop sending to phones that uninstalled the app
        if t.get("status") == "error" and (t.get("details") or {}).get("error") == "DeviceNotRegistered":
            await db.push_devices.update_one({"expo_token": d["expo_token"]}, {"$set": {"inactive": True, "inactive_at": now_iso()}})


async def send_push(recipients: list, data: dict, idempotency_key: str = None, _attempt: int = 0) -> None:
    if not recipients:
        return
    if len(recipients) > 100:
        raise ValueError("max 100 recipients per /trigger call; chunk before sending")
    if "title" not in data or "message" not in data:
        raise ValueError("data must include title and message")
    if PUSH_PROVIDER == "expo":
        return await send_expo_push(recipients, data)
    payload: dict = {"recipients": recipients, "data": data}
    if idempotency_key:
        payload["$idempotency_key"] = idempotency_key
    resp = await _client.post("/api/v1/push/trigger", json=payload)
    if resp.status_code == 401:
        raise HTTPException(500, "EMERGENT_PUSH_KEY missing or invalid")
    code = resp.json().get("code", "") if resp.headers.get("content-type", "").startswith("application/json") else ""
    if code == "idempotency_key_in_progress" and idempotency_key and _attempt < 3:
        await asyncio.sleep(3)
        return await send_push(recipients, data, idempotency_key=idempotency_key, _attempt=_attempt + 1)
    if code in ("insufficient_credits", "integration_disabled", "daily_limit_reached"):
        logger.warning(f"Push paused: {code} ({resp.status_code}): {resp.json().get('message', '')}")
        return
    if resp.status_code >= 500:
        raise HTTPException(502, "Push provider unavailable")
    resp.raise_for_status()


async def _notify_run(driver_id: str, route_id: str, route_name: str, stops: int, added: bool):
    try:
        u = await db.users.find_one({"role": "driver", "driver_id": driver_id, "disabled": {"$ne": True}}, {"_id": 0, "id": 1})
        if not u:
            return
        msg = f"{stops} more drop{'s' if stops != 1 else ''} added to {route_name}" if added else f"{route_name} · {stops} drop{'s' if stops != 1 else ''} — tap to open your manifest"
        await send_push([u["id"]], {"title": "Stops added to your run" if added else "New run assigned",
                                    "message": msg, "action_url": f"/driver/{driver_id}"},
                        idempotency_key=f"run-{route_id}-{stops}-{int(added)}")
    except Exception as e:
        logger.warning(f"Push failed (non-blocking): {e}")


def notify_run(driver_id: str, route_id: str, route_name: str, stops: int, added: bool = False):
    """Buzz the driver's phone when dispatch gives them a run; never delays or blocks dispatch."""
    if driver_id and stops:
        realtime.spawn(_notify_run(driver_id, route_id, route_name, stops, added))
