import feedparser
import yaml
from feedgen.feed import FeedGenerator
import os
import re
from dotenv import load_dotenv
import logging

logger = logging.getLogger(__name__)

load_dotenv()
server_ip = os.environ.get("SERVER_IP")  # Replace with your server IP
data_dir = os.environ.get("DATA_DIR")


def slugify(title):
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


with open(os.environ.get("SUBSCRIPTIONS")) as f:
    config = yaml.safe_load(f)

for subscription_url in config["subscriptions"]:
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

        try:
            if hasattr(entry, "link"):
                fe.link(href=entry.link)
            else:
                fe.link(href=getattr(entry.links[0], "href"))
        except AttributeError as e:
            logger.warning(f"Error with {entry.title} from podcast {title}")
            logger.warning(e)
        try:
            fe.enclosure(
                url=f"http://{server_ip}/episodes/{slug}/{ep_filename}",
                type="audio/mpeg",
                length=entry.enclosures[0]["length"],
            )
        except AttributeError as e:
            logger.warning(f"Error with {entry.title} from podcast {title}")
            logger.warning(e)

    os.makedirs(f"{data_dir}/feeds", exist_ok=True)
    fg.rss_file(f"{data_dir}/feeds/{slug}.xml", pretty=True)
