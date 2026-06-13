"""Generate Facebook profile pictures and cover photos for two niche fanpages.

Renders four PNG assets entirely procedurally with PIL (no network, no AI):

  _storage_data/fanpage_assets/
    fishing_profile.png   500 x 500   (Săn Cá Khổng Lồ - "SCKL")
    fishing_cover.png     1640 x 924
    survival_profile.png  500 x 500   (Một Mình Giữa Rừng - "MMGR")
    survival_cover.png    1640 x 924

Design notes
------------
* Fishing: navy -> deep cyan vertical gradient; fish + rod silhouettes;
  wave shapes along the bottom of the cover.
* Survival: forest green -> near-black gradient with orange firelight accent;
  campfire, lone figure, tent and tree silhouettes; soft center vignette.

The script is idempotent (it overwrites previous outputs on each run) and
designed to keep critical typography inside the centre 60% horizontally of
the cover so that Facebook's mobile crop preserves it.
"""

from __future__ import annotations

import math
import os
import random
from glob import glob
from pathlib import Path
from typing import Iterable, Sequence

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "_storage_data" / "fanpage_assets"

PROFILE_SIZE = (500, 500)
COVER_SIZE = (1640, 924)


# --------------------------------------------------------------------------- #
# Font resolution
# --------------------------------------------------------------------------- #

# Ordered preference: heaviest weight first, then Vietnamese-safe fallbacks.
FONT_PREFERENCE: Sequence[str] = (
    "Inter-Black.ttf",
    "Inter-Bold.ttf",
    "NotoSans-Black.ttf",
    "NotoSansDisplay-Bold.ttf",
    "NotoSans-Bold.ttf",
    "DejaVuSans-Bold.ttf",
    "LiberationSans-Bold.ttf",
)


def _find_font_path() -> str:
    """Pick the heaviest bold/black font with Vietnamese coverage."""
    candidates: list[str] = []
    for root in ("/usr/share/fonts", "/usr/local/share/fonts", os.path.expanduser("~/.fonts")):
        candidates.extend(glob(os.path.join(root, "**", "*.ttf"), recursive=True))
        candidates.extend(glob(os.path.join(root, "**", "*.otf"), recursive=True))

    by_name = {os.path.basename(p): p for p in candidates}
    for wanted in FONT_PREFERENCE:
        if wanted in by_name:
            return by_name[wanted]

    # Last-ditch fallback: any file whose name contains "Bold".
    bold = [p for p in candidates if "Bold" in os.path.basename(p)]
    if bold:
        return bold[0]
    if candidates:
        return candidates[0]
    raise RuntimeError("No TrueType/OpenType fonts found on system")


FONT_PATH = _find_font_path()


def font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_PATH, size=size)


# --------------------------------------------------------------------------- #
# Drawing helpers
# --------------------------------------------------------------------------- #


def _lerp(a: int, b: int, t: float) -> int:
    return int(round(a + (b - a) * t))


def vertical_gradient(
    size: tuple[int, int],
    top: tuple[int, int, int],
    bottom: tuple[int, int, int],
) -> Image.Image:
    """Cheap, smooth vertical gradient using band-by-band fills."""
    w, h = size
    img = Image.new("RGB", (w, h), top)
    draw = ImageDraw.Draw(img)
    for y in range(h):
        t = y / max(h - 1, 1)
        c = (_lerp(top[0], bottom[0], t), _lerp(top[1], bottom[1], t), _lerp(top[2], bottom[2], t))
        draw.line([(0, y), (w, y)], fill=c)
    return img


def radial_glow(
    size: tuple[int, int],
    center: tuple[float, float],
    radius: float,
    color: tuple[int, int, int],
    intensity: float = 0.7,
) -> Image.Image:
    """RGBA layer with a soft radial glow at `center`."""
    w, h = size
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    px = layer.load()
    cx, cy = center
    r2 = radius * radius
    for y in range(h):
        for x in range(w):
            dx = x - cx
            dy = y - cy
            d2 = dx * dx + dy * dy
            if d2 > r2:
                continue
            t = 1.0 - math.sqrt(d2) / radius
            a = int(255 * intensity * (t ** 2))
            if a > 0:
                px[x, y] = (color[0], color[1], color[2], a)
    return layer


def add_noise(img: Image.Image, opacity: float = 0.05) -> Image.Image:
    """Overlay a low-opacity monochrome noise layer for subtle grain."""
    w, h = img.size
    rng = random.Random(0xC0FFEE ^ (w * 1315423911) ^ h)
    noise = Image.new("L", (w, h))
    pixels = bytes(rng.randint(0, 255) for _ in range(w * h))
    noise.frombytes(pixels)
    noise_rgba = Image.merge("RGBA", (noise, noise, noise, Image.new("L", (w, h), int(255 * opacity))))
    base = img.convert("RGBA")
    return Image.alpha_composite(base, noise_rgba)


def vignette(img: Image.Image, strength: float = 0.55) -> Image.Image:
    """Darken the corners with a centered radial falloff."""
    w, h = img.size
    mask = Image.new("L", (w, h), 0)
    px = mask.load()
    cx, cy = w / 2, h / 2
    max_d = math.hypot(cx, cy)
    for y in range(h):
        for x in range(w):
            d = math.hypot(x - cx, y - cy) / max_d
            d = max(0.0, min(1.0, d))
            px[x, y] = int(255 * (d ** 2) * strength)
    dark = Image.new("RGBA", (w, h), (0, 0, 0, 255))
    dark.putalpha(mask)
    return Image.alpha_composite(img.convert("RGBA"), dark)


def draw_text_centered_x(
    draw: ImageDraw.ImageDraw,
    text: str,
    cx: int,
    y: int,
    font_obj: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int],
    shadow: tuple[int, int, int] | None = (0, 0, 0),
    shadow_offset: tuple[int, int] = (3, 4),
) -> tuple[int, int, int, int]:
    bbox = draw.textbbox((0, 0), text, font=font_obj)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    x = cx - tw // 2 - bbox[0]
    ty = y - bbox[1]
    if shadow is not None:
        draw.text((x + shadow_offset[0], ty + shadow_offset[1]), text, font=font_obj, fill=shadow)
    draw.text((x, ty), text, font=font_obj, fill=fill)
    return (x + bbox[0], ty + bbox[1], x + bbox[2], ty + bbox[3])


def draw_text_left(
    draw: ImageDraw.ImageDraw,
    text: str,
    x: int,
    y: int,
    font_obj: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int],
    shadow: tuple[int, int, int] | None = (0, 0, 0),
    shadow_offset: tuple[int, int] = (3, 4),
) -> tuple[int, int, int, int]:
    bbox = draw.textbbox((0, 0), text, font=font_obj)
    px = x - bbox[0]
    py = y - bbox[1]
    if shadow is not None:
        draw.text((px + shadow_offset[0], py + shadow_offset[1]), text, font=font_obj, fill=shadow)
    draw.text((px, py), text, font=font_obj, fill=fill)
    return (px + bbox[0], py + bbox[1], px + bbox[2], py + bbox[3])


# --------------------------------------------------------------------------- #
# Silhouettes
# --------------------------------------------------------------------------- #


def silhouette_fish(
    layer_size: tuple[int, int],
    center: tuple[float, float],
    length: float,
    angle_deg: float = -25.0,
    color: tuple[int, int, int, int] = (0, 0, 0, 220),
) -> Image.Image:
    """Stylised leaping fish silhouette (body + tail fin)."""
    layer = Image.new("RGBA", layer_size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    cx, cy = center
    L = length
    # Build fish shape in local coords with nose to the right.
    pts_local = [
        (0.50, 0.00),   # nose
        (0.25, -0.18),
        (-0.10, -0.20),
        (-0.40, -0.10),
        (-0.55, -0.22),  # tail upper
        (-0.45, 0.00),
        (-0.55, 0.22),   # tail lower
        (-0.40, 0.10),
        (-0.10, 0.20),
        (0.25, 0.18),
    ]
    a = math.radians(angle_deg)
    cos_a, sin_a = math.cos(a), math.sin(a)
    pts = []
    for px, py in pts_local:
        x = px * L
        y = py * L
        rx = x * cos_a - y * sin_a + cx
        ry = x * sin_a + y * cos_a + cy
        pts.append((rx, ry))
    draw.polygon(pts, fill=color)
    # Eye highlight
    eye_x = cx + math.cos(a) * 0.35 * L - math.sin(a) * (-0.04) * L
    eye_y = cy + math.sin(a) * 0.35 * L + math.cos(a) * (-0.04) * L
    r = max(2, int(L * 0.025))
    draw.ellipse((eye_x - r, eye_y - r, eye_x + r, eye_y + r), fill=(255, 255, 255, 230))
    return layer


def silhouette_rod(
    layer_size: tuple[int, int],
    base: tuple[float, float],
    tip: tuple[float, float],
    bend: float = 0.18,
    width: int = 6,
    color: tuple[int, int, int, int] = (0, 0, 0, 230),
    line_to: tuple[float, float] | None = None,
) -> Image.Image:
    """Bent rod drawn as a quadratic-bezier polyline plus optional fishing line."""
    layer = Image.new("RGBA", layer_size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    bx, by = base
    tx, ty = tip
    # Control point: perpendicular offset from midpoint.
    mx, my = (bx + tx) / 2, (by + ty) / 2
    dx, dy = tx - bx, ty - by
    length = math.hypot(dx, dy) or 1.0
    nx, ny = -dy / length, dx / length
    cpx = mx + nx * length * bend
    cpy = my + ny * length * bend
    steps = 40
    pts = []
    for i in range(steps + 1):
        t = i / steps
        x = (1 - t) ** 2 * bx + 2 * (1 - t) * t * cpx + t ** 2 * tx
        y = (1 - t) ** 2 * by + 2 * (1 - t) * t * cpy + t ** 2 * ty
        pts.append((x, y))
    # Taper width along rod.
    for i in range(len(pts) - 1):
        w = max(1, int(width * (1 - i / len(pts)) + 1))
        draw.line([pts[i], pts[i + 1]], fill=color, width=w)
    # Handle nub
    draw.ellipse((bx - width, by - width, bx + width, by + width), fill=color)
    # Fishing line
    if line_to is not None:
        draw.line([(tx, ty), line_to], fill=(255, 255, 255, 180), width=2)
    return layer


def silhouette_angler(
    layer_size: tuple[int, int],
    base: tuple[float, float],
    scale: float = 200.0,
    color: tuple[int, int, int, int] = (0, 0, 0, 235),
) -> Image.Image:
    """Standing angler silhouette holding a rod up and to the right."""
    layer = Image.new("RGBA", layer_size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    bx, by = base  # feet center
    s = scale

    # Body (torso + legs as a single tapered polygon)
    body_pts = [
        (bx - 0.18 * s, by),
        (bx + 0.18 * s, by),
        (bx + 0.13 * s, by - 0.45 * s),
        (bx + 0.18 * s, by - 0.80 * s),
        (bx - 0.18 * s, by - 0.80 * s),
        (bx - 0.13 * s, by - 0.45 * s),
    ]
    draw.polygon(body_pts, fill=color)
    # Head
    hx, hy = bx, by - 0.95 * s
    hr = 0.13 * s
    draw.ellipse((hx - hr, hy - hr, hx + hr, hy + hr), fill=color)
    # Forward arm reaching for the rod
    arm_pts = [
        (bx + 0.10 * s, by - 0.75 * s),
        (bx + 0.55 * s, by - 0.70 * s),
        (bx + 0.55 * s, by - 0.60 * s),
        (bx + 0.10 * s, by - 0.65 * s),
    ]
    draw.polygon(arm_pts, fill=color)
    # Back arm (lower)
    back_arm = [
        (bx - 0.10 * s, by - 0.75 * s),
        (bx - 0.35 * s, by - 0.55 * s),
        (bx - 0.30 * s, by - 0.45 * s),
        (bx - 0.05 * s, by - 0.65 * s),
    ]
    draw.polygon(back_arm, fill=color)
    return layer


def silhouette_tree(
    draw: ImageDraw.ImageDraw,
    base: tuple[float, float],
    height: float,
    width: float,
    color: tuple[int, int, int, int],
    layers: int = 4,
) -> None:
    """Conifer silhouette using stacked triangles + a trunk."""
    bx, by = base
    trunk_w = max(2, int(width * 0.10))
    draw.rectangle(
        (bx - trunk_w, by - height * 0.12, bx + trunk_w, by),
        fill=color,
    )
    layer_h = height * 0.30
    top = by - height
    overlap = layer_h * 0.55
    for i in range(layers):
        y_top = top + i * (layer_h - overlap)
        y_bottom = y_top + layer_h
        w = width * (0.55 + 0.15 * i)
        draw.polygon(
            [(bx, y_top), (bx - w / 2, y_bottom), (bx + w / 2, y_bottom)],
            fill=color,
        )


def silhouette_tent(
    draw: ImageDraw.ImageDraw,
    base_center: tuple[float, float],
    width: float,
    height: float,
    color: tuple[int, int, int, int],
) -> None:
    bx, by = base_center
    apex = (bx, by - height)
    left = (bx - width / 2, by)
    right = (bx + width / 2, by)
    draw.polygon([apex, left, right], fill=color)
    # Door slit
    door_pts = [
        (bx, by - height * 0.92),
        (bx - width * 0.13, by),
        (bx + width * 0.13, by),
    ]
    draw.polygon(door_pts, fill=(0, 0, 0, 255))


def silhouette_campfire(
    layer_size: tuple[int, int],
    base: tuple[float, float],
    scale: float = 80.0,
) -> Image.Image:
    """Logs (X) + flame shapes, returned as an RGBA layer."""
    layer = Image.new("RGBA", layer_size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    bx, by = base
    s = scale
    # Logs (two crossed bars)
    log_color = (40, 25, 18, 255)
    log_thickness = max(3, int(s * 0.10))
    draw.line(
        ((bx - 0.55 * s, by + 0.05 * s), (bx + 0.55 * s, by - 0.05 * s)),
        fill=log_color, width=log_thickness,
    )
    draw.line(
        ((bx - 0.55 * s, by - 0.05 * s), (bx + 0.55 * s, by + 0.05 * s)),
        fill=log_color, width=log_thickness,
    )
    # Flame: layered teardrops (outer dark orange -> inner yellow)
    flames = [
        ((bx - 0.45 * s, by - 0.05 * s), (bx, by - 1.10 * s), (bx + 0.45 * s, by - 0.05 * s), (255, 120, 25, 235)),
        ((bx - 0.30 * s, by - 0.05 * s), (bx + 0.05 * s, by - 0.85 * s), (bx + 0.30 * s, by - 0.05 * s), (255, 175, 50, 240)),
        ((bx - 0.16 * s, by - 0.05 * s), (bx, by - 0.55 * s), (bx + 0.16 * s, by - 0.05 * s), (255, 230, 120, 250)),
    ]
    for p1, p2, p3, color in flames:
        draw.polygon([p1, p2, p3], fill=color)
    return layer


def silhouette_seated_figure(
    layer_size: tuple[int, int],
    base: tuple[float, float],
    scale: float = 90.0,
    color: tuple[int, int, int, int] = (0, 0, 0, 240),
) -> Image.Image:
    """Person sitting cross-legged (side profile) facing right."""
    layer = Image.new("RGBA", layer_size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    bx, by = base
    s = scale
    # Lap / legs (low ellipse-like polygon)
    legs = [
        (bx - 0.70 * s, by),
        (bx + 0.70 * s, by),
        (bx + 0.85 * s, by - 0.20 * s),
        (bx - 0.10 * s, by - 0.30 * s),
        (bx - 0.85 * s, by - 0.10 * s),
    ]
    draw.polygon(legs, fill=color)
    # Torso (leaning slightly forward toward the fire on the right)
    torso = [
        (bx - 0.30 * s, by - 0.30 * s),
        (bx + 0.10 * s, by - 0.30 * s),
        (bx + 0.20 * s, by - 0.90 * s),
        (bx - 0.05 * s, by - 1.00 * s),
        (bx - 0.30 * s, by - 0.95 * s),
    ]
    draw.polygon(torso, fill=color)
    # Head
    hx, hy = bx + 0.05 * s, by - 1.10 * s
    hr = 0.18 * s
    draw.ellipse((hx - hr, hy - hr, hx + hr, hy + hr), fill=color)
    # Arm reaching toward the fire
    arm = [
        (bx + 0.15 * s, by - 0.80 * s),
        (bx + 0.65 * s, by - 0.55 * s),
        (bx + 0.65 * s, by - 0.45 * s),
        (bx + 0.15 * s, by - 0.70 * s),
    ]
    draw.polygon(arm, fill=color)
    return layer


# --------------------------------------------------------------------------- #
# Asset builders
# --------------------------------------------------------------------------- #


def build_fishing_profile() -> Image.Image:
    w, h = PROFILE_SIZE
    base = vertical_gradient((w, h), top=(8, 18, 46), bottom=(0, 92, 130))
    base = base.convert("RGBA")

    # Soft moon-like glow upper right
    glow = radial_glow((w, h), (w * 0.78, h * 0.22), radius=180, color=(120, 200, 230), intensity=0.45)
    base = Image.alpha_composite(base, glow)

    # Curved horizon waves at bottom
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    for i, (alpha, y_off, amp) in enumerate([(60, 360, 14), (90, 400, 18), (140, 440, 22)]):
        pts = []
        for x in range(0, w + 1, 8):
            y = y_off + math.sin((x / w) * math.pi * 2 + i) * amp
            pts.append((x, y))
        pts.extend([(w, h), (0, h)])
        od.polygon(pts, fill=(0, 0, 0, alpha))
    base = Image.alpha_composite(base, overlay)

    # Fishing rod silhouette upper-right -> bent down toward center
    rod = silhouette_rod(
        (w, h),
        base=(w * 0.92, h * 0.18),
        tip=(w * 0.55, h * 0.55),
        bend=0.22,
        width=7,
        color=(0, 0, 0, 235),
        line_to=(w * 0.30, h * 0.78),
    )
    base = Image.alpha_composite(base, rod)

    # Jumping fish lower-left
    fish = silhouette_fish(
        (w, h),
        center=(w * 0.30, h * 0.72),
        length=180,
        angle_deg=-30,
        color=(0, 0, 0, 230),
    )
    base = Image.alpha_composite(base, fish)

    # Splash arc near fish
    splash = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    sd = ImageDraw.Draw(splash)
    sd.arc((w * 0.18, h * 0.78, w * 0.45, h * 0.96), start=200, end=340,
           fill=(180, 230, 255, 200), width=4)
    base = Image.alpha_composite(base, splash)

    # Wordmark "SCKL" with a subtle hook ornament
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    title_font = font(120)
    sub_font = font(28)
    draw_text_centered_x(od, "SCKL", cx=w // 2, y=int(h * 0.18),
                         font_obj=title_font, fill=(255, 220, 110), shadow=(0, 0, 0))
    draw_text_centered_x(od, "SĂN CÁ KHỔNG LỒ", cx=w // 2, y=int(h * 0.46),
                         font_obj=sub_font, fill=(235, 245, 255), shadow=(0, 0, 0))
    # Decorative underline
    od.line([(w * 0.30, h * 0.55), (w * 0.70, h * 0.55)], fill=(255, 220, 110, 200), width=3)
    base = Image.alpha_composite(base, overlay)

    base = add_noise(base, opacity=0.04)
    return base


def build_fishing_cover() -> Image.Image:
    w, h = COVER_SIZE
    base = vertical_gradient((w, h), top=(6, 16, 44), bottom=(0, 110, 150))
    base = base.convert("RGBA")

    # Big soft sun/moon glow on the right horizon
    glow = radial_glow((w, h), (w * 0.78, h * 0.30), radius=420,
                      color=(150, 215, 240), intensity=0.45)
    base = Image.alpha_composite(base, glow)

    # Layered wave silhouettes at the bottom (centre-friendly)
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    wave_layers = [
        (50, h * 0.78, 28, 1.4, (4, 30, 60, 80)),
        (70, h * 0.83, 36, 1.7, (3, 22, 50, 130)),
        (90, h * 0.88, 44, 2.1, (2, 16, 38, 190)),
        (110, h * 0.93, 52, 2.5, (0, 10, 24, 240)),
    ]
    for _, base_y, amp, freq, color in wave_layers:
        pts = []
        for x in range(0, w + 1, 8):
            y = base_y + math.sin((x / w) * math.pi * 2 * freq) * amp
            pts.append((x, y))
        pts.extend([(w, h), (0, h)])
        od.polygon(pts, fill=color)
    base = Image.alpha_composite(base, overlay)

    # Distant jumping fish (left-of-center, inside safe area)
    fish = silhouette_fish(
        (w, h),
        center=(w * 0.32, h * 0.62),
        length=170,
        angle_deg=-28,
        color=(0, 0, 0, 220),
    )
    base = Image.alpha_composite(base, fish)
    splash = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    sd = ImageDraw.Draw(splash)
    sd.arc((w * 0.24, h * 0.68, w * 0.40, h * 0.80), start=200, end=340,
           fill=(180, 230, 255, 180), width=4)
    base = Image.alpha_composite(base, splash)

    # Angler on right side, with rod and fishing line connecting to fish
    angler = silhouette_angler((w, h), base=(w * 0.78, h * 0.86), scale=240,
                              color=(0, 0, 0, 240))
    base = Image.alpha_composite(base, angler)
    rod = silhouette_rod(
        (w, h),
        base=(w * 0.78 + 50, h * 0.86 - 168),
        tip=(w * 0.55, h * 0.30),
        bend=0.20,
        width=8,
        color=(0, 0, 0, 240),
        line_to=(w * 0.34, h * 0.60),
    )
    base = Image.alpha_composite(base, rod)

    # Title block (left-aligned, safe-area aware)
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    title_x = int(w * 0.22)
    title_y = int(h * 0.28)
    title_font = font(120)
    sub_font = font(42)
    tag_font = font(28)

    draw_text_left(od, "SĂN CÁ", x=title_x, y=title_y,
                   font_obj=title_font, fill=(255, 225, 120), shadow=(0, 0, 0))
    draw_text_left(od, "KHỔNG LỒ", x=title_x, y=title_y + 130,
                   font_obj=title_font, fill=(255, 255, 255), shadow=(0, 0, 0))
    od.rectangle((title_x, title_y + 270, title_x + 80, title_y + 276),
                 fill=(255, 225, 120, 240))
    draw_text_left(od, "Những cú câu thót tim từ khắp thế giới",
                   x=title_x, y=title_y + 295, font_obj=sub_font,
                   fill=(220, 235, 250), shadow=(0, 0, 0))
    draw_text_left(od, "FANPAGE • SCKL", x=title_x, y=title_y + 360,
                   font_obj=tag_font, fill=(150, 200, 230), shadow=None)
    base = Image.alpha_composite(base, overlay)

    base = add_noise(base, opacity=0.04)
    return base


def build_survival_profile() -> Image.Image:
    w, h = PROFILE_SIZE
    base = vertical_gradient((w, h), top=(6, 28, 18), bottom=(0, 0, 0))
    base = base.convert("RGBA")

    # Orange firelight glow lower center
    glow = radial_glow((w, h), (w * 0.50, h * 0.72), radius=220,
                      color=(255, 140, 40), intensity=0.55)
    base = Image.alpha_composite(base, glow)

    # Ground line (subtle)
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.rectangle((0, int(h * 0.85), w, h), fill=(0, 0, 0, 200))
    base = Image.alpha_composite(base, overlay)

    # Seated figure to the left of the fire
    figure = silhouette_seated_figure((w, h), base=(w * 0.32, h * 0.82), scale=110)
    base = Image.alpha_composite(base, figure)

    # Campfire to the right of the figure
    fire = silhouette_campfire((w, h), base=(w * 0.62, h * 0.85), scale=95)
    base = Image.alpha_composite(base, fire)

    # Wordmark "MMGR"
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    title_font = font(110)
    sub_font = font(26)
    draw_text_centered_x(od, "MMGR", cx=w // 2, y=int(h * 0.15),
                         font_obj=title_font, fill=(255, 165, 60), shadow=(0, 0, 0))
    draw_text_centered_x(od, "MỘT MÌNH GIỮA RỪNG", cx=w // 2, y=int(h * 0.42),
                         font_obj=sub_font, fill=(230, 235, 220), shadow=(0, 0, 0))
    od.line([(w * 0.33, h * 0.50), (w * 0.67, h * 0.50)], fill=(255, 165, 60, 200), width=3)
    base = Image.alpha_composite(base, overlay)

    base = add_noise(base, opacity=0.04)
    return base


def build_survival_cover() -> Image.Image:
    w, h = COVER_SIZE
    base = vertical_gradient((w, h), top=(8, 30, 20), bottom=(0, 0, 0))
    base = base.convert("RGBA")

    # Moonlight glow upper center-right
    moon_glow = radial_glow((w, h), (w * 0.66, h * 0.18), radius=320,
                           color=(120, 160, 140), intensity=0.32)
    base = Image.alpha_composite(base, moon_glow)
    # Moon disk
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    mx, my, mr = w * 0.66, h * 0.18, 42
    od.ellipse((mx - mr, my - mr, mx + mr, my + mr), fill=(225, 230, 215, 230))
    base = Image.alpha_composite(base, overlay)

    # Firelight glow lower-left
    fire_glow = radial_glow((w, h), (w * 0.22, h * 0.72), radius=380,
                           color=(255, 140, 40), intensity=0.55)
    base = Image.alpha_composite(base, fire_glow)

    # Tree silhouettes along the bottom
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    ground_y = int(h * 0.92)
    od.rectangle((0, ground_y, w, h), fill=(0, 0, 0, 255))
    tree_color = (0, 0, 0, 255)
    rng = random.Random(7)
    x = 30
    while x < w - 30:
        height_t = rng.randint(180, 360)
        width_t = rng.randint(120, 200)
        silhouette_tree(od, (x, ground_y + 8), height_t, width_t, tree_color, layers=5)
        x += rng.randint(70, 130)
    base = Image.alpha_composite(base, overlay)

    # Tent on the right
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    silhouette_tent(od, base_center=(w * 0.82, ground_y), width=260, height=180,
                    color=(8, 10, 8, 255))
    base = Image.alpha_composite(base, overlay)

    # Campfire bottom-left with seated figure
    figure = silhouette_seated_figure((w, h), base=(w * 0.14, ground_y - 6), scale=160)
    base = Image.alpha_composite(base, figure)
    fire = silhouette_campfire((w, h), base=(w * 0.24, ground_y - 4), scale=140)
    base = Image.alpha_composite(base, fire)

    # Title block centered
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    title_font = font(120)
    sub_font = font(40)
    tag_font = font(28)

    title_cx = w // 2
    title_y = int(h * 0.22)
    draw_text_centered_x(od, "MỘT MÌNH", cx=title_cx, y=title_y,
                         font_obj=title_font, fill=(255, 175, 70), shadow=(0, 0, 0))
    draw_text_centered_x(od, "GIỮA RỪNG", cx=title_cx, y=title_y + 130,
                         font_obj=title_font, fill=(240, 240, 220), shadow=(0, 0, 0))
    od.rectangle((title_cx - 45, title_y + 275, title_cx + 45, title_y + 281),
                 fill=(255, 175, 70, 230))
    draw_text_centered_x(od, "Sinh tồn 7 ngày, không lương thực, không đồng đội",
                         cx=title_cx, y=title_y + 300, font_obj=sub_font,
                         fill=(215, 220, 200), shadow=(0, 0, 0))
    draw_text_centered_x(od, "FANPAGE • MMGR", cx=title_cx, y=title_y + 370,
                         font_obj=tag_font, fill=(170, 150, 110), shadow=None)
    base = Image.alpha_composite(base, overlay)

    # Subtle vignette to focus center
    base = vignette(base, strength=0.55)
    base = add_noise(base, opacity=0.04)
    return base


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def _save(img: Image.Image, path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, format="PNG", optimize=False, compress_level=6)
    return path.stat().st_size


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    targets = [
        ("fishing_profile.png", build_fishing_profile),
        ("fishing_cover.png", build_fishing_cover),
        ("survival_profile.png", build_survival_profile),
        ("survival_cover.png", build_survival_cover),
    ]
    print(f"Using font: {FONT_PATH}")
    for name, builder in targets:
        img = builder()
        out = OUTPUT_DIR / name
        size = _save(img, out)
        print(f"  wrote {out}  ({size/1024:.1f} KiB, {img.size[0]}x{img.size[1]})")


if __name__ == "__main__":
    main()
