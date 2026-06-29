"""Download and process podcast artwork to add a branded gradient frame."""

import hashlib
import logging
import os
from io import BytesIO
from typing import Optional

import requests
from PIL import Image, ImageDraw

logger = logging.getLogger(__name__)

_GREEN = (39, 174, 96)
_WHITE = (245, 245, 245)

# Fraction of the artwork's edge band that must sit close to the green frame
# colour before we judge the green frame too low-contrast and fall back to white.
_LOW_CONTRAST_NEAR = 70.0   # RGB distance counted as "close to" the frame colour
_LOW_CONTRAST_FRACTION = 0.35


def _color_distance(c1, c2) -> float:
    return sum((a - b) ** 2 for a, b in zip(c1, c2)) ** 0.5


def _edge_blend_fraction(img: Image.Image, border: int, ref) -> float:
    """Fraction of perimeter-band pixels that are within _LOW_CONTRAST_NEAR of ref.

    A high value means the artwork's edge is the same colour as the frame, so the
    frame would blend in (e.g. a green cover under the green frame).
    """
    rgb = img.convert("RGB")
    w, h = rgb.size
    px = rgb.load()
    step = max(1, min(w, h) // 200)  # subsample for speed on large art
    near = total = 0
    for x in range(0, w, step):
        for y in list(range(0, border, step)) + list(range(h - border, h, step)):
            total += 1
            if _color_distance(px[x, y], ref) <= _LOW_CONTRAST_NEAR:
                near += 1
    for y in range(0, h, step):
        for x in list(range(0, border, step)) + list(range(w - border, w, step)):
            total += 1
            if _color_distance(px[x, y], ref) <= _LOW_CONTRAST_NEAR:
                near += 1
    return near / total if total else 0.0


def _frame_color(img: Image.Image, border: int):
    """Use the green brand frame unless it would blend into the artwork's edge,
    in which case fall back to white (e.g. Lingthusiasm's green cover)."""
    blend = _edge_blend_fraction(img, border, _GREEN)
    if blend >= _LOW_CONTRAST_FRACTION:
        logger.info("Green frame low-contrast (%.0f%% of edge near green) — using white frame", blend * 100)
        return _WHITE
    return _GREEN


def _add_frame(img: Image.Image, border_fraction: float = 0.06) -> Image.Image:
    img = img.convert("RGBA")
    w, h = img.size
    border = max(int(min(w, h) * border_fraction), 12)
    r, g, b = _frame_color(img, border)

    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    for i in range(border):
        # i=0 is outermost (fully opaque), i=border-1 is innermost (transparent)
        t = 1.0 - (i / border)
        alpha = int(255 * t)
        draw.rectangle([i, i, w - 1 - i, h - 1 - i], outline=(r, g, b, alpha), width=1)

    return Image.alpha_composite(img, overlay).convert("RGB")


def process_artwork(source_url: str, output_dir: str, slug: str) -> Optional[str]:
    """Download artwork, apply gradient frame, save with a content-hash filename.

    Returns the filename (e.g. 'philosophize-this-a1b2c3d4.jpg') on success,
    or None on failure. Old versioned files are left in place for safety.
    """
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; Skipcastify/1.0)"}
        resp = requests.get(source_url, timeout=15, headers=headers)
        resp.raise_for_status()
    except Exception as e:
        logger.warning("Failed to download artwork from %s: %s", source_url, e)
        return None

    raw_bytes = resp.content
    content_hash = hashlib.md5(raw_bytes).hexdigest()[:8]
    filename = f"{slug}-{content_hash}.jpg"
    output_path = os.path.join(output_dir, filename)

    if os.path.exists(output_path):
        logger.debug("Artwork unchanged, reusing %s", filename)
        return filename

    try:
        img = Image.open(BytesIO(raw_bytes))
        img = _add_frame(img)
        os.makedirs(output_dir, exist_ok=True)
        img.save(output_path, "JPEG", quality=92)
        logger.info("Saved processed artwork to %s", output_path)
        return filename
    except Exception as e:
        logger.warning("Failed to process artwork: %s", e)
        return None
