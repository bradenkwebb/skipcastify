"""Small cached thumbnails of a show's processed artwork, for page icons."""

import re
from pathlib import Path

from PIL import Image

SIZE = 96  # 2x a 48px icon


def thumbnail(data_dir, slug: str) -> Path | None:
    """The newest `<slug>-<hash>.jpg` in data/artwork, shrunk once and cached."""
    art = Path(data_dir) / "artwork"
    pattern = re.compile(rf"{re.escape(slug)}-[0-9a-f]{{8}}\.jpg")
    sources = [p for p in art.glob(f"{slug}-*.jpg") if pattern.fullmatch(p.name)]
    if not sources:
        return None
    src = max(sources, key=lambda p: p.stat().st_mtime)
    out = art / "thumbs" / f"{src.stem}-{SIZE}.jpg"
    if not out.exists():
        out.parent.mkdir(exist_ok=True)
        with Image.open(src) as im:
            im = im.convert("RGB")
            im.thumbnail((SIZE, SIZE))
            tmp = out.with_suffix(".tmp")
            im.save(tmp, "JPEG", quality=85)
            tmp.replace(out)
    return out
