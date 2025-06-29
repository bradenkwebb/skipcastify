import feedparser
import yaml
from feedgen.feed import FeedGenerator
import os
import re
from dotenv import load_dotenv
import logging

from utils.utils import slugify

logger = logging.getLogger(__name__)

class FeedManager:
    def __init__(self, server_ip: str, data_dir: str) -> None:
        self.server_ip = server_ip
        self.data_dir = data_dir
    
    def generate_feed(self, subscription_url: str) -> str:
        feed = feedparser.parse(subscription_url)
        title = feed.feed.title
        slug = slugify(title)

        fg = FeedGenerator()
        fg.title(title)
        fg.link(href=subscription_url)
        fg.description(feed.feed.get("description", ""))

        for entry in feed.entries[:10]:  # take top 10 episodes
            fe = fg.add_entry()
            fe.title(entry.title)
            fe.description(entry.get("summary", ""))
            ep_filename = slug + "-" + entry.title.replace(" ", "_") + ".mp3"

            if hasattr(entry, "link"):
                fe.link(href=entry.link)
            else:
                logger.warning(f"Episode '{entry.title}' of {title} has no webview link.")
            try:
                fe.enclosure(
                    url=f"http://{self.server_ip}/podcasts/{slug}/{ep_filename}",
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