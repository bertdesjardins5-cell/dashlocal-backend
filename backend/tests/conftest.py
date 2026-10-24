import os
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


def _request(self, method, url, *args, **kwargs):
    # /api/admin/reset needs the admin login; older suites call it without one
    if str(url).endswith("/api/admin/reset"):
        headers = dict(kwargs.get("headers") or {})
        headers.setdefault("Authorization", f"Bearer {_admin_token(str(url).rsplit('/api/', 1)[0])}")
        kwargs["headers"] = headers
    return _orig_request(self, method, url, *args, **kwargs)


@pytest.fixture(autouse=True, scope="session")
def _admin_reset_auth():
    requests.Session.request = _request  # requests.post() also goes through Session.request
    yield
    requests.Session.request = _orig_request
