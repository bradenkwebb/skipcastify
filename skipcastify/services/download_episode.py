import os
import yaml
import feedparser
import requests
from dotenv import load_dotenv
from pathlib import Path
import logging

from utils.utils import safe_filename, slugify

logger = logging.getLogger(__name__)

# Re-write in OOP style
class EpisodeDownloader:
    def __init__(self, config_path: str, data_dir: str):
        self.data_dir = data_dir
        self.config_path = config_path
        with open(config_path) as f:
            self.subscriptions = yaml.safe_load(f)
    
    @staticmethod
    def get_audio_url(entry: dict):
        # Try standard RSS enclosure
        if hasattr(entry, "enclosures") and entry.enclosures:
            return entry.enclosures[0].get("href")

        # Fallback: Atom-style link with rel='enclosure'
        for link in entry.get("links", []):
            if link.get("rel") == "enclosure":
                return link.get("href")
        raise ValueError("No audio URL found for the episode")
    
    def download_episode(self, entry: dict, podcast_title: str):
        audio_url = self.get_audio_url(entry)
        if not audio_url:
            logger.warning(f"No audio URL found for episode '{entry.title}'")
            return None

        slug = slugify(podcast_title)
        podcast_dir = Path(self.data_dir) / "podcasts" / slug
        podcast_dir.mkdir(parents=True, exist_ok=True)

        filename = safe_filename(entry.title, slug)
        target_path = podcast_dir / filename

        if target_path.exists():
            logger.info(f"Skipping already-downloaded episode: {filename}")
            return target_path

        try:
            logger.info(f"Downloading: {entry.title}")
            with requests.get(audio_url, stream=True, timeout=10) as r:
                r.raise_for_status()
                with open(target_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
            logger.info(f"Saved to: {target_path}")
            return target_path
        except Exception as e:
            logger.error(f"Failed to download episode '{entry.title}': {e}")
            return None

    def download_latest(self):
        for subscription_url in self.subscriptions["subscriptions"]:
            feed = feedparser.parse(subscription_url)
            podcast_title = feed.feed.title
            logger.info(f"Checking podcast: {podcast_title}")

            for entry in feed.entries[:10]:
                self.download_episode(entry, podcast_title)

