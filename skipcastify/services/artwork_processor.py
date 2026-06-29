"""Download and process podcast artwork to add a green gradient frame."""

import hashlib
import logging
import os
from io import BytesIO
from typing import Optional

import requests
from PIL import Image, ImageDraw

logger = logging.getLogger(__name__)

_GREEN = (39, 174, 96)


def _add_frame(img: Image.Image, border_fraction: float = 0.06) -> Image.Image:
    img = img.convert("RGBA")
    w, h = img.size
    border = max(int(min(w, h) * border_fraction), 12)
    r, g, b = _GREEN

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
