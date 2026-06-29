import feedparser
import yaml
from feedgen.feed import FeedGenerator
import os
import re
from datetime import datetime, timezone
from dotenv import load_dotenv
import logging

from skipcastify.utils.utils import slugify, safe_filename
from skipcastify.services.artwork_processor import process_artwork

logger = logging.getLogger(__name__)

class FeedManager:
    def __init__(self, server_base_url: str, data_dir: str, episode_token: str = "") -> None:
        self.server_base_url = server_base_url.rstrip('/')
        self.episode_token = episode_token
        self.data_dir = data_dir
    
    def generate_feed(self, subscription_url: str) -> str:
        feed = feedparser.parse(subscription_url)
        title = feed.feed.title
        slug = slugify(title)

        fg = FeedGenerator()
        fg.load_extension('podcast')
        fg.title(title)
        fg.link(href=subscription_url)
        fg.description(feed.feed.get("description", ""))

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

        for entry in feed.entries[:10]:  # take top 10 episodes
            fe = fg.add_entry()
            fe.title(entry.title)
            fe.description(entry.get("summary", ""))
            ep_filename = safe_filename(entry.title, slug)

            if entry.get('published_parsed'):
                fe.pubDate(datetime(*entry.published_parsed[:6], tzinfo=timezone.utc))
            else:
                logger.warning(f"Episode '{entry.title}' of {title} has no publish date.")

            if hasattr(entry, "link"):
                fe.link(href=entry.link)
            else:
                logger.warning(f"Episode '{entry.title}' of {title} has no webview link.")
            try:
                fe.enclosure(
                    url=f"{self.server_base_url}/episodes/{self.episode_token}/{slug}/{ep_filename}",
                    type="audio/mpeg",
                    length=entry.enclosures[0]["length"],
                )
            except Exception as e:
                logger.warning(f"No usable enclosure for '{entry.title}' from {title}: {e}")
        
        feed_path = f"{self.data_dir}/feeds/{slug}.xml"
        os.makedirs(os.path.dirname(feed_path), exist_ok=True)
        fg.rss_file(feed_path, pretty=True)
        return feed_path