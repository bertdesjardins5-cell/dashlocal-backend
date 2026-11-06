import asyncio
import os
import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from core import current_user, db, logger
from services import realtime

router = APIRouter(prefix="/api")

# ---------------------------------------------------------------- phone alerts (Emergent managed push relay)
PUSH_BASE_URL = "https://integrations.emergentagent.com"
_client = httpx.AsyncClient(base_url=PUSH_BASE_URL, headers={"X-Push-Key": os.environ.get("EMERGENT_PUSH_KEY", "placeholder")}, timeout=10.0)


class RegisterPushBody(BaseModel):
    user_id: str
    platform: str  # "android" | "ios"
    device_token: str


@router.post("/register-push", status_code=201)
async def register_push(body: RegisterPushBody, u: dict = Depends(current_user)):
    if body.user_id != u["id"]:
        raise HTTPException(403, "You can only register your own phone")
    if body.platform not in ("android", "ios") or not body.device_token.strip():
        raise HTTPException(400, "Unsupported device")
    resp = await _client.post("/api/v1/push/users/register", json=body.model_dump())
    if resp.status_code == 401:
        raise HTTPException(500, "EMERGENT_PUSH_KEY missing or invalid")
    if resp.status_code >= 500:
        raise HTTPException(502, "Push provider unavailable")
    resp.raise_for_status()
    return {"status": "registered"}


async def send_push(recipients: list, data: dict, idempotency_key: str = None, _attempt: int = 0) -> None:
    if not recipients:
        return
    if len(recipients) > 100:
        raise ValueError("max 100 recipients per /trigger call; chunk before sending")
    if "title" not in data or "message" not in data:
        raise ValueError("data must include title and message")
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
