import logging
import os
import socket
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

from skipcastify.services.audio_processor import AudioProcessor
from skipcastify.services.download_episode import EpisodeDownloader
from skipcastify.services.generate_feeds import FeedManager
from skipcastify.services.labeling import label_path
from skipcastify.services.run_log import (
    RunRecord,
    append_run,
    clear_inflight,
    take_inflight,
    write_inflight,
)
from skipcastify.services.state_manager import StateManager
from skipcastify.utils.alerts import Alerter
from skipcastify.utils.lock import LockHeld, pipeline_lock
from skipcastify.utils.logger import setup_logging

logger = logging.getLogger(__name__)

# Pipeline.stage names the step currently running, and goes into the failure
# alert: knowing a run died in "process" rather than "publish" is most of the
# diagnosis from a phone. The others are set inline in run(); this one is the
# value before run() is even reached.
STAGE_STARTUP = "startup"
# An episode that started this many runs without finishing is skipped, so one
# bad file (e.g. an OOM kill) cannot block every later episode and publish.
MAX_ATTEMPTS = 2


class Pipeline:
    def __init__(self, config_path: str, data_dir: str, alerter=None, run=None) -> None:
        self.data_dir = data_dir
        self.run_record = run if run is not None else RunRecord()
        self.alerter = alerter if alerter is not None else Alerter(data_dir)
        # The stage currently executing, and whether run() has already alerted
        # about a failure. main() reads both so one crash is one push.
        self.episode = None
        self.stage = STAGE_STARTUP
        self.alerted = False

        server_base_url = os.environ.get("SERVER_BASE_URL") or f"http://{socket.getfqdn()}:5000"
        episode_token = os.environ.get("EPISODE_TOKEN", "")
        episode_limit = int(os.environ.get("EPISODE_LIMIT", "5"))
        self.storage_high_water = int(os.environ.get("STORAGE_HIGH_WATER_GB", "75")) * 1024 ** 3
        self.storage_low_water = int(os.environ.get("STORAGE_LOW_WATER_GB", "50")) * 1024 ** 3
        self.processing_enabled = os.environ.get("ENABLE_PROCESSING", "false").lower() == "true"
        self.publish_to_r2 = os.environ.get("PUBLISH_TO_R2", "false").lower() == "true"
        slugs_env = os.environ.get("PROCESS_SLUGS", "")
        self.process_slugs = {s.strip() for s in slugs_env.split(",") if s.strip()}

        exclude_host = urlparse(server_base_url).hostname
        self.feed_manager = FeedManager(server_base_url, data_dir, episode_token)
        self.downloader = EpisodeDownloader(config_path, data_dir, exclude_host,
                                           episode_limit, alerter=self.alerter)
        self.processor = AudioProcessor(data_dir)

    @property
    def stage(self) -> str:
        return self._stage

    @stage.setter
    def stage(self, value: str) -> None:
        self._stage, self.episode = value, None
        self._checkpoint()

    def _checkpoint(self) -> None:
        try:
            write_inflight(self.data_dir, self.run_record, self._stage, self.episode)
        except OSError as e:
            logger.warning(f"Could not write run marker: {e}")

    def _audio_size(self) -> int:
        """Total bytes used by raw and processed audio files (.mp3 only)."""
        total = 0
        for subdir in ('raw', 'processed'):
            base = Path(self.data_dir) / 'podcasts' / subdir
            if base.exists():
                total += sum(f.stat().st_size for f in base.rglob('*.mp3'))
        return total

    def _labeled(self, raw_file: Path) -> bool:
        return label_path(self.data_dir, raw_file.stem).exists()

    def _cleanup_old_episodes(self):
        """Storage-based retention: delete oldest audio files when usage exceeds
        the high-water mark, stopping once we're back under the low-water mark.

        Deletion order (only when over the high-water mark):
          1. Raw files that already have a processed counterpart (safe to drop first)
          2. Oldest remaining raw files by mtime
          3. Oldest processed files only as a last resort

        Only .mp3 files are ever deleted — transcripts and other metadata are kept.
        Raw audio of hand-labeled episodes is never deleted: labels are timed to it.
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
                if self._labeled(raw_file):
                    continue
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
                if self._labeled(f):
                    continue
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

    def _publish_to_r2(self):
        """Push ad-free audio, artwork and feeds to Cloudflare R2.

        Overcast's crawlers can't reach the tailnet and we want no inbound
        exposure of this box, so the feeds podcast apps subscribe to are
        static objects we push out instead. See services/publisher.py.

        Imported lazily so boto3 is only loaded when publishing is actually
        switched on.
        """
        from skipcastify.services.publisher import R2Publisher

        try:
            # Share this run's alerter, so the publisher's throttle windows
            # live in the same file as the pipeline's rather than in a second
            # one that knows nothing about them.
            publisher = R2Publisher.from_env(self.data_dir, alerter=self.alerter)
        except ValueError as e:
            logger.error(f"R2 publishing is enabled but not configured: {e}")
            return
        published = publisher.publish_all(self.downloader.subscription_urls)
        self.run_record.published = len(published)
        logger.info(f"Published {len(published)} feed(s) to R2")

    def report_failure(self, exc: BaseException) -> None:
        """Push an alert about an unhandled failure, deduped by type+stage.

        Until now a crashing pipeline was only noticeable through monitor.sh's
        feed-staleness check, which fires hours later and says nothing about
        what broke. The alerter scrubs the text, which matters here because an
        exception message routinely contains the URL that failed.
        """
        self.alerted = True
        self.alerter.mark_failing(f"{type(exc).__name__}:{self.stage}", str(exc))
        self.alerter.send(
            f"{type(exc).__name__}:{self.stage}",
            f"Pipeline failed: {self.stage}",
            f"{type(exc).__name__}: {exc}",
            priority="high")

    def run(self):
        logger.info("Starting Skipcastify pipeline...")
        try:
            self.stage = "download"
            logger.info("Downloading new episodes")
            self.run_record.feeds = self.downloader.download_latest() or []

            self.stage = "process"
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
                    if state_manager.attempts(episode_fpath) >= MAX_ATTEMPTS:
                        logger.warning(f"Skipping {Path(episode_fpath).name}: failed {MAX_ATTEMPTS} runs")
                        continue
                    self.episode = f"{slug}/{Path(episode_fpath).name}"
                    self._checkpoint()
                    if state_manager.start_attempt(episode_fpath) == MAX_ATTEMPTS:
                        # Last try: alert now, since a SIGKILL leaves no chance to later.
                        self.alerter.send(f"episode-stuck:{Path(episode_fpath).name}",
                                          "Episode keeps failing",
                                          f"{slug}/{Path(episode_fpath).name} is on attempt "
                                          f"{MAX_ATTEMPTS}; it will be skipped if this run fails too.")
                    self.processor.process(episode_fpath, state_manager)
                    state_manager.finish_attempt(episode_fpath)
                    self.run_record.processed.append(
                        {"feed": slug, "episode": Path(episode_fpath).stem})
            else:
                logger.info("Audio processing disabled (ENABLE_PROCESSING=false)")

            self.stage = "cleanup"
            self._cleanup_old_episodes()

            self.stage = "feeds"
            logger.info("Generating RSS feeds")
            for url in self.downloader.subscription_urls:
                try:
                    self.feed_manager.generate_feed(url)
                except Exception as e:
                    logger.warning(f"Skipping feed generation for {url}: {e}")

            self.stage = "publish"
            if self.publish_to_r2:
                logger.info("Publishing to R2")
                self._publish_to_r2()
            else:
                logger.info("R2 publishing disabled (PUBLISH_TO_R2=false)")
        except Exception as e:
            logger.error(f"Pipeline failed during {self.stage}: {e}", exc_info=True)
            self.report_failure(e)
            raise

        # A clean run from end to end. If earlier runs were failing, say so
        # once -- otherwise the only signal that a problem went away is the
        # absence of further alerts, which is indistinguishable from ntfy
        # being broken.
        self.alerter.clear_failing()


def main():
    alerter = None
    pipeline = None
    data_dir = None
    run = RunRecord()
    outcome = "failed"
    try:
        load_dotenv()
        data_dir = os.getenv("DATA_DIR")
        if not data_dir:
            raise EnvironmentError("DATA_DIR is not defined")
        setup_logging(data_dir)
        alerter = Alerter(data_dir)

        try:
            with pipeline_lock(data_dir):
                report_killed_run(data_dir, alerter)
                try:
                    pipeline = Pipeline(os.environ["SUBSCRIPTIONS"], data_dir, alerter=alerter, run=run)
                    pipeline.run()
                finally:
                    clear_inflight(data_dir)
            outcome = "ok"
        except LockHeld:
            # Nothing ran, so this must not clear a failing flag.
            logging.info("Another pipeline instance is already running — exiting.")
            outcome = "skipped"
        _record_run(data_dir, run, outcome)
        return 0
    except Exception as e:
        stage = pipeline.stage if pipeline is not None else STAGE_STARTUP
        _record_run(data_dir, run, outcome, stage, type(e).__name__)
        logging.error(f"Pipeline execution failed: {e}", exc_info=True)
        # Alert only for failures run() did not already report -- a bad
        # DATA_DIR, a missing SUBSCRIPTIONS, a construction error. Without the
        # flag one crash would push twice under two different fingerprints.
        if alerter is not None and not (pipeline is not None and pipeline.alerted):
            alerter.mark_failing(f"{type(e).__name__}:{stage}", str(e))
            alerter.send(f"{type(e).__name__}:{stage}",
                         f"Pipeline failed: {stage}",
                         f"{type(e).__name__}: {e}", priority="high")
        return 1


def report_killed_run(data_dir, alerter) -> None:
    """A marker left by the previous run means it died without running any
    except/finally (SIGKILL, usually the OOM killer), so it never alerted."""
    dead = take_inflight(data_dir)
    if dead is None:
        return
    stage = dead.get("stage") or "unknown"
    where = f"{stage}, on {dead['episode']}" if dead.get("episode") else stage
    try:
        append_run(data_dir, {"started": dead.get("started"), "duration_s": dead.get("duration_s"),
                              "outcome": "killed", "feeds": [], "processed": [], "published": None,
                              "stage": stage, "error": "killed", "episode": dead.get("episode")})
    except Exception as e:
        logging.warning(f"Could not record killed run: {e}")
    detail = (f"The run started {dead.get('started')} died during {where} without logging an "
              "error. Usually the kernel OOM killer; check journalctl -k.")
    alerter.mark_failing(f"killed:{stage}", detail)
    alerter.send(f"killed:{stage}", "Pipeline run was killed", detail, priority="high")


def _record_run(data_dir, run, outcome, stage=None, error=None) -> None:
    # Exception type only: messages can carry feed URLs.
    if not data_dir:
        return
    try:
        append_run(data_dir, run.to_dict(outcome, stage, error))
    except Exception as e:
        logging.warning(f"Could not record pipeline run: {e}")


if __name__ == "__main__":
    sys.exit(main())
