import fcntl
import logging
import socket
import sys
import os
import time
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

        Deletion order (only when over the high-water mark):
          1. Raw files that already have a processed counterpart (safe to drop first)
          2. Oldest remaining raw files by mtime
          3. Oldest processed files only as a last resort

        Only .mp3 files are ever deleted — transcripts and other metadata are kept.
        """
        raw_base = Path(self.data_dir) / 'podcasts' / 'raw'
        processed_base = Path(self.data_dir) / 'podcasts' / 'processed'

        total = self._audio_size()
        if total <= self.storage_high_water:
            logger.debug(f"Audio storage at {total / 1024**3:.1f}GB, under high-water mark")
            return

        logger.info(f"Audio storage at {total / 1024**3:.1f}GB, trimming to {self.storage_low_water / 1024**3:.0f}GB")

        # Step 1: drop raw files that have a processed counterpart (cheapest to remove).
        if raw_base.exists():
            for raw_file in sorted(raw_base.rglob('*.mp3'), key=lambda f: f.stat().st_mtime):
                if total <= self.storage_low_water:
                    break
                counterpart = processed_base / raw_file.parent.name / raw_file.name
                if counterpart.exists():
                    size = raw_file.stat().st_size
                    raw_file.unlink()
                    total -= size
                    logger.info(f"Deleted raw (processed exists): {raw_file.name} ({size / 1024**2:.1f}MB)")

        # Step 2: delete oldest remaining raw files.
        if total > self.storage_low_water and raw_base.exists():
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
                cooldown_s = int(os.environ.get("EPISODE_COOLDOWN_S", "120"))
                logger.info("Processing episodes")
                first = True
                for episode_fpath in state_manager.get_unprocessed_episodes():
                    slug = Path(episode_fpath).parent.name
                    if self.process_slugs and slug not in self.process_slugs:
                        logger.debug(f"Skipping {slug} (not in PROCESS_SLUGS)")
                        continue
                    if not first and cooldown_s > 0:
                        logger.info(f"Cooling down {cooldown_s}s before next episode")
                        time.sleep(cooldown_s)
                    first = False
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

        lock_path = Path(data_dir) / "pipeline.lock"
        lock_file = open(lock_path, "w")
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            logging.info("Another pipeline instance is already running — exiting.")
            return 0

        try:
            pipeline = Pipeline(os.environ["SUBSCRIPTIONS"], data_dir)
            pipeline.run()
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)
            lock_file.close()

        return 0
    except Exception as e:
        logging.error(f"Pipeline execution failed: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
