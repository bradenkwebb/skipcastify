import logging
import socket
from dotenv import load_dotenv
from urllib.parse import urlparse
from skipcastify.services.audio_processor import AudioProcessor
from skipcastify.services.download_episode import EpisodeDownloader
from skipcastify.services.generate_feeds import FeedManager
import sys
import os

from skipcastify.services.state_manager import StateManager
from skipcastify.utils.logger import setup_logging

logger = logging.getLogger(__name__)


class Pipeline:
    def __init__(self, config_path: str, data_dir: str) -> None:
        self.data_dir = data_dir

        server_base_url = os.environ.get("SERVER_BASE_URL") or f"http://{socket.getfqdn()}:5000"
        episode_token = os.environ.get("EPISODE_TOKEN", "")
        exclude_host = urlparse(server_base_url).hostname

        self.feed_manager = FeedManager(server_base_url, self.data_dir, episode_token)
        self.downloader = EpisodeDownloader(config_path, self.data_dir, exclude_host)
        self.processor = AudioProcessor(self.data_dir)

    def run(self):
        logger.info("Starting Skipcastify pipeline...")
        try:
            logger.info("Starting episode downloads")
            self.downloader.download_latest()

            # TODO: Process audio (placeholder for now)
            state_manager = StateManager(self.data_dir)
            logger.info("Processing episodes:")
            for episode_fpath in state_manager.get_unprocessed_episodes():
                self.processor.process(episode_fpath, state_manager)

            logger.info("Generating RSS feeds")
            for url in self.downloader.subscription_urls:
                self.feed_manager.generate_feed(url)
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
