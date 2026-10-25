# Iteration 22 UI seed: a past delivered stop with a proof photo for drv-1's next stop (doorstep hint). Prints the target stop id.
import json
import subprocess
import sys

import requests

sys.path.insert(0, "/app/backend/tests")
from test_csv_note_doorstep_hint import API, NOAUTH, clone_delivered, mongo  # noqa: E402

d = requests.post(f"{API}/auth/driver-login", json={"phone": "+14155550101", "pin": "482913"}, headers=NOAUTH).json()["token"]
H = {"Authorization": f"Bearer {d}", "X-No-Auto-Auth": "1"}
img = requests.get("https://images.unsplash.com/photo-1558036117-15d82a90b9b1?w=600&q=60").content
up = requests.post(f"{API}/upload", headers=H, files={"file": ("door.jpg", img, "image/jpeg")})
up.raise_for_status()
t = json.loads(mongo('const r=db.routes.findOne({driver_id:"drv-1",status:"active"});'
                     'print(JSON.stringify(db.stops.find({route_id:r.id,status:"pending"},{_id:0,id:1,tracking_code:1,sequence:1}).sort({sequence:1}).limit(1).toArray()[0]))'))
clone_delivered(t["id"], up.json()["path"], "2026-01-12T17:30:00+00:00")
print(json.dumps(t))
