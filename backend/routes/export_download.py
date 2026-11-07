"""TEMPORARY: one private link to download the sanitized project export ZIP. Remove this file after the owner has it."""
import json
import secrets
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter(prefix="/api")

ZIP = Path(__file__).resolve().parents[2] / "export" / "ArriveWise_DashLocal_project.zip"
TOKEN_FILE = ZIP.parent / ".download_token.json"  # {"token", "expires", "left"} written when the link is issued


@router.get("/export-download/{token}")
async def export_download(token: str):
    try:
        t = json.loads(TOKEN_FILE.read_text())
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, "Not found")
    if not secrets.compare_digest(token, t["token"]) or time.time() > t["expires"] or t["left"] <= 0 or not ZIP.exists():
        raise HTTPException(404, "Not found")
    t["left"] -= 1
    TOKEN_FILE.write_text(json.dumps(t))
    return FileResponse(ZIP, media_type="application/zip", filename=ZIP.name, headers={"Cache-Control": "no-store"})
