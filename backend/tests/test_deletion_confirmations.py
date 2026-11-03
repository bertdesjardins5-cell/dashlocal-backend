# Deletion confirmation (iteration 65): unit tests for routes.account.confirm_store / confirm_driver with the
# email sender and Twilio client stubbed, so nothing real is sent.
import sys
import asyncio
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from routes import account  # noqa: E402


def run(c):
    return asyncio.get_event_loop().run_until_complete(c) if False else asyncio.run(c)


class FakeTw:
    sent = []
    fail = False

    def __init__(self, *a):
        self.messages = self

    def create(self, **kw):
        if FakeTw.fail:
            raise RuntimeError("twilio down")
        FakeTw.sent.append(kw)


def _env(monkeypatch):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "tok")
    monkeypatch.setenv("TWILIO_PHONE_NUMBER", "+15195550000")
    monkeypatch.setattr(account, "TwilioClient", FakeTw)
    FakeTw.sent, FakeTw.fail = [], False


# Store: one email per recipient, name escaped, failures swallowed
def test_confirm_store_sends_each_and_escapes(monkeypatch):
    calls = []

    async def fake_send(*, to, subject, html):
        calls.append((to, subject, html))
        if to == "bad@example.com":
            raise RuntimeError("bounce")
        return "id"
    monkeypatch.setattr(account.emails, "send_email", fake_send)
    run(account.confirm_store("TEST_<b>Shop</b> & Co", ["a@example.com", "bad@example.com", "c@example.com"], "2026-01-15T10:00:00+00:00"))
    assert [c[0] for c in calls] == ["a@example.com", "bad@example.com", "c@example.com"]
    assert all(c[1] == "Your ArriveWise account was deleted" for c in calls)
    html = calls[0][2]
    assert "TEST_&lt;b&gt;Shop&lt;/b&gt; &amp; Co" in html and "<b>Shop</b>" not in html
    assert "2026-01-15" in html


def test_confirm_store_empty_list_noop(monkeypatch):
    calls = []

    async def fake_send(**kw):
        calls.append(kw)
    monkeypatch.setattr(account.emails, "send_email", fake_send)
    run(account.confirm_store("X", [], "2026-01-15"))
    assert calls == []


# Driver: SMS to real numbers, skipped for fictional 555-01xx / empty / missing Twilio env
def test_confirm_driver_real_number(monkeypatch):
    _env(monkeypatch)
    run(account.confirm_driver("+15197771234"))
    assert len(FakeTw.sent) == 1 and FakeTw.sent[0]["to"] == "+15197771234" and "deleted" in FakeTw.sent[0]["body"]


def test_confirm_driver_skips_fictional_and_empty(monkeypatch):
    _env(monkeypatch)
    run(account.confirm_driver("+15195550101"))
    run(account.confirm_driver(""))
    assert FakeTw.sent == []


def test_confirm_driver_skips_without_twilio_env(monkeypatch):
    _env(monkeypatch)
    monkeypatch.delenv("TWILIO_AUTH_TOKEN")
    run(account.confirm_driver("+15197771234"))
    assert FakeTw.sent == []


def test_confirm_driver_swallows_errors(monkeypatch):
    _env(monkeypatch)
    FakeTw.fail = True
    run(account.confirm_driver("+15197771234"))  # must not raise
