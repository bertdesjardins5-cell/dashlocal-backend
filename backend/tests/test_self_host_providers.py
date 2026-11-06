"""Own S3 / Resend / Expo push paths (self-hosting) work with mocked providers; defaults stay on Emergent."""
import asyncio
import importlib
import io
import os
import sys

import httpx
from botocore.stub import Stubber

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))


def test_s3_put_get():
    import core
    core.S3_BUCKET = "test-bucket"
    os.environ.setdefault("AWS_ACCESS_KEY_ID", "x")
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "y")
    os.environ["S3_REGION"] = "ca-central-1"
    core._s3 = None
    c = core.s3_client()
    st = Stubber(c)
    st.add_response("put_object", {}, {"Bucket": "test-bucket", "Key": "a/b.jpg", "Body": b"hi", "ContentType": "image/jpeg"})
    from botocore.response import StreamingBody
    st.add_response("get_object", {"Body": StreamingBody(io.BytesIO(b"hi"), 2), "ContentType": "image/jpeg"}, {"Bucket": "test-bucket", "Key": "a/b.jpg"})
    st.add_client_error("get_object", "NoSuchKey", http_status_code=404)
    with st:
        assert core.put_object("a/b.jpg", b"hi", "image/jpeg") == {"path": "a/b.jpg", "size": 2}
        assert core.get_object("a/b.jpg") == (b"hi", "image/jpeg")
        import requests
        try:
            core.get_object("missing")
            raise AssertionError("expected error")
        except requests.HTTPError:
            pass
    core.S3_BUCKET = ""
    core._s3 = None


def test_resend_send(monkeypatch):
    from routes import emails
    seen = {}

    def handler(req: httpx.Request):
        seen["url"], seen["auth"], seen["json"] = str(req.url), req.headers["authorization"], req.read()
        return httpx.Response(200, json={"id": "email_123"})

    real = httpx.AsyncClient
    monkeypatch.setattr(emails.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(emails, "RESEND_API_KEY", "re_test")
    monkeypatch.setattr(emails, "EMAIL_FROM_ADDRESS", "notifications@dashlocal.ca")
    rid = asyncio.run(emails.send_email(to="you@example.com", subject="Hi", html="<p>Hello</p>"))
    assert rid == "email_123"
    assert seen["url"] == "https://api.resend.com/emails" and seen["auth"] == "Bearer re_test"
    assert b"DashLocal <notifications@dashlocal.ca>" in seen["json"] or b"<notifications@dashlocal.ca>" in seen["json"]


def test_expo_push(monkeypatch):
    from routes import push
    from core import db
    sent = {}

    def handler(req: httpx.Request):
        sent["body"] = req.read()
        return httpx.Response(200, json={"data": [{"status": "error", "details": {"error": "DeviceNotRegistered"}}]})

    real = httpx.AsyncClient
    monkeypatch.setattr(push.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(push, "PUSH_PROVIDER", "expo")
    tok = "ExponentPushToken[selfhost-test]"

    async def run():
        await db.push_devices.update_one({"expo_token": tok}, {"$set": {"user_id": "selfhost-test-user", "inactive": False}}, upsert=True)
        await push.send_push(["selfhost-test-user"], {"title": "T", "message": "M", "action_url": "/x"})
        d = await db.push_devices.find_one({"expo_token": tok})
        await db.push_devices.delete_one({"expo_token": tok})  # test cleanup of its own row only
        return d

    d = asyncio.run(run())
    assert b"selfhost-test" in sent["body"] and b'"body":"M"' in sent["body"].replace(b" ", b"")
    assert d["inactive"] is True


def test_defaults_stay_emergent():
    from routes import emails, push
    import core
    importlib.reload(core)
    assert push.PUSH_PROVIDER == os.environ.get("PUSH_PROVIDER", "emergent").strip().lower()
    assert core.S3_BUCKET == (os.environ.get("S3_BUCKET") or "").strip()
    assert emails.RESEND_API_KEY == os.environ.get("RESEND_API_KEY", "").strip()
