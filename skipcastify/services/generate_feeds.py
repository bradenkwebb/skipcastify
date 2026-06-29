import feedparser
from feedgen.feed import FeedGenerator
import os
from datetime import datetime, timezone
import logging

from skipcastify.utils.utils import slugify, safe_filename
from skipcastify.services.artwork_processor import process_artwork

logger = logging.getLogger(__name__)


class FeedManager:
    def __init__(self, server_base_url: str, data_dir: str, episode_token: str = "") -> None:
        self.server_base_url = server_base_url.rstrip('/')
        self.episode_token = episode_token
        self.data_dir = data_dir

    def _local_episode_path(self, slug: str, filename: str) -> str | None:
        """Return path to local episode file (processed preferred), or None if not downloaded."""
        for subdir in ('processed', 'raw'):
            path = os.path.join(self.data_dir, 'podcasts', subdir, slug, filename)
            if os.path.exists(path):
                return path
        return None

    def _original_audio_url(self, entry) -> tuple[str | None, str]:
        """Extract original audio URL and length from a feedparser entry."""
        if entry.enclosures:
            enc = entry.enclosures[0]
            return enc.get('href'), enc.get('length') or '0'
        for link in entry.get('links', []):
            if link.get('rel') == 'enclosure':
                return link.get('href'), link.get('length') or '0'
        return None, '0'

    def generate_feed(self, subscription_url: str) -> str:
        feed = feedparser.parse(subscription_url)
        title = feed.feed.title
        slug = slugify(title)

        fg = FeedGenerator()
        fg.load_extension('podcast')
        fg.title(title)
        fg.link(href=subscription_url)
        fg.description(feed.feed.get("description") or feed.feed.get("subtitle") or title)

        image_url = feed.feed.get('image', {}).get('href')
        if image_url:
            artwork_dir = os.path.join(self.data_dir, 'artwork')
            filename = process_artwork(image_url, artwork_dir, slug)
            if filename:
                local_artwork_url = f"{self.server_base_url}/artwork/{self.episode_token}/{filename}"
                fg.image(url=local_artwork_url, title=title, link=subscription_url)
                fg.podcast.itunes_image(local_artwork_url)
            else:
                fg.image(url=image_url, title=title, link=subscription_url)
                fg.podcast.itunes_image(image_url)

        for entry in feed.entries:
            fe = fg.add_entry()
            fe.title(entry.title)
            fe.description(entry.get("summary", ""))

            if entry.get('published_parsed'):
                fe.pubDate(datetime(*entry.published_parsed[:6], tzinfo=timezone.utc))
            else:
                logger.warning(f"Episode '{entry.title}' of {title} has no publish date.")

            if hasattr(entry, "link"):
                fe.link(href=entry.link)

            ep_filename = safe_filename(entry.title, slug)
            local_path = self._local_episode_path(slug, ep_filename)

            if local_path:
                audio_url = f"{self.server_base_url}/episodes/{self.episode_token}/{slug}/{ep_filename}"
                length = str(os.path.getsize(local_path))
            else:
                audio_url, length = self._original_audio_url(entry)

            if audio_url:
                try:
                    fe.enclosure(url=audio_url, type="audio/mpeg", length=length)
                except Exception as e:
                    logger.warning(f"No usable enclosure for '{entry.title}' from {title}: {e}")

        feed_path = os.path.join(self.data_dir, 'feeds', f'{slug}.xml')
        os.makedirs(os.path.dirname(feed_path), exist_ok=True)
        fg.rss_file(feed_path, pretty=True)
        return feed_path
