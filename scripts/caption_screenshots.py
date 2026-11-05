"""Turn /app/store_screenshots/*.png into captioned store images (bold headline on top) in /app/store_screenshots/captioned."""
import glob
import os
import zipfile
from PIL import Image, ImageDraw, ImageFont

SRC = "/app/store_screenshots"
OUT = f"{SRC}/captioned"
FONT = "/app/frontend/assets/fonts/Poppins-SemiBold.ttf"
KICKER_FONT = "/app/frontend/assets/fonts/SpaceMono-Regular.ttf"
BG, RED, WHITE = (10, 10, 10), (230, 0, 0), (255, 255, 255)
CAPTIONS = {  # screen key: (kicker, headline)
    "1_track_live": ("LIVE TRACKING", "Watch your delivery arrive in real time"),
    "2_rate_delivery": ("CUSTOMER RATINGS", "Rate every delivery in one tap"),
    "3_driver_route": ("FOR DRIVERS", "Your fastest route, stop by stop"),
    "4_ratings": ("RATINGS", "See how every driver is doing"),
    "5_store_new_delivery": ("FOR STORES", "Send a delivery in seconds"),
    "6_dispatch_route": ("DISPATCH", "Plan and optimize routes instantly"),
    "7_home": ("LOCALDASH", "Same-day local delivery, done right"),
}


def wrap(draw, text, font, width):
    lines, cur = [], ""
    for w in text.split():
        t = f"{cur} {w}".strip()
        if draw.textlength(t, font=font) <= width:
            cur = t
        else:
            lines.append(cur)
            cur = w
    return lines + [cur]


def caption(path):
    name = os.path.basename(path)[:-4]
    device, key = name.split("_", 1)
    kicker, head = CAPTIONS[key]
    shot = Image.open(path).convert("RGB")
    W, H = shot.size
    canvas = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(canvas)
    pad = int(W * 0.08)
    kf = ImageFont.truetype(KICKER_FONT, int(W * 0.035))
    hf = ImageFont.truetype(FONT, int(W * 0.075))
    y = int(H * 0.05)
    d.text((pad, y), kicker, font=kf, fill=RED)
    y += int(W * 0.06)
    for line in wrap(d, head, hf, W - pad * 2):
        d.text((pad, y), line, font=hf, fill=WHITE)
        y += int(hf.size * 1.2)
    y += int(H * 0.025)
    # phone frame: screenshot scaled to the remaining space, white border, cropped at the bottom edge
    border = max(6, W // 120)
    sw = W - pad * 2
    scaled = shot.resize((sw, int(H * sw / W)), Image.LANCZOS)
    d.rectangle([pad - border, y - border, pad + sw + border, H + border], fill=WHITE)
    canvas.paste(scaled.crop((0, 0, sw, H - y)), (pad, y))
    os.makedirs(f"{OUT}/{device}", exist_ok=True)
    canvas.save(f"{OUT}/{device}/{key}.png", optimize=True)


files = sorted(p for p in glob.glob(f"{SRC}/*.png"))
for f in files:
    caption(f)
    print("captioned", f)
with zipfile.ZipFile(f"{SRC}/LocalDash_store_screenshots.zip", "w") as z:
    for f in files:
        z.write(f, f"plain/{os.path.basename(f)}")
    for f in sorted(glob.glob(f"{OUT}/*/*.png")):
        z.write(f, f"captioned/{os.path.relpath(f, OUT)}")
print("zip ready")
