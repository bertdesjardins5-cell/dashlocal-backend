"""Google Play feature graphic (1024x500): DashLocal logo + headline over the landing photo."""
import io
import httpx
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1024, 500
RED, FOREST, CHAR, WHITE = (215, 38, 46), (43, 137, 50), (51, 51, 53), (255, 255, 255)
FONTS = "/app/frontend/assets/fonts"
PHOTO = "https://images.unsplash.com/photo-1575663620136-5ebbfcc2c597?crop=entropy&cs=srgb&fm=jpg&q=80&w=1600"
OUT = "/app/store_screenshots/feature/play_feature_graphic.png"


def cover(img: Image.Image) -> Image.Image:
    r = max(W / img.width, H / img.height)
    img = img.resize((int(img.width * r) + 1, int(img.height * r) + 1), Image.LANCZOS)
    x, y = (img.width - W) // 2, (img.height - H) // 2
    return img.crop((x, y, x + W, y + H))


def logo_mark(d: ImageDraw.ImageDraw, x: int, y: int, s: float):
    """Same mark as Logo.tsx (100x100 viewBox): green D + red up-arrow stem."""
    p = lambda pts: [(x + a * s, y + b * s) for a, b in pts]
    d.pieslice([x + 18 * s, y + 14 * s, x + 90 * s, y + 86 * s], -90, 90, fill=FOREST)
    d.rectangle([x + 36 * s, y + 14 * s, x + 54 * s, y + 86 * s], fill=FOREST)
    d.pieslice([x + 36 * s, y + 33 * s, x + 70 * s, y + 67 * s], -90, 90, fill=WHITE)
    d.rectangle([x + 36 * s, y + 33 * s, x + 53 * s, y + 67 * s], fill=WHITE)
    d.polygon(p([(14, 90), (31, 90), (31, 46), (43, 46), (22.5, 10), (2, 46), (14, 46)]), fill=RED)


bg = cover(Image.open(io.BytesIO(httpx.get(PHOTO, timeout=30, follow_redirects=True).content)).convert("RGB"))
bg = bg.filter(ImageFilter.GaussianBlur(2))
shade = Image.new("RGB", (W, H), (10, 10, 10))
mask = Image.linear_gradient("L").rotate(90).resize((W, H))  # dark on the left (text side), photo shows on the right
bg = Image.composite(shade, bg, mask.point(lambda v: int(70 + v * 0.7)))
d = ImageDraw.Draw(bg)

# white logo plate
d.rounded_rectangle([56, 52, 330, 150], radius=18, fill=WHITE)
logo_mark(d, 70, 60, 0.82)
wf = ImageFont.truetype(f"{FONTS}/Poppins-SemiBold.ttf", 34)
d.text((160, 64), "DASH", font=wf, fill=RED)
d.text((160, 102), "LOCAL", font=wf, fill=CHAR)

hf = ImageFont.truetype(f"{FONTS}/Poppins-SemiBold.ttf", 54)
d.text((56, 190), "Same-day local", font=hf, fill=WHITE)
d.text((56, 254), "delivery, tracked live.", font=hf, fill=WHITE)
sf = ImageFont.truetype(f"{FONTS}/Poppins-Medium.ttf", 24)
d.text((58, 340), "For shops, pharmacies and their customers", font=sf, fill=(230, 230, 230))
d.rounded_rectangle([56, 396, 56 + 40 + int(d.textlength("Kitchener-Waterloo, ON", font=sf)), 446], radius=12, fill=FOREST)
d.text((76, 405), "Kitchener-Waterloo, ON", font=sf, fill=WHITE)
bg.save(OUT, optimize=True)
print("saved", OUT, bg.size)
