import json
import feedparser
import requests
from pathlib import Path
import calendar
import logging
import time

from skipcastify.utils.alerts import Alerter
from skipcastify.utils.utils import safe_filename, slugify, load_subscriptions

logger = logging.getLogger(__name__)

# A backlog bigger than this is reported: the catch-up will run hot for a bit.
BACKLOG_ALERT_THRESHOLD = 10

# After downtime, catch up on this much of the past only. Anything unseen and
# older is marked seen without downloading.
CATCH_UP_WINDOW_DAYS = 7


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

    def _feed_cache_path(self) -> Path:
        return Path(self.data_dir) / "feed_cache.json"

    def _load_feed_cache(self) -> dict[str, dict]:
        """Per-feed HTTP validators from the last successful fetch."""
        path = self._feed_cache_path()
        if path.exists():
            try:
                with open(path) as f:
                    return json.load(f)
            except (OSError, ValueError) as e:
                # Losing it only costs one unconditional fetch per feed.
                logger.warning(f"Ignoring unreadable feed cache: {e}")
        return {}

    def _save_feed_cache(self, cache: dict[str, dict]) -> None:
        try:
            with open(self._feed_cache_path(), "w") as f:
                json.dump(cache, f, indent=2)
        except OSError as e:
            logger.warning(f"Could not save feed cache: {e}")

    def _remember_feed(self, cache: dict, url: str, slug: str, feed,
                       fully_handled: bool) -> None:
        """Record the slug always; the HTTP validators only when nothing is
        left to retry. A 304 skips the feed, so a backlog or a failed
        download would otherwise never be picked up again."""
        entry = {"slug": slug}
        if fully_handled:
            for key in ("etag", "modified"):
                value = getattr(feed, key, None)
                if value:
                    entry[key] = value
        cache[url] = entry

    def _safe_entry_id(self, entry) -> str | None:
        """_entry_id, or None for an entry we cannot identify at all."""
        try:
            return self._entry_id(entry)
        except Exception:
            return None

    def _entry_published(self, entry) -> float | None:
        """Publish time as a UTC timestamp, or None if the feed gives none."""
        parsed = entry.get("published_parsed") or entry.get("updated_parsed")
        if not parsed:
            return None
        try:
            return calendar.timegm(parsed)
        except Exception:
            return None

    def _split_on_catch_up_window(self, entries, now: float) -> tuple[list, list]:
        """Split unseen entries into (fresh, stale), fresh newest-first.

        An entry with no parseable date counts as stale, so a feed without
        dates cannot trigger an unbounded catch-up.
        """
        cutoff = now - CATCH_UP_WINDOW_DAYS * 86400
        fresh, stale = [], []
        for entry in entries:
            published = self._entry_published(entry)
            if published is not None and published >= cutoff:
                fresh.append((published, entry))
            else:
                stale.append(entry)
        fresh.sort(key=lambda pair: pair[0], reverse=True)
        return [entry for _, entry in fresh], stale

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

    def download_latest(self, now: float | None = None) -> list[dict]:
        """Returns one {feed, status, ...} per subscription for the run log."""
        now = now if now is not None else time.time()
        seen = self._load_seen()
        cache = self._load_feed_cache()
        results = []

        for subscription_url in self.subscription_urls:
            cached = cache.get(subscription_url) or {}
            try:
                feed = feedparser.parse(subscription_url,
                                        etag=cached.get("etag"),
                                        modified=cached.get("modified"))
                if getattr(feed, "status", None) == 304:
                    # Not modified: no body was sent, so feed.entries is empty
                    # and must not be read as "this feed has no episodes".
                    logger.info(f"Unchanged since last run: {cached.get('slug') or subscription_url}")
                    results.append({"feed": cached.get("slug"), "status": "up_to_date",
                                    "not_modified": True})
                    continue
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
                self._remember_feed(cache, subscription_url, slug, feed, True)
                results.append({"feed": slug, "status": "baselined"})
                continue

            # An unidentifiable entry (no guid, no enclosure) cannot be
            # tracked, and must not take the whole download stage down with it.
            unseen = []
            for entry in feed.entries:
                try:
                    entry_id = self._entry_id(entry)
                except Exception as e:
                    logger.warning(f"Skipping an entry of '{podcast_title}' with no "
                                   f"usable id: {e}")
                    continue
                if entry_id not in feed_seen:
                    unseen.append(entry)

            # A feed migration rewrites every guid, so nothing is recognised;
            # downtime still recognises the older entries. Test that, not what
            # fraction looks new.
            identified = sum(1 for e in feed.entries if self._safe_entry_id(e))
            recognised = identified - len(unseen)
            if (feed_seen and recognised == 0
                    and identified > self.episode_limit):
                logger.warning(
                    f"{podcast_title}: none of {identified} identifiable entries match the "
                    f"{len(feed_seen)} ids we have seen — treating it as an id change "
                    "and re-baselining. Downloading nothing this run."
                )
                for entry in feed.entries:
                    try:
                        feed_seen.add(self._entry_id(entry))
                    except Exception as e:
                        logger.warning(f"Could not derive ID for entry in '{podcast_title}': {e}")
                seen[subscription_url] = feed_seen
                # This discards everything the feed offers, so never let it
                # pass unnoticed.
                self.alerter.send(
                    f"rebaseline:{slug}", "Podcast feed ids changed",
                    f"{podcast_title}: none of its {identified} entries "
                    f"match what we have seen, so they were all marked seen and "
                    f"none downloaded. If this was not a feed migration, episodes "
                    f"have been skipped.", priority="high")
                self._remember_feed(cache, subscription_url, slug, feed, True)
                results.append({"feed": slug, "status": "rebaselined"})
                continue

            # A real backlog: the seen set still matches, so these are genuinely
            # new. Only the last CATCH_UP_WINDOW_DAYS are worth downloading.
            fresh, stale = self._split_on_catch_up_window(unseen, now=now)

            for entry in stale:
                entry_id = self._safe_entry_id(entry)
                if entry_id:
                    feed_seen.add(entry_id)
            if stale:
                logger.info(f"{podcast_title}: {len(stale)} unseen episodes older "
                            f"than {CATCH_UP_WINDOW_DAYS} days marked seen, not downloaded")
                self.alerter.send(
                    f"stale-backlog:{slug}", "Old episodes skipped",
                    f"{podcast_title}: {len(stale)} unseen episodes older than "
                    f"{CATCH_UP_WINDOW_DAYS} days were marked seen without being "
                    f"downloaded.")

            # Undownloaded fresh ones stay unseen, so later runs take the rest.
            if len(fresh) > BACKLOG_ALERT_THRESHOLD:
                logger.info(f"{podcast_title}: {len(fresh)} recent episodes behind, "
                            f"taking {self.episode_limit} this run")
                self.alerter.send(
                    f"backlog:{slug}", "Podcast backlog being worked through",
                    f"{podcast_title} is {len(fresh)} recent episodes behind and is "
                    f"catching up {self.episode_limit} per run. Expect the box "
                    f"to run warm until it clears.")

            new_entries = fresh[:self.episode_limit]

            downloaded = failed = 0
            for entry in new_entries:
                result = self.download_episode(entry, podcast_title)
                if result:
                    feed_seen.add(self._entry_id(entry))
                    downloaded += 1
                else:
                    failed += 1

            seen[subscription_url] = feed_seen
            # Anything past episode_limit waits for the next run, and a failed
            # download is retried then, so neither may be hidden by a 304.
            self._remember_feed(cache, subscription_url, slug, feed,
                                failed == 0 and len(fresh) <= self.episode_limit)
            if new_entries:
                results.append({"feed": slug, "status": "downloaded",
                                "downloaded": downloaded, "failed": failed})
            elif stale:
                # Not "nothing new": episodes were retired undownloaded.
                results.append({"feed": slug, "status": "skipped_old",
                                "skipped": len(stale)})
            else:
                results.append({"feed": slug, "status": "up_to_date"})

        self._save_seen(seen)
        self._save_feed_cache(cache)
        return results

