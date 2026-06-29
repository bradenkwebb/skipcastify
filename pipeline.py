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
        self.storage_high_water = int(os.environ.get("STORAGE_HIGH_WATER_GB", "75")) * 1024 ** 3
        self.storage_low_water = int(os.environ.get("STORAGE_LOW_WATER_GB", "50")) * 1024 ** 3
        self.processing_enabled = os.environ.get("ENABLE_PROCESSING", "false").lower() == "true"
        slugs_env = os.environ.get("PROCESS_SLUGS", "")
        self.process_slugs = {s.strip() for s in slugs_env.split(",") if s.strip()}

        exclude_host = urlparse(server_base_url).hostname
        self.feed_manager = FeedManager(server_base_url, data_dir, episode_token)
        self.downloader = EpisodeDownloader(config_path, data_dir, exclude_host, episode_limit)
        self.processor = AudioProcessor(data_dir)

    def _audio_size(self) -> int:
        """Total bytes used by raw and processed audio files (.mp3 only)."""
        total = 0
        for subdir in ('raw', 'processed'):
            base = Path(self.data_dir) / 'podcasts' / subdir
            if base.exists():
                total += sum(f.stat().st_size for f in base.rglob('*.mp3'))
        return total

    def _cleanup_old_episodes(self):
        """Storage-based retention: delete oldest audio files when usage exceeds
        the high-water mark, stopping once we're back under the low-water mark.

        Deletion order:
          1. Raw files that already have a processed counterpart (always safe to drop)
          2. Oldest raw files (by mtime) under storage pressure
          3. Oldest processed files only as a last resort

        Only .mp3 files are ever deleted — transcripts and other metadata are kept.
        """
        raw_base = Path(self.data_dir) / 'podcasts' / 'raw'
        processed_base = Path(self.data_dir) / 'podcasts' / 'processed'

        # Step 1: always drop raw files that have a processed counterpart.
        if raw_base.exists():
            for raw_file in raw_base.rglob('*.mp3'):
                counterpart = processed_base / raw_file.parent.name / raw_file.name
                if counterpart.exists():
                    raw_file.unlink()
                    logger.info(f"Deleted raw (processed exists): {raw_file.name}")

        total = self._audio_size()
        if total <= self.storage_high_water:
            logger.debug(f"Audio storage at {total / 1024**3:.1f}GB, under high-water mark")
            return

        logger.info(f"Audio storage at {total / 1024**3:.1f}GB, trimming to {self.storage_low_water / 1024**3:.0f}GB")

        # Step 2: delete oldest raw files until under low-water mark.
        if raw_base.exists():
            for f in sorted(raw_base.rglob('*.mp3'), key=lambda f: f.stat().st_mtime):
                if total <= self.storage_low_water:
                    break
                size = f.stat().st_size
                f.unlink()
                total -= size
                logger.info(f"Deleted raw {f.name} ({size / 1024**2:.1f}MB)")

        # Step 3: delete oldest processed files only if still over the low-water mark.
        if total > self.storage_low_water and processed_base.exists():
            for f in sorted(processed_base.rglob('*.mp3'), key=lambda f: f.stat().st_mtime):
                if total <= self.storage_low_water:
                    break
                size = f.stat().st_size
                f.unlink()
                total -= size
                logger.info(f"Deleted processed {f.name} ({size / 1024**2:.1f}MB)")

    def run(self):
        logger.info("Starting Skipcastify pipeline...")
        try:
            logger.info("Downloading new episodes")
            self.downloader.download_latest()

            if self.processing_enabled:
                state_manager = StateManager(self.data_dir)
                logger.info("Processing episodes")
                for episode_fpath in state_manager.get_unprocessed_episodes():
                    slug = Path(episode_fpath).parent.name
                    if self.process_slugs and slug not in self.process_slugs:
                        logger.debug(f"Skipping {slug} (not in PROCESS_SLUGS)")
                        continue
                    self.processor.process(episode_fpath, state_manager)
            else:
                logger.info("Audio processing disabled (ENABLE_PROCESSING=false)")

            self._cleanup_old_episodes()

            logger.info("Generating RSS feeds")
            for url in self.downloader.subscription_urls:
                try:
                    self.feed_manager.generate_feed(url)
                except Exception as e:
                    logger.warning(f"Skipping feed generation for {url}: {e}")
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
