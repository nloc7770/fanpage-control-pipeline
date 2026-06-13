"""Generate Facebook profile picture and cover photo for Skinny Dad | Dev & Gym.

Renders two PNG assets procedurally with PIL (no network, no AI):

  _storage_data/fanpage_assets/
    skinnydad_profile.png   500 x 500
    skinnydad_cover.png     1640 x 924

Design direction
----------------
* Dark background (#0a0a0f) with neon green (#39ff14) accent and subtle blue (#4488ff)
* Terminal/IDE aesthetic with grid/scanline textures
* Profile: "SD" monogram with dumbbell + code bracket silhouette
* Cover: "SKINNY DAD" + "Dev & Gym" with laptop, dumbbell, baby silhouettes,
  progress bar 50->60kg, tagline "Code by day. Gym by night. Dad 24/7."
* Critical text in centre 60% for mobile crop safety
"""

from __future__ import annotations

import math
import os
import random
from glob import glob
from pathlib import Path
from typing import Sequence

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# --------------------------------------------------------------------------- #
# Paths & constants
# --------------------------------------------------------------------------- #

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "_storage_data" / "fanpage_assets"

PROFILE_SIZE = (500, 500)
COVER_SIZE = (1640, 924)

# Color palette
BG_DARK = (10, 10, 15)
NEON_GREEN = (57, 255, 20)
SUBTLE_BLUE = (68, 136, 255)
DIM_GREEN = (30, 130, 10)
TEXT_WHITE = (235, 240, 245)
TEXT_DIM = (140, 150, 160)

# --------------------------------------------------------------------------- #
# Font resolution
# --------------------------------------------------------------------------- #

FONT_PREFERENCE: Sequence[str] = (
    "JetBrainsMono-Bold.ttf",
    "FiraCode-Bold.ttf",
    "SourceCodePro-Bold.ttf",
    "RobotoMono-Bold.ttf",
    "Inter-Black.ttf",
    "Inter-Bold.ttf",
    "NotoSans-Black.ttf",
    "NotoSans-Bold.ttf",
    "DejaVuSans-Bold.ttf",
    "LiberationSans-Bold.ttf",
)


def _find_font_path() -> str:
    candidates: list[str] = []
    for root in ("/usr/share/fonts", "/usr/local/share/fonts", os.path.expanduser("~/.fonts")):
        candidates.extend(glob(os.path.join(root, "**", "*.ttf"), recursive=True))
        candidates.extend(glob(os.path.join(root, "**", "*.otf"), recursive=True))

    by_name = {os.path.basename(p): p for p in candidates}
    for wanted in FONT_PREFERENCE:
        if wanted in by_name:
            return by_name[wanted]

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


def add_scanlines(img: Image.Image, spacing: int = 4, opacity: float = 0.08) -> Image.Image:
    """Add horizontal scanline texture for terminal aesthetic."""
    w, h = img.size
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    alpha = int(255 * opacity)
    for y in range(0, h, spacing):
        draw.line([(0, y), (w, y)], fill=(0, 0, 0, alpha), width=1)
    base = img.convert("RGBA")
    return Image.alpha_composite(base, layer)


def add_grid(img: Image.Image, spacing: int = 40, opacity: float = 0.06) -> Image.Image:
    """Add subtle grid overlay for techy feel."""
    w, h = img.size
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    alpha = int(255 * opacity)
    color = (NEON_GREEN[0], NEON_GREEN[1], NEON_GREEN[2], alpha)
    for x in range(0, w, spacing):
        draw.line([(x, 0), (x, h)], fill=color, width=1)
    for y in range(0, h, spacing):
        draw.line([(0, y), (w, y)], fill=color, width=1)
    base = img.convert("RGBA")
    return Image.alpha_composite(base, layer)


def add_noise(img: Image.Image, opacity: float = 0.04) -> Image.Image:
    w, h = img.size
    rng = random.Random(0xDEADBEEF ^ (w * 1315423911) ^ h)
    noise = Image.new("L", (w, h))
    pixels = bytes(rng.randint(0, 255) for _ in range(w * h))
    noise.frombytes(pixels)
    noise_rgba = Image.merge("RGBA", (noise, noise, noise, Image.new("L", (w, h), int(255 * opacity))))
    base = img.convert("RGBA")
    return Image.alpha_composite(base, noise_rgba)


def draw_text_centered_x(
    draw: ImageDraw.ImageDraw,
    text: str,
    cx: int,
    y: int,
    font_obj: ImageFont.FreeTypeFont,
    fill: tuple[int, ...],
    shadow: tuple[int, int, int] | None = None,
    shadow_offset: tuple[int, int] = (2, 3),
) -> tuple[int, int, int, int]:
    bbox = draw.textbbox((0, 0), text, font=font_obj)
    tw = bbox[2] - bbox[0]
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
    fill: tuple[int, ...],
    shadow: tuple[int, int, int] | None = None,
    shadow_offset: tuple[int, int] = (2, 3),
) -> tuple[int, int, int, int]:
    bbox = draw.textbbox((0, 0), text, font=font_obj)
    px = x - bbox[0]
    py = y - bbox[1]
    if shadow is not None:
        draw.text((px + shadow_offset[0], py + shadow_offset[1]), text, font=font_obj, fill=shadow)
    draw.text((px, py), text, font=font_obj, fill=fill)
    return (px + bbox[0], py + bbox[1], px + bbox[2], py + bbox[3])


# --------------------------------------------------------------------------- #
# Silhouettes for cover
# --------------------------------------------------------------------------- #


def draw_dumbbell(
    draw: ImageDraw.ImageDraw,
    center: tuple[float, float],
    width: float,
    color: tuple[int, ...],
) -> None:
    """Draw a simple dumbbell silhouette."""
    cx, cy = center
    bar_h = width * 0.08
    plate_w = width * 0.12
    plate_h = width * 0.35

    # Bar
    draw.rectangle(
        (cx - width / 2, cy - bar_h / 2, cx + width / 2, cy + bar_h / 2),
        fill=color,
    )
    # Left plate
    draw.rectangle(
        (cx - width / 2 - plate_w * 0.3, cy - plate_h / 2,
         cx - width / 2 + plate_w, cy + plate_h / 2),
        fill=color,
    )
    # Right plate
    draw.rectangle(
        (cx + width / 2 - plate_w, cy - plate_h / 2,
         cx + width / 2 + plate_w * 0.3, cy + plate_h / 2),
        fill=color,
    )


def draw_laptop(
    draw: ImageDraw.ImageDraw,
    center: tuple[float, float],
    width: float,
    color: tuple[int, ...],
) -> None:
    """Draw a laptop silhouette (screen + base)."""
    cx, cy = center
    screen_w = width * 0.8
    screen_h = width * 0.55
    base_h = width * 0.06

    # Screen
    draw.rectangle(
        (cx - screen_w / 2, cy - screen_h / 2,
         cx + screen_w / 2, cy + screen_h / 2),
        fill=color,
    )
    # Screen inner (darker for screen area)
    margin = width * 0.05
    draw.rectangle(
        (cx - screen_w / 2 + margin, cy - screen_h / 2 + margin,
         cx + screen_w / 2 - margin, cy + screen_h / 2 - margin),
        fill=(BG_DARK[0] + 5, BG_DARK[1] + 5, BG_DARK[2] + 10, 200),
    )
    # Code lines on screen
    line_color = (NEON_GREEN[0], NEON_GREEN[1], NEON_GREEN[2], 150)
    line_y_start = cy - screen_h / 2 + margin + 8
    for i in range(5):
        line_w = random.Random(i + 42).randint(int(screen_w * 0.3), int(screen_w * 0.7))
        ly = line_y_start + i * (screen_h * 0.12)
        draw.rectangle(
            (cx - screen_w / 2 + margin + 8, ly,
             cx - screen_w / 2 + margin + 8 + line_w, ly + 4),
            fill=line_color,
        )
    # Base/keyboard
    draw.polygon(
        [(cx - width / 2, cy + screen_h / 2 + 2),
         (cx + width / 2, cy + screen_h / 2 + 2),
         (cx + width / 2 - width * 0.05, cy + screen_h / 2 + base_h + 2),
         (cx - width / 2 + width * 0.05, cy + screen_h / 2 + base_h + 2)],
        fill=color,
    )


def draw_baby_figure(
    draw: ImageDraw.ImageDraw,
    center: tuple[float, float],
    scale: float,
    color: tuple[int, ...],
) -> None:
    """Draw a simplified child/toddler silhouette."""
    cx, cy = center
    s = scale

    # Head (proportionally larger for a child)
    head_r = s * 0.22
    head_y = cy - s * 0.55
    draw.ellipse(
        (cx - head_r, head_y - head_r, cx + head_r, head_y + head_r),
        fill=color,
    )
    # Body
    body_pts = [
        (cx - s * 0.18, cy - s * 0.35),
        (cx + s * 0.18, cy - s * 0.35),
        (cx + s * 0.15, cy + s * 0.1),
        (cx - s * 0.15, cy + s * 0.1),
    ]
    draw.polygon(body_pts, fill=color)
    # Legs
    draw.rectangle(
        (cx - s * 0.14, cy + s * 0.1, cx - s * 0.04, cy + s * 0.5),
        fill=color,
    )
    draw.rectangle(
        (cx + s * 0.04, cy + s * 0.1, cx + s * 0.14, cy + s * 0.5),
        fill=color,
    )
    # Arms out (toddler reaching)
    arm_pts_l = [
        (cx - s * 0.18, cy - s * 0.28),
        (cx - s * 0.40, cy - s * 0.15),
        (cx - s * 0.38, cy - s * 0.08),
        (cx - s * 0.15, cy - s * 0.20),
    ]
    draw.polygon(arm_pts_l, fill=color)
    arm_pts_r = [
        (cx + s * 0.18, cy - s * 0.28),
        (cx + s * 0.40, cy - s * 0.15),
        (cx + s * 0.38, cy - s * 0.08),
        (cx + s * 0.15, cy - s * 0.20),
    ]
    draw.polygon(arm_pts_r, fill=color)


def draw_progress_bar(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    width: int,
    height: int,
    progress: float,
    bg_color: tuple[int, ...],
    fill_color: tuple[int, ...],
    border_color: tuple[int, ...],
    label_left: str = "50kg",
    label_right: str = "60kg",
    font_obj: ImageFont.FreeTypeFont | None = None,
) -> None:
    """Draw a progress bar with labels."""
    # Background
    draw.rectangle((x, y, x + width, y + height), fill=bg_color, outline=border_color, width=2)
    # Fill
    fill_w = int(width * progress)
    if fill_w > 0:
        draw.rectangle((x + 2, y + 2, x + fill_w - 2, y + height - 2), fill=fill_color)
    # Glow on the fill edge
    if fill_w > 4:
        for i in range(3):
            alpha = 150 - i * 50
            glow_color = (fill_color[0], fill_color[1], fill_color[2], alpha)
            draw.line(
                [(x + fill_w - 2 - i, y + 2), (x + fill_w - 2 - i, y + height - 2)],
                fill=glow_color, width=1,
            )
    # Labels
    if font_obj:
        draw.text((x, y + height + 5), label_left, font=font_obj, fill=TEXT_DIM)
        bbox_r = draw.textbbox((0, 0), label_right, font=font_obj)
        rw = bbox_r[2] - bbox_r[0]
        draw.text((x + width - rw, y + height + 5), label_right, font=font_obj, fill=TEXT_DIM)


# --------------------------------------------------------------------------- #
# Profile builder
# --------------------------------------------------------------------------- #


def build_profile() -> Image.Image:
    w, h = PROFILE_SIZE
    # Dark base
    base = Image.new("RGB", (w, h), BG_DARK)
    base = base.convert("RGBA")

    # Subtle grid
    base = add_grid(base, spacing=30, opacity=0.04)

    # Central neon green glow
    glow = radial_glow((w, h), (w * 0.5, h * 0.5), radius=220,
                       color=NEON_GREEN, intensity=0.18)
    base = Image.alpha_composite(base, glow)

    # Secondary blue glow offset
    glow2 = radial_glow((w, h), (w * 0.65, h * 0.35), radius=140,
                        color=SUBTLE_BLUE, intensity=0.10)
    base = Image.alpha_composite(base, glow2)

    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)

    # Dumbbell silhouette behind text (subtle, dim)
    dumbbell_color = (NEON_GREEN[0], NEON_GREEN[1], NEON_GREEN[2], 35)
    draw_dumbbell(od, center=(w * 0.5, h * 0.72), width=280, color=dumbbell_color)

    # Code brackets "{ }" silhouette behind text
    bracket_font = font(160)
    bracket_color = (SUBTLE_BLUE[0], SUBTLE_BLUE[1], SUBTLE_BLUE[2], 30)
    od.text((w * 0.08, h * 0.25), "{", font=bracket_font, fill=bracket_color)
    od.text((w * 0.68, h * 0.25), "}", font=bracket_font, fill=bracket_color)

    base = Image.alpha_composite(base, overlay)

    # Main "SD" monogram
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    mono_font = font(200)
    # Shadow/glow layer
    draw_text_centered_x(od, "SD", cx=w // 2, y=int(h * 0.22),
                         font_obj=mono_font,
                         fill=(NEON_GREEN[0], NEON_GREEN[1], NEON_GREEN[2], 255),
                         shadow=(0, 40, 0),
                         shadow_offset=(4, 5))
    base = Image.alpha_composite(base, overlay)

    # Subtitle
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    sub_font = font(28)
    draw_text_centered_x(od, "SKINNY DAD", cx=w // 2, y=int(h * 0.62),
                         font_obj=sub_font, fill=TEXT_WHITE)
    tiny_font = font(22)
    draw_text_centered_x(od, "Dev & Gym", cx=w // 2, y=int(h * 0.72),
                         font_obj=tiny_font, fill=TEXT_DIM)
    base = Image.alpha_composite(base, overlay)

    # Decorative underline
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.line([(w * 0.30, h * 0.80), (w * 0.70, h * 0.80)],
            fill=(NEON_GREEN[0], NEON_GREEN[1], NEON_GREEN[2], 180), width=2)
    base = Image.alpha_composite(base, overlay)

    # Scanlines + noise
    base = add_scanlines(base, spacing=3, opacity=0.05)
    base = add_noise(base, opacity=0.03)

    return base


# --------------------------------------------------------------------------- #
# Cover builder
# --------------------------------------------------------------------------- #


def build_cover() -> Image.Image:
    w, h = COVER_SIZE
    # Dark gradient base (slightly lighter at top for depth)
    base = vertical_gradient((w, h), top=(12, 12, 20), bottom=(6, 6, 10))
    base = base.convert("RGBA")

    # Grid texture
    base = add_grid(base, spacing=50, opacity=0.035)

    # Green glow left side (where text is)
    glow = radial_glow((w, h), (w * 0.35, h * 0.45), radius=450,
                       color=NEON_GREEN, intensity=0.08)
    base = Image.alpha_composite(base, glow)

    # Blue glow right side (where silhouettes are)
    glow2 = radial_glow((w, h), (w * 0.72, h * 0.50), radius=380,
                        color=SUBTLE_BLUE, intensity=0.07)
    base = Image.alpha_composite(base, glow2)

    # --- Right side: silhouettes --- #
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)

    # Laptop silhouette (upper right area)
    laptop_color = (SUBTLE_BLUE[0], SUBTLE_BLUE[1], SUBTLE_BLUE[2], 80)
    draw_laptop(od, center=(w * 0.72, h * 0.30), width=220, color=laptop_color)

    # Dumbbell silhouette (middle right)
    dumbbell_color = (NEON_GREEN[0], NEON_GREEN[1], NEON_GREEN[2], 70)
    draw_dumbbell(od, center=(w * 0.78, h * 0.58), width=200, color=dumbbell_color)

    # Baby/child figure (lower right)
    baby_color = (TEXT_WHITE[0], TEXT_WHITE[1], TEXT_WHITE[2], 60)
    draw_baby_figure(od, center=(w * 0.68, h * 0.78), scale=120, color=baby_color)

    base = Image.alpha_composite(base, overlay)

    # --- Left side: text (within centre 60% = 20%-80% of width) --- #
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)

    safe_left = int(w * 0.20)
    title_y = int(h * 0.18)

    # "SKINNY DAD" large
    title_font = font(110)
    draw_text_left(od, "SKINNY DAD", x=safe_left, y=title_y,
                   font_obj=title_font,
                   fill=NEON_GREEN,
                   shadow=(0, 30, 0),
                   shadow_offset=(3, 4))

    # "Dev & Gym" below
    sub_font = font(48)
    draw_text_left(od, "Dev & Gym", x=safe_left, y=title_y + 130,
                   font_obj=sub_font, fill=SUBTLE_BLUE)

    # Separator line
    od.line([(safe_left, title_y + 200), (safe_left + 300, title_y + 200)],
            fill=(NEON_GREEN[0], NEON_GREEN[1], NEON_GREEN[2], 150), width=2)

    # Progress bar (50kg -> 60kg)
    bar_y = title_y + 230
    bar_font = font(18)
    draw_progress_bar(
        od,
        x=safe_left,
        y=bar_y,
        width=320,
        height=26,
        progress=0.65,  # ~56.5kg out of the range visually
        bg_color=(20, 20, 30, 200),
        fill_color=(NEON_GREEN[0], NEON_GREEN[1], NEON_GREEN[2], 220),
        border_color=(NEON_GREEN[0], NEON_GREEN[1], NEON_GREEN[2], 100),
        label_left="50kg",
        label_right="60kg",
        font_obj=bar_font,
    )

    # Tagline
    tagline_font = font(28)
    tagline_y = bar_y + 80
    draw_text_left(od, "Code by day. Gym by night. Dad 24/7.",
                   x=safe_left, y=tagline_y,
                   font_obj=tagline_font, fill=TEXT_DIM)

    base = Image.alpha_composite(base, overlay)

    # --- Terminal prompt decoration (bottom center, in safe area) --- #
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    term_font = font(20)
    term_y = int(h * 0.88)
    term_text = "$ ./transform --from skinny --to strong --parallel"
    draw_text_centered_x(od, term_text, cx=w // 2, y=term_y,
                         font_obj=term_font,
                         fill=(NEON_GREEN[0], NEON_GREEN[1], NEON_GREEN[2], 130))
    base = Image.alpha_composite(base, overlay)

    # Scanlines + noise for terminal aesthetic
    base = add_scanlines(base, spacing=3, opacity=0.04)
    base = add_noise(base, opacity=0.03)

    return base


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def _save(img: Image.Image, path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    final = img.convert("RGB")
    final.save(path, format="PNG", optimize=False, compress_level=6)
    return path.stat().st_size


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    targets = [
        ("skinnydad_profile.png", build_profile),
        ("skinnydad_cover.png", build_cover),
    ]
    print(f"Using font: {FONT_PATH}")
    for name, builder in targets:
        img = builder()
        out = OUTPUT_DIR / name
        size = _save(img, out)
        print(f"  wrote {out}  ({size/1024:.1f} KiB, {img.size[0]}x{img.size[1]})")
    print("Done.")


if __name__ == "__main__":
    main()
