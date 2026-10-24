import os
import re
import requests
import pytest
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
_orig_request = requests.Session.request
_token = {}


def _admin_token(base: str) -> str:
    if base not in _token:
        r = _orig_request(requests.Session(), "POST", f"{base}/api/auth/login",
                          json={"email": os.environ["ADMIN_EMAIL"], "password": os.environ["ADMIN_PASSWORD"]})
        _token[base] = r.json()["token"]
    return _token[base]


# Endpoints that became admin-only; older suites call them without a login.
ADMIN_PATHS = re.compile(r"/api/(admin/reset|analytics|routes|zones|scorecards|routes/batch|routes/[^/]+/(sequence|optimize|labels-link)"
                         r"|alerts/(trends|low-ratings)|stops/[^/]+/follow-up|codes/(apology|redeem)|drivers/[^/]+/coaching|digest)(\?.*)?$")


def _request(self, method, url, *args, **kwargs):
    u = str(url)
    coaching_read = method.upper() == "GET" and "/coaching" in u  # driver reads own notes: open
    if ADMIN_PATHS.search(u) and not coaching_read and not (kwargs.get("headers") or {}).get("Authorization"):
        headers = dict(kwargs.get("headers") or {})
        headers["Authorization"] = f"Bearer {_admin_token(u.split('/api/', 1)[0])}"
        kwargs["headers"] = headers
    return _orig_request(self, method, url, *args, **kwargs)


@pytest.fixture(autouse=True, scope="session")
def _admin_reset_auth():
    requests.Session.request = _request  # requests.post() also goes through Session.request
    yield
    requests.Session.request = _orig_request
