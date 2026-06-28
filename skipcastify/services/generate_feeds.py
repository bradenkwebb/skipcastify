import feedparser
import yaml
from feedgen.feed import FeedGenerator
import os
import re
from dotenv import load_dotenv
import logging

from skipcastify.utils.utils import slugify, safe_filename

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
            fg.image(url=image_url, title=title, link=subscription_url)
            fg.podcast.itunes_image(image_url)

        for entry in feed.entries[:10]:  # take top 10 episodes
            fe = fg.add_entry()
            fe.title(entry.title)
            fe.description(entry.get("summary", ""))
            ep_filename = safe_filename(entry.title, slug)

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
            except AttributeError as e:
                logger.warning(f"Error with {entry.title} from podcast {title}")
                logger.warning(e)
        
        feed_path = f"{self.data_dir}/feeds/{slug}.xml"
        os.makedirs(os.path.dirname(feed_path), exist_ok=True)
        fg.rss_file(feed_path, pretty=True)
        return feed_path