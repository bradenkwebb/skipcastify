"""Where an episode can be played: the enclosure our published feed gives it.

That is what a podcast app subscribed to the feed would play: the ad-free copy
on R2, or the original audio when nothing was cut, the copy was evicted, or the
episode predates processing. Both URLs carry the path secret, so callers must
treat the result like the /feeds page (auth, no caching).
"""

import os
import xml.etree.ElementTree as ET
from pathlib import Path

from skipcastify.services.published_feeds import R2_FEEDS_SUBDIR, published_feeds
from skipcastify.services.state_manager import uncut_marker
from skipcastify.utils.utils import safe_filename

_items_cache: dict[str, tuple[float, dict[str, str]]] = {}


def _enclosures(path: Path, slug: str) -> dict[str, str]:
    """Episode name -> enclosure URL for one generated feed, cached by mtime."""
    mtime = path.stat().st_mtime
    hit = _items_cache.get(str(path))
    if hit and hit[0] == mtime:
        return hit[1]
    items: dict[str, str] = {}
    channel = ET.parse(path).getroot().find("channel")
    for item in channel.iter("item") if channel is not None else ():
        title, enclosure = item.findtext("title"), item.find("enclosure")
        url = enclosure.get("url") if enclosure is not None else None
        if title and url:
            # Episode files are named from the feed title, the same way the downloader names them.
            items.setdefault(safe_filename(title, slug)[: -len(".mp3")], url)
    _items_cache[str(path)] = (mtime, items)
    return items


def playback(data_dir, public_base_url: str, slug: str | None, name: str) -> dict | None:
    """{url, feed_url, ad_free, uncut} for a published episode, or None."""
    if not slug or not public_base_url:
        return None
    path = Path(data_dir) / R2_FEEDS_SUBDIR / f"{slug}.xml"
    feed = next((f for f in published_feeds(data_dir, public_base_url) if f.slug == slug), None)
    if feed is None or not path.is_file():
        return None
    try:
        url = _enclosures(path, slug).get(name)
    except (OSError, ET.ParseError):
        return None
    if not url:
        return None
    processed = os.path.join(data_dir, "podcasts", "processed", slug, f"{name}.mp3")
    return {
        "url": url,
        "feed_url": feed.url,
        # Our copy lives under <base>/audio/; anything else is the publisher's original.
        "ad_free": url.startswith(public_base_url.rstrip("/") + "/audio/"),
        "uncut": uncut_marker(processed).exists(),
    }
