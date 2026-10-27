from fastapi import APIRouter
from core import ADMIN_ONLY, db, now_iso
from routes import launch

router = APIRouter(prefix="/api")


# ---------------------------------------------------------------- first-store setup guide (after the launch cleanup)
async def _flag(key: str) -> bool:
    return bool(await db.settings.find_one({"key": key}, {"_id": 1}))


async def mark_pricing_reviewed():
    await db.settings.update_one({"key": "setup_pricing"}, {"$set": {"key": "setup_pricing", "value": now_iso()}}, upsert=True)


@router.get("/admin/setup", dependencies=ADMIN_ONLY)
async def setup_status():
    drivers_with_pin = await db.users.count_documents({"role": "driver", "pin_hash": {"$type": "string"}})
    steps = {
        "pricing": await _flag("setup_pricing"),
        "store": await db.users.count_documents({"role": "merchant"}) > 0,
        "driver": drivers_with_pin > 0,
    }
    done = await _flag("setup_done") or all(steps.values())
    return {"show": await launch.demo_removed() and not done, "done": done, "steps": steps}


@router.post("/admin/setup/pricing-reviewed", dependencies=ADMIN_ONLY)
async def pricing_reviewed():
    await mark_pricing_reviewed()
    return {"ok": True}


@router.post("/admin/setup/finish", dependencies=ADMIN_ONLY)
async def finish_setup():
    """Hide the guide for good (also hidden automatically once every step is done)."""
    await db.settings.update_one({"key": "setup_done"}, {"$set": {"key": "setup_done", "value": now_iso()}}, upsert=True)
    return {"ok": True}
