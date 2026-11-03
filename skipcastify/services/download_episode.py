import json
import os
import feedparser
import requests
from pathlib import Path
import logging

from skipcastify.utils.alerts import Alerter
from skipcastify.utils.utils import safe_filename, slugify, load_subscriptions

logger = logging.getLogger(__name__)

# A backlog bigger than this is reported, because it means something stopped
# the pipeline for a while and the catch-up will run hot for a bit.
BACKLOG_ALERT_THRESHOLD = 10


class EpisodeDownloader:
    def __init__(self, config_path: str, data_dir: str, exclude_host: str = None,
                 episode_limit: int = 5, alerter=None):
        self.data_dir = data_dir
        self.episode_limit = episode_limit
        self.subscription_urls = load_subscriptions(config_path, exclude_host)
        self.alerter = alerter if alerter is not None else Alerter(data_dir)

    def _seen_path(self) -> Path:
        return Path(self.data_dir) / "seen_episodes.json"

    def _load_seen(self) -> dict[str, set[str]]:
        path = self._seen_path()
        if path.exists():
            with open(path) as f:
                raw = json.load(f)
            return {k: set(v) for k, v in raw.items()}
        return {}

    def _save_seen(self, seen: dict[str, set[str]]) -> None:
        with open(self._seen_path(), "w") as f:
            json.dump({k: sorted(v) for k, v in seen.items()}, f, indent=2)

    def _entry_id(self, entry) -> str:
        """Return a stable identifier for a feed entry (guid preferred, audio URL as fallback)."""
        eid = entry.get("id") or entry.get("guid")
        if eid:
            return eid
        try:
            return self.get_audio_url(entry)
        except ValueError:
            raise ValueError(f"Cannot derive stable ID for entry: {entry.get('title', '<untitled>')!r}")
    
    @staticmethod
    def get_audio_url(entry):
        # Try standard RSS enclosure
        if hasattr(entry, "enclosures") and entry.enclosures:
            return entry.enclosures[0].get("href")

        # Fallback: Atom-style link with rel='enclosure'
        for link in entry.get("links", []):
            if link.get("rel") == "enclosure":
                return link.get("href")
        raise ValueError("No audio URL found for the episode")
    
    def download_episode(self, entry, podcast_title: str):
        audio_url = self.get_audio_url(entry)
        if not audio_url:
            logger.warning(f"No audio URL found for episode '{entry.title}'")
            return None

        slug = slugify(podcast_title)
        podcast_dir = Path(self.data_dir) / "podcasts" / "raw" / slug
        podcast_dir.mkdir(parents=True, exist_ok=True)

        filename = safe_filename(entry.title, slug)
        target_path = podcast_dir / filename
        
        processed_path = Path(self.data_dir) / "podcasts" / "processed" / slug / filename
        if target_path.exists() or processed_path.exists():
            logger.info(f"Skipping already-downloaded episode: {filename}")
            return target_path if target_path.exists() else processed_path

        try:
            logger.info(f"Downloading: {entry.title}")
            headers = {"User-Agent": "Mozilla/5.0 (compatible; Skipcastify/1.0)"}
            with requests.get(audio_url, stream=True, timeout=10, headers=headers) as r:
                r.raise_for_status()
                with open(target_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
            logger.info(f"Saved to: {target_path}")
            return target_path
        except Exception as e:
            logger.error(f"Failed to download episode '{entry.title}': {e}")
            return None

    def download_latest(self) -> list[dict]:
        """Returns one {feed, status, ...} per subscription for the run log."""
        seen = self._load_seen()
        results = []

        for subscription_url in self.subscription_urls:
            try:
                feed = feedparser.parse(subscription_url)
                podcast_title = feed.feed.title
            except Exception as e:
                logger.warning(f"Skipping feed {subscription_url}: {e}")
                results.append({"feed": None, "status": "error", "error": type(e).__name__})
                continue
            logger.info(f"Checking podcast: {podcast_title}")
            slug = slugify(podcast_title)

            feed_seen = seen.get(subscription_url)
            if feed_seen is None:
                # First time seeing this feed — record all current episodes as seen, download none.
                feed_seen = set()
                for entry in feed.entries:
                    try:
                        feed_seen.add(self._entry_id(entry))
                    except Exception as e:
                        logger.warning(f"Could not derive ID for entry in '{podcast_title}': {e}")
                seen[subscription_url] = feed_seen
                logger.info(f"New feed '{podcast_title}': marked {len(feed_seen)} existing episodes as seen, will download future episodes only")
                results.append({"feed": slug, "status": "baselined"})
                continue

            unseen = [
                entry for entry in feed.entries
                if self._entry_id(entry) not in feed_seen
            ]

            # Guard against feed-platform migrations (e.g. Art19 → Libsyn) that
            # rewrite every <guid>. When that happens the whole feed suddenly
            # looks new and we would re-download it a few episodes per run.
            #
            # The test is whether ANY current entry is still recognised, not
            # what fraction looks new. A migration rewrites every id, so the
            # seen set stops matching the feed entirely. Downtime leaves the
            # older entries matching perfectly and only recent ones unseen.
            #
            # The previous version keyed on "more than half the feed is
            # unseen", which cannot tell those apart — and on 2026-11-01 it
            # read a four-month outage as a migration, marked 290 Global News
            # episodes seen and downloaded none of them. A whole backlog was
            # dropped on the strength of a log line nobody reads.
            recognised = len(feed.entries) - len(unseen)
            if (feed_seen and recognised == 0
                    and len(feed.entries) > self.episode_limit):
                logger.warning(
                    f"{podcast_title}: none of {len(feed.entries)} entries match the "
                    f"{len(feed_seen)} ids we have seen — treating it as an id change "
                    "and re-baselining. Downloading nothing this run."
                )
                for entry in feed.entries:
                    try:
                        feed_seen.add(self._entry_id(entry))
                    except Exception as e:
                        logger.warning(f"Could not derive ID for entry in '{podcast_title}': {e}")
                seen[subscription_url] = feed_seen
                # Loud, not just logged. This silently discards everything the
                # feed currently offers, so it is the one decision here that
                # must never again pass unnoticed.
                self.alerter.send(
                    f"rebaseline:{slug}", "Podcast feed ids changed",
                    f"{podcast_title}: none of its {len(feed.entries)} entries "
                    f"match what we have seen, so they were all marked seen and "
                    f"none downloaded. If this was not a feed migration, episodes "
                    f"have been skipped.", priority="high")
                results.append({"feed": slug, "status": "rebaselined"})
                continue

            # A real backlog: the seen set still matches the feed, so these are
            # genuinely new episodes. Work through them episode_limit at a time
            # — undownloaded ones stay unseen, so later runs pick up the rest
            # rather than losing them.
            if len(unseen) > BACKLOG_ALERT_THRESHOLD:
                logger.info(f"{podcast_title}: {len(unseen)} episodes behind, "
                            f"taking {self.episode_limit} this run")
                self.alerter.send(
                    f"backlog:{slug}", "Podcast backlog being worked through",
                    f"{podcast_title} is {len(unseen)} episodes behind and is "
                    f"catching up {self.episode_limit} per run. Expect the box "
                    f"to run warm until it clears.")

            new_entries = unseen[:self.episode_limit]

            downloaded = failed = 0
            for entry in new_entries:
                result = self.download_episode(entry, podcast_title)
                if result:
                    feed_seen.add(self._entry_id(entry))
                    downloaded += 1
                else:
                    failed += 1

            seen[subscription_url] = feed_seen
            if not new_entries:
                results.append({"feed": slug, "status": "up_to_date"})
            else:
                results.append({"feed": slug, "status": "downloaded",
                                "downloaded": downloaded, "failed": failed})

        self._save_seen(seen)
        return results

