"""Capture store-listing screenshots of LocalDash at phone size (Play: 1080x1920, App Store 6.7": 1290x2796)."""
import asyncio
import json
import os
import sys
import httpx
from playwright.async_api import async_playwright

BASE = sys.argv[1].rstrip("/")
OUT = "/app/store_screenshots"
SIZES = {"android": (360, 640), "iphone": (430, 932)}
STORE = ("staff", {"email": "bakery@localdash.app", "password": "Bakery2026"})
ADMIN = ("staff", {"email": "admin@localdash.app", "password": "DashLocalAdmin2026"})
SHOTS = [  # (file, path, login, (tracking code, last4) to unlock, action)
    ("1_track_live", "/customer/MD1001", None, ("MD1001", "0100"), None),
    ("2_rate_delivery", "/customer/MH2088", None, ("MH2088", "0195"), "rate"),
    ("3_driver_route", "/driver/drv-1", ("driver", {"phone": "(519) 555-0101", "pin": "482913"}), None, "notnow"),
    ("4_ratings", "/admin", ADMIN, None, "ratings"),
    ("5_store_new_delivery", "/merchant/mer-1", STORE, None, "newdelivery"),
    ("6_dispatch_route", "/admin/route/a6e0851a-233b-4482-8102-c0f77e7faefc", ADMIN, None, None),
    ("7_home", "/", None, None, None),
]


def unlock(code, last4):
    r = httpx.post(f"{BASE}/api/track/{code}/verify", json={"last4": last4}, timeout=30)
    r.raise_for_status()
    return r.json()


async def act(page, action):
    try:
        if action == "notnow":
            await page.get_by_text("NOT NOW", exact=True).first.click(timeout=5000)
        elif action == "rate":
            await page.get_by_test_id("rating-star-5").click(timeout=5000)
            await page.get_by_test_id("rating-tag-on-time").click()
            await page.get_by_test_id("rating-tag-friendly-driver").click()
            await page.get_by_test_id("rating-comment-input").fill("Arrived right on time and the driver was lovely. Thank you!")
            await page.get_by_test_id("rating-prompt").scroll_into_view_if_needed()
        elif action == "ratings":
            await page.get_by_test_id("ratings-card").scroll_into_view_if_needed(timeout=8000)
            await page.evaluate("() => { const e = document.querySelector('[data-testid=ratings-card]'); e && e.scrollIntoView({block: 'start'}); }")
        elif action == "newdelivery":
            await page.get_by_test_id("order-consent-checkbox").click(timeout=5000)
            await page.get_by_test_id("merchant-new-delivery-button").click()
        await page.wait_for_timeout(2500)
    except Exception as e:
        print("action failed", action, e)


def login(kind, body):
    url = f"{BASE}/api/auth/{'driver-login' if kind == 'driver' else 'login'}"
    r = httpx.post(url, json=body, timeout=30)
    r.raise_for_status()
    d = r.json()
    return {"token": d["token"], "user": d["user"]}


async def main():
    os.makedirs(OUT, exist_ok=True)
    sessions = {}
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        for device, (w, h) in SIZES.items():
            for name, path, auth, unl, action in SHOTS:
                if len(sys.argv) > 2 and not name.startswith(sys.argv[2]):
                    continue
                ctx = await browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=3, is_mobile=True, has_touch=True)
                page = await ctx.new_page()
                await page.goto(BASE + "/privacy")
                if auth:
                    key = json.dumps(auth[1])
                    sessions.setdefault(key, login(*auth))
                    await page.evaluate("s => localStorage.setItem('multidrop_session', s)", json.dumps(sessions[key]))
                if unl:
                    sessions.setdefault(unl, {**unlock(*unl)})
                    key = "dashlocal_order_" + unl[0]
                    await page.evaluate("([k, v]) => localStorage.setItem(k, v)", [key, json.dumps(sessions[unl])])
                await page.goto(BASE + path)
                await page.wait_for_timeout(9000)
                if action:
                    await act(page, action)
                f = f"{OUT}/{device}_{name}.png"
                await page.screenshot(path=f)
                print("saved", f)
                await ctx.close()
        await browser.close()


asyncio.run(main())
