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


# Endpoints that became admin- or driver-only; older suites call them without a login (admin passes both).
# Send header "X-No-Auto-Auth: 1" to opt out (for 401 checks).
ADMIN_PATHS = re.compile(r"/api/(admin/reset|analytics|routes|zones|scorecards|routes/batch|routes/[^/]+/(sequence|optimize|labels-link|start|update-seen)"
                         r"|routes/[^/?]+|drivers|drivers/[^/]+/(route|location|simulate-step|summary|scorecard|badges/new|badges/seen|phone|coaching)"
                         r"|stops/[^/?]+|stops/[^/]+/(verify-qr|arrive|deliver|fail|contact|phone|window|rating|share)|upload|files/.+"
                         r"|alerts/(trends|low-ratings)|stops/[^/]+/follow-up|codes/(apology|redeem)|digest)(\?.*)?$")


def _request(self, method, url, *args, **kwargs):
    u = str(url)
    headers = dict(kwargs.get("headers") or {})
    if headers.pop("X-No-Auto-Auth", None):
        kwargs["headers"] = headers
        return _orig_request(self, method, url, *args, **kwargs)
    if ADMIN_PATHS.search(u) and not headers.get("Authorization") and not (self.headers or {}).get("Authorization"):
        headers["Authorization"] = f"Bearer {_admin_token(u.split('/api/', 1)[0])}"
        kwargs["headers"] = headers
    return _orig_request(self, method, url, *args, **kwargs)


@pytest.fixture(autouse=True, scope="session")
def _admin_reset_auth():
    requests.Session.request = _request  # requests.post() also goes through Session.request
    yield
    requests.Session.request = _orig_request
