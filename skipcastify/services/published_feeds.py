"""What is actually published to R2, for the Feeds page.

The question this answers is narrower than it looks: not "which feeds does the
pipeline know about" but **"which feed URLs would work right now if Braden
pasted one into Overcast"**. Those differ. A local ``feeds_r2/<slug>.xml``
exists the moment a feed is generated, which is before it is uploaded and
remains true if the upload failed; subscribing to a URL whose object is not
there yet is a 404 and, worse, teaches a podcast app that the feed is broken.

So the publish manifest is the source of truth here, exactly as it is for
retention and the cap. One row per *uploaded* feed object, nothing else.

The URL is built from the manifest key rather than re-derived from
PUBLIC_PATH_SECRET, so a key written under a previous secret still yields the
URL that actually works.
"""

import logging
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import quote

from skipcastify.services.publish_manifest import PublishManifest

logger = logging.getLogger(__name__)

R2_FEEDS_SUBDIR = "feeds_r2"


@dataclass
class PublishedFeed:
    slug: str
    title: str
    url: str
    episodes: int
    audio_bytes: int
    last_audio_upload: float | None
    feed_uploaded: float | None

    @property
    def overcast_url(self) -> str:
        return overcast_add_url(self.url)


def overcast_add_url(feed_url: str) -> str:
    """Overcast's documented subscribe scheme.

    From https://overcast.fm/podcasterinfo: ``overcast://x-callback-url/add``
    with a required, URL-encoded ``url`` parameter opens a Subscribe prompt for
    any RSS feed. Encoded with ``safe=""`` on purpose — an unencoded feed URL
    that carries its own query string gets truncated.

    iOS only. On a desktop browser the link simply does nothing, which is why
    the Copy button is the page's primary control and this is the shortcut.
    """
    return f"overcast://x-callback-url/add?url={quote(feed_url, safe='')}"


def _channel_title(path: str) -> str | None:
    """The <channel><title> of a generated feed, without parsing all of it.

    Global News carries ~290 items; the channel title is the first </title> to
    close, so iterparse can stop there instead of building a tree per request.
    """
    try:
        for _event, elem in ET.iterparse(path, events=("end",)):
            if elem.tag == "title":
                return (elem.text or "").strip() or None
    except (OSError, ET.ParseError) as e:
        logger.debug("Could not read a title from %s: %s", os.path.basename(path), e)
    return None


def published_feeds(data_dir: str, public_base_url: str,
                    manifest: PublishManifest | None = None,
                    feeds_subdir: str = R2_FEEDS_SUBDIR) -> list[PublishedFeed]:
    """Every feed currently live in R2, titled and counted. Sorted by title."""
    manifest = manifest if manifest is not None else PublishManifest(data_dir)
    base = public_base_url.rstrip('/')

    # audio/<secret>/<slug>/<file>.mp3 -> group the episodes by slug.
    counts: dict[str, int] = {}
    sizes: dict[str, int] = {}
    latest: dict[str, float] = {}
    for key in manifest.keys_of_kind("audio"):
        parts = key.split('/')
        if len(parts) < 4:
            continue
        slug = parts[2]
        entry = manifest.get(key) or {}
        counts[slug] = counts.get(slug, 0) + 1
        sizes[slug] = sizes.get(slug, 0) + int(entry.get("size") or 0)
        uploaded = entry.get("uploaded_at")
        if uploaded and uploaded > latest.get(slug, 0):
            latest[slug] = float(uploaded)

    rows: list[PublishedFeed] = []
    for key in manifest.keys_of_kind("feed"):
        slug = key.split('/')[-1]
        if slug.endswith('.xml'):
            slug = slug[:-len('.xml')]
        entry = manifest.get(key) or {}
        local = os.path.join(data_dir, feeds_subdir, f"{slug}.xml")
        rows.append(PublishedFeed(
            slug=slug,
            # The slug is a poor title but it is never wrong, which beats a
            # blank row when the local XML has been cleaned up or never existed.
            title=_channel_title(local) or slug,
            url=f"{base}/{key}",
            episodes=counts.get(slug, 0),
            audio_bytes=sizes.get(slug, 0),
            last_audio_upload=latest.get(slug),
            feed_uploaded=entry.get("uploaded_at"),
        ))
    rows.sort(key=lambda r: r.title.casefold())
    return rows
