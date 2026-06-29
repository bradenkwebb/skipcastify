import logging
import socket
import sys
import os
from pathlib import Path
from urllib.parse import urlparse
from dotenv import load_dotenv

from skipcastify.services.audio_processor import AudioProcessor
from skipcastify.services.download_episode import EpisodeDownloader
from skipcastify.services.generate_feeds import FeedManager
from skipcastify.services.state_manager import StateManager
from skipcastify.utils.logger import setup_logging

logger = logging.getLogger(__name__)


class Pipeline:
    def __init__(self, config_path: str, data_dir: str) -> None:
        self.data_dir = data_dir

        server_base_url = os.environ.get("SERVER_BASE_URL") or f"http://{socket.getfqdn()}:5000"
        episode_token = os.environ.get("EPISODE_TOKEN", "")
        episode_limit = int(os.environ.get("EPISODE_LIMIT", "5"))
        self.retention_count = int(os.environ.get("EPISODE_RETENTION", "20"))

        exclude_host = urlparse(server_base_url).hostname
        self.feed_manager = FeedManager(server_base_url, data_dir, episode_token)
        self.downloader = EpisodeDownloader(config_path, data_dir, exclude_host, episode_limit)
        self.processor = AudioProcessor(data_dir)

    def _cleanup_old_episodes(self):
        """Delete raw files that have a processed counterpart, and trim oldest raw
        files beyond self.retention_count per podcast. Currently a no-op in
        pass-through mode since no processed files exist yet."""
        raw_base = Path(self.data_dir) / 'podcasts' / 'raw'
        if not raw_base.exists():
            return
        for podcast_dir in raw_base.iterdir():
            if not podcast_dir.is_dir():
                continue
            files = sorted(podcast_dir.glob('*.mp3'), key=lambda f: f.stat().st_mtime, reverse=True)
            for i, raw_file in enumerate(files):
                processed = Path(self.data_dir) / 'podcasts' / 'processed' / podcast_dir.name / raw_file.name
                if processed.exists():
                    raw_file.unlink()
                    logger.info(f"Deleted raw (processed exists): {raw_file.name}")
                elif i >= self.retention_count:
                    raw_file.unlink()
                    logger.info(f"Deleted raw (beyond retention limit): {raw_file.name}")

    def run(self):
        logger.info("Starting Skipcastify pipeline...")
        try:
            logger.info("Downloading new episodes")
            self.downloader.download_latest()

            state_manager = StateManager(self.data_dir)
            logger.info("Processing episodes")
            for episode_fpath in state_manager.get_unprocessed_episodes():
                self.processor.process(episode_fpath, state_manager)

            logger.info("Generating RSS feeds")
            for url in self.downloader.subscription_urls:
                self.feed_manager.generate_feed(url)

            self._cleanup_old_episodes()
        except Exception as e:
            logger.error(f"Pipeline failed: {e}", exc_info=True)
            raise


def main():
    try:
        load_dotenv()
        data_dir = os.getenv("DATA_DIR")
        if not data_dir:
            raise EnvironmentError("DATA_DIR is not defined")
        setup_logging(data_dir)
        pipeline = Pipeline(os.environ["SUBSCRIPTIONS"], data_dir)
        pipeline.run()
        return 0
    except Exception as e:
        logging.error(f"Pipeline execution failed: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
