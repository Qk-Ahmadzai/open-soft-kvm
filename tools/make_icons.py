"""Generate the SoftKVM PWA icons.

Run with:  python tools/make_icons.py

Produces:
    softkvm/static/icons/icon-192.png
    softkvm/static/icons/icon-512.png
    softkvm/static/icons/icon-maskable.png
    softkvm/static/icons/favicon.svg
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ICON_DIR = PROJECT_ROOT / "softkvm" / "static" / "icons"

# CasaBlue -> turquoise, matching the CasaOS palette.
START = (13, 108, 243)
END = (0, 209, 178)


def lerp(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def gradient(size: int, angle_deg: float = 135.0) -> Image.Image:
    """Linear gradient image."""
    image = Image.new("RGB", (size, size))
    pixels = image.load()
    radians = math.radians(angle_deg)
    dx, dy = math.cos(radians), math.sin(radians)
    for y in range(size):
        for x in range(size):
            t = (x * dx + y * dy) / (size * (abs(dx) + abs(dy)))
            t = max(0.0, min(1.0, t))
            pixels[x, y] = lerp(START, END, t)
    return image


def house_points(size: int, scale: float = 0.56) -> list[tuple[float, float]]:
    """The SoftKVM house glyph, centred and scaled."""
    # Master shape in a 0-100 box.
    master = [
        (50, 16),
        (92, 51),
        (82, 51),
        (82, 86),
        (18, 86),
        (18, 51),
        (8, 51),
    ]
    offset = (100 - 100 * scale) / 2
    return [
        ((x * scale + offset) / 100 * size, (y * scale + offset) / 100 * size)
        for x, y in master
    ]


def make_icon(size: int, padding: float = 0.0, rounded: bool = True) -> Image.Image:
    """Build one icon: gradient plate + white house."""
    image = gradient(size)

    mask = Image.new("L", (size, size), 0)
    mask_draw = ImageDraw.Draw(mask)
    if rounded:
        mask_draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=int(size * 0.22), fill=255)
    else:
        mask_draw.rectangle([0, 0, size - 1, size - 1], fill=255)
    image.putalpha(mask)

    glyph = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    glyph_draw = ImageDraw.Draw(glyph)
    points = house_points(size, 0.56 * (1 - padding * 2))
    if padding:
        points = [
            (
                x * (1 - padding * 2) + size * padding,
                y * (1 - padding * 2) + size * padding,
            )
            for x, y in points
        ]
    glyph_draw.polygon(points, fill=(255, 255, 255, 255))
    # Door punch-out keeps the glyph readable at small sizes.
    door_w = size * 0.14
    door_h = size * 0.18
    glyph_draw.rectangle(
        [
            size / 2 - door_w / 2,
            size * 0.86 - door_h,
            size / 2 + door_w / 2,
            size * 0.86,
        ],
        fill=(13, 108, 243, 255),
    )

    return Image.alpha_composite(image.convert("RGBA"), glyph)


FAVICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#0d6cf3"/>
      <stop offset="100%" stop-color="#00d1b2"/>
    </linearGradient>
  </defs>
  <rect width="100" height="100" rx="22" fill="url(#g)"/>
  <path fill="#ffffff" d="M50 16 92 51H82v35H18V51H8z"/>
</svg>
"""


def main() -> None:
    ICON_DIR.mkdir(parents=True, exist_ok=True)

    make_icon(192).convert("RGB").save(ICON_DIR / "icon-192.png", optimize=True)
    make_icon(512).convert("RGB").save(ICON_DIR / "icon-512.png", optimize=True)
    # Maskable icons need a safe zone: the glyph is inset and the plate is square.
    make_icon(512, padding=0.12, rounded=False).convert("RGB").save(
        ICON_DIR / "icon-maskable.png", optimize=True
    )
    (ICON_DIR / "favicon.svg").write_text(FAVICON_SVG, encoding="utf-8")

    for path in sorted(ICON_DIR.iterdir()):
        print(f"{path.name}: {path.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
