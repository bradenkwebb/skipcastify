"""Publish ad-free feeds to Cloudflare R2 so podcast apps can read them.

Overcast fetches feeds from its own crawlers, which cannot reach bw-server's
tailnet, and we do not want any inbound exposure of the box. So the pipeline
pushes static objects out to R2 and the published feed points there; bw-server
stays tailnet-only.

Object layout (the ``audio/`` prefix must match the bucket's lifecycle rule):

    audio/<SECRET>/<feed-slug>/<file>.mp3
    feeds/<SECRET>/<feed-slug>.xml
    art/<SECRET>/<file>

``SECRET`` is ``PUBLIC_PATH_SECRET``. It is obscurity, not access control: the
bucket is world-readable, so anyone holding a URL can fetch the object. Keys
therefore never reach a log file without being redacted first.

How an episode is decided into or out of a published feed:

===========================================  ==================================
state                                        enclosure
===========================================  ==================================
processed audio uploaded                     R2 URL (the normal case)
downloaded, unprocessed, younger than 6h     withheld — still being worked on
downloaded, unprocessed, 6h to 14d           original URL, alerted once
published once, then evicted from R2         original URL, same guid
never downloaded (pre-go-live backlog)       original URL
===========================================  ==================================

A published feed is therefore a rolling retention-window of ad-free episodes,
with everything outside that window — both the pre-go-live backlog and
episodes that have aged out of it — still listed against its original audio.
An episode's guid never changes as it moves between those states, so a podcast
app sees one episode throughout rather than a new one each time.
"""

import hashlib
import logging
import os
import re
import time
from pathlib import Path

from skipcastify.services.generate_feeds import FeedManager
from skipcastify.services.publish_manifest import PublishManifest
from skipcastify.services.r2_client import (CACHE_FEED, CACHE_IMMUTABLE,
                                            CREDENTIAL_ERROR_CODES, R2Client,
                                            content_type_for, error_code,
                                            redact_key)
from skipcastify.utils.alerts import Alerter
from skipcastify.utils.notify import notify as ntfy_notify

logger = logging.getLogger(__name__)

GB = 1024 ** 3
DEFAULT_RETENTION_DAYS = 14
DEFAULT_CAP_GB = 7
DEFAULT_FALLBACK_HOURS = 6
# Consecutive upload/delete failures in one run before we call it systemic.
# A pilot run performs single-digit operations, so three failures is not noise.
DEFAULT_MAX_FAILURES_PER_RUN = 3
# Cloudflare's free tier. Class B (reads) is 10M and is spent by listeners
# rather than by us, so only Class A is worth tracking locally.
CLASS_A_MONTHLY_LIMIT = 1_000_000
ALERT_AT = 0.8
# Keep tombstones well past retention so the re-upload guard can never race.
TOMBSTONE_TTL_S = 60 * 86400

_LAST_BUILD_DATE = re.compile(rb"<lastBuildDate>.*?</lastBuildDate>", re.S)


def feed_fingerprint(xml: bytes) -> str:
    """Hash of a feed's *meaningful* content.

    feedgen stamps <lastBuildDate> with the generation time, so hashing raw
    bytes would differ on every run and re-upload an identical feed every 15
    minutes — precisely the Class A spend the changed-only rule exists to
    avoid. Normalising that one element out makes the hash stable.
    """
    return hashlib.sha256(_LAST_BUILD_DATE.sub(b"", xml)).hexdigest()


class R2FeedManager(FeedManager):
    """A feed whose enclosures point at R2.

    This class owns only the question of *what belongs in the feed*. Uploading
    is delegated back to the publisher, which owns the storage cap, the
    manifest and alerting.
    """

    def __init__(self, publisher, feeds_subdir: str = "feeds_r2") -> None:
        super().__init__(
            server_base_url=publisher.public_base_url,
            data_dir=publisher.data_dir,
            episode_token="",
            feeds_subdir=feeds_subdir,
        )
        self.pub = publisher

    def _artwork_url(self, filename: str) -> str:
        # Best effort: artwork is a few hundred KB and a missing image only
        # costs a blank thumbnail, so an upload failure must not withhold the
        # whole feed. ensure_art logs the failure.
        self.pub.ensure_art(filename)
        return self.pub.public_url(self.pub.art_key(filename))

    def _entry_audio(self, entry, slug: str, ep_filename: str):
        pub = self.pub
        key = pub.audio_key(slug, ep_filename)

        if pub.manifest.is_tombstoned(key):
            # Published once, then evicted — by retention, or by the cap making
            # room for something newer. Keep listing it with its original
            # audio rather than withdrawing the item: the guid is unchanged, so
            # a podcast app keeps the episode and any copy already downloaded
            # instead of dropping it from the queue. Braden often has episodes
            # in progress well past the retention window.
            #
            # The tombstone still does its main job here, which is to stop the
            # processed file on local disk being uploaded all over again.
            url, length = self._original_audio_url(entry)
            return url, length, None

        processed = os.path.join(pub.data_dir, 'podcasts', 'processed', slug, ep_filename)
        if os.path.exists(processed):
            # Audio processed longer ago than the retention window predates
            # go-live. This box holds ~90 days of July/August episodes, and
            # uploading them on the first run would both contradict "start
            # fresh" and make them all disappear again 14 days later. Treat
            # them as backlog: original enclosure, never uploaded.
            if time.time() - os.path.getmtime(processed) >= pub.retention_s:
                url, length = self._original_audio_url(entry)
                return url, length, None
            url = pub.ensure_audio(key, processed, slug, ep_filename)
            if url:
                return url, str(os.path.getsize(processed)), processed
            # Upload refused (cap reached, or the PUT failed). Withhold rather
            # than silently serving the version with ads in it.
            return None

        raw = os.path.join(pub.data_dir, 'podcasts', 'raw', slug, ep_filename)
        if not os.path.exists(raw):
            # Never downloaded: pre-go-live backlog. Keep the original audio.
            url, length = self._original_audio_url(entry)
            return url, length, None

        # Downloaded but not processed. The raw file's mtime is the download
        # time, which is exactly the clock the fallback window runs on.
        age_s = time.time() - os.path.getmtime(raw)
        if age_s >= pub.retention_s:
            # Same reasoning as the processed case above: downloaded long
            # before go-live, so it is backlog rather than a stuck episode.
            # It has already alerted once on its way through the fallback
            # window, so keep listing it instead of making it vanish.
            url, length = self._original_audio_url(entry)
            return url, length, None
        if age_s < pub.fallback_after_s:
            return None                      # still in the processing window
        pub.note_fallback(slug, ep_filename, age_s)
        url, length = self._original_audio_url(entry)
        return url, length, None


class R2Publisher:
    """Uploads audio, artwork and feeds to R2 under the free-tier guardrails."""

    def __init__(self, client: R2Client, manifest: PublishManifest, secret: str,
                 public_base_url: str, data_dir: str,
                 retention_days: int = DEFAULT_RETENTION_DAYS,
                 cap_bytes: int = DEFAULT_CAP_GB * GB,
                 fallback_after_s: float = DEFAULT_FALLBACK_HOURS * 3600,
                 notifier=None, alerter=None,
                 max_failures_per_run: int = DEFAULT_MAX_FAILURES_PER_RUN) -> None:
        if not secret:
            raise ValueError("PUBLIC_PATH_SECRET must not be empty")
        self.client = client
        self.manifest = manifest
        self.secret = secret
        self.public_base_url = public_base_url.rstrip('/')
        self.data_dir = data_dir
        self.retention_days = retention_days
        self.retention_s = retention_days * 86400
        self.cap_bytes = cap_bytes
        self.fallback_after_s = fallback_after_s
        # Two alert paths, because they want different semantics:
        #   * _alert_once  — fires once ever, recorded in the manifest. Right
        #     for "this episode went out with ads" or "80% of the monthly
        #     budget", where repeating on a timer adds nothing.
        #   * self.alerts  — 6h throttle, shared with the pipeline's alerter.
        #     Right for a condition that may clear up and recur, like a
        #     rejected credential or a failing upload.
        self.alerts = (alerter if alerter is not None
                       else Alerter(data_dir, notifier=notifier or ntfy_notify))
        self._notify = notifier if notifier is not None else self.alerts.notifier
        self.max_failures_per_run = max_failures_per_run
        # Why publishing stopped for the rest of this run, if it did.
        self._halted: str | None = None
        self._failures = 0
        # Audio keys already written into a feed during the current pass. The
        # cap must not evict one of these: the feed referencing it has been
        # built (and possibly uploaded) already, so deleting the object would
        # leave a published feed pointing at a 404.
        self._emitted_keys: set[str] = set()

    # --- construction -------------------------------------------------------

    @classmethod
    def from_env(cls, data_dir: str, manifest: PublishManifest | None = None,
                 alerter=None) -> "R2Publisher":
        secret = os.environ.get("PUBLIC_PATH_SECRET", "").strip()
        if not secret:
            raise ValueError(
                "PUBLIC_PATH_SECRET is not set. Generate one with:\n"
                "  python -c 'import secrets; print(secrets.token_urlsafe(32))'\n"
                "and add it to .env. Do not reuse EPISODE_TOKEN."
            )
        base = os.environ.get("PUBLIC_BASE_URL", "").strip()
        if not base:
            raise ValueError("PUBLIC_BASE_URL is not set")
        manifest = manifest or PublishManifest(data_dir)
        client = R2Client(
            endpoint=os.environ.get("R2_ENDPOINT", ""),
            bucket=os.environ.get("R2_BUCKET", ""),
            access_key_id=os.environ.get("R2_ACCESS_KEY_ID", ""),
            secret_access_key=os.environ.get("R2_SECRET_ACCESS_KEY", ""),
            secret_segment=secret,
            on_class_a=manifest.bump_class_a,
        )
        return cls(
            client=client, manifest=manifest, secret=secret,
            public_base_url=base, data_dir=data_dir,
            retention_days=int(os.environ.get("R2_RETENTION_DAYS", DEFAULT_RETENTION_DAYS)),
            cap_bytes=int(float(os.environ.get("R2_CAP_GB", DEFAULT_CAP_GB)) * GB),
            fallback_after_s=float(os.environ.get("R2_FALLBACK_HOURS", DEFAULT_FALLBACK_HOURS)) * 3600,
            alerter=alerter,
            max_failures_per_run=int(os.environ.get(
                "R2_MAX_FAILURES_PER_RUN", DEFAULT_MAX_FAILURES_PER_RUN)),
        )

    # --- keys and URLs ------------------------------------------------------

    def audio_key(self, slug: str, filename: str) -> str:
        return f"audio/{self.secret}/{slug}/{filename}"

    def feed_key(self, slug: str) -> str:
        return f"feeds/{self.secret}/{slug}.xml"

    def art_key(self, filename: str) -> str:
        return f"art/{self.secret}/{filename}"

    def public_url(self, key: str) -> str:
        return f"{self.public_base_url}/{key}"

    def _safe(self, key: str) -> str:
        return redact_key(key, self.secret)

    # --- failure routing ----------------------------------------------------

    def _halt(self, reason: str) -> None:
        """Stop publishing for the rest of this run.

        Reached when R2 rejects our credentials or the bucket is unreachable.
        Pushing on would mean one failed upload per episode per feed and a
        wall of log noise saying the same thing. Downloading and processing are
        untouched: the only thing that stops is publishing.
        """
        if self._halted is None:
            self._halted = reason
            logger.error("R2 publishing halted for this run: %s", reason)

    @property
    def halted(self) -> str | None:
        return self._halted

    def _note_r2_error(self, operation: str, exc: Exception) -> None:
        """Turn a logged-and-swallowed R2 failure into something audible.

        Every call site here used to log and carry on, which is the right
        behaviour for the run but means a revoked API token looks exactly like
        nothing happening: feeds quietly stop updating and nothing says why.
        """
        code = error_code(exc)
        if code in CREDENTIAL_ERROR_CODES:
            self._halt(f"R2 returned {code}")
            # Fingerprinted on the code, not the operation: whichever call
            # hits it first is the one that reports it.
            self.alerts.send(
                f"r2-credentials:{code}", "R2 credentials rejected",
                f"R2 returned {code}, so publishing has stopped. Downloads "
                f"and processing continue. Check the bucket API token in the "
                f"Cloudflare dashboard.", priority="high")
            return

        self._failures += 1
        logger.error("R2 %s failed (%d this run): %s", operation,
                     self._failures, code or exc)
        if self._failures == self.max_failures_per_run:
            self.alerts.send(
                "r2-failures", "Repeated R2 failures",
                f"{self._failures} R2 operations have failed this run, the "
                f"latest a {operation} ({code or type(exc).__name__}). The "
                f"published feeds may be stale.", priority="high")

    # --- uploads ------------------------------------------------------------

    def ensure_audio(self, key: str, path: str, slug: str,
                     filename: str) -> str | None:
        """Upload an episode if it isn't already in R2. Returns its URL."""
        if self.manifest.has(key):
            self._emitted_keys.add(key)
            return self.public_url(key)

        if self._halted:
            return None

        size = os.path.getsize(path)
        if not self._make_room(size):
            self._alert_once(
                f"cap:{key}", "R2 storage cap reached",
                f"Skipped {filename} ({size / 1024 ** 2:.0f} MB): it would "
                f"exceed the {self.cap_bytes / GB:.0f} GB cap and there is "
                f"nothing older left to evict.", priority="high")
            return None

        try:
            self.client.put_file(key, path, CACHE_IMMUTABLE)
        except Exception as e:
            self._note_r2_error(f"upload of {filename}", e)
            return None

        self.manifest.record_put(key, size, "audio")
        self.manifest.save()
        self._emitted_keys.add(key)
        return self.public_url(key)

    def ensure_art(self, filename: str) -> str | None:
        """Upload feed artwork once. Best effort: failure is cosmetic."""
        key = self.art_key(filename)
        if self.manifest.has(key):
            return self.public_url(key)
        if self._halted:
            return None
        path = os.path.join(self.data_dir, 'artwork', filename)
        if not os.path.exists(path):
            return None
        try:
            size = self.client.put_file(key, path, CACHE_IMMUTABLE)
        except Exception as e:
            self._note_r2_error("artwork upload", e)
            return None
        self.manifest.record_put(key, size, "art")
        self.manifest.save()
        return self.public_url(key)

    def publish_feed(self, subscription_url: str, new_pass: bool = True) -> str | None:
        """Publish one feed. Audio is uploaded first, as a side effect of
        generating the XML, so the feed can never reference a missing object.

        publish_all clears the emitted-key set once for the whole run and
        passes new_pass=False, so one feed's uploads cannot evict an object
        that an earlier feed in the same run already points at.
        """
        if new_pass:
            self._emitted_keys = set()
        fm = R2FeedManager(self)
        feed_path = fm.generate_feed(subscription_url)
        xml = Path(feed_path).read_bytes()
        slug = Path(feed_path).stem
        key = self.feed_key(slug)

        fingerprint = feed_fingerprint(xml)
        if self.manifest.has(key) and self.manifest.sha256(key) == fingerprint:
            logger.info("Feed %s unchanged; skipping upload", slug)
            return self.public_url(key)

        if self._halted:
            return None

        try:
            self.client.put_bytes(key, xml, "application/rss+xml", CACHE_FEED)
        except Exception as e:
            self._note_r2_error(f"feed upload for {slug}", e)
            return None
        self.manifest.record_put(key, len(xml), "feed", sha256=fingerprint)
        self.manifest.save()
        return self.public_url(key)

    # --- retention and the storage cap -------------------------------------

    def _delete(self, keys: list[str]) -> None:
        """Delete from R2, then tombstone exactly what R2 confirmed gone.

        A key R2 refused to delete stays live in the manifest on purpose: it
        is still costing storage, so the next run should try again rather than
        forget it.
        """
        if not keys:
            return
        try:
            deleted = self.client.delete_keys(keys)
        except Exception as e:
            self._note_r2_error("delete", e)
            return
        if len(deleted) < len(keys):
            self._note_r2_error(
                f"delete ({len(keys) - len(deleted)} of {len(keys)} keys "
                f"refused)", RuntimeError("partial delete"))
        self.manifest.record_delete(deleted)
        self.manifest.save()

    def enforce_retention(self, now: float | None = None) -> list[str]:
        expired = self.manifest.expired("audio", self.retention_s, now)
        if expired:
            logger.info("Retention: evicting %d episode(s) older than %d days",
                        len(expired), self.retention_days)
            self._delete(expired)
        self.manifest.prune_tombstones(TOMBSTONE_TTL_S, now)
        return expired

    def _make_room(self, size: int) -> bool:
        """Evict until `size` more bytes fit under the cap. False if it can't."""
        if self.manifest.total_bytes("audio") + size <= self.cap_bytes:
            return True
        self.enforce_retention()
        for key in self.manifest.oldest_first("audio"):
            if self.manifest.total_bytes("audio") + size <= self.cap_bytes:
                break
            if key in self._emitted_keys:
                continue          # a feed built this pass already points at it
            logger.info("Cap: evicting %s to make room", self._safe(key))
            self._delete([key])
        return self.manifest.total_bytes("audio") + size <= self.cap_bytes

    # --- alerting -----------------------------------------------------------

    def _alert_once(self, tag: str, title: str, message: str,
                    priority: str = "default") -> None:
        if self.manifest.was_notified(tag):
            return
        self._notify(title, message, priority)
        self.manifest.mark_notified(tag)
        self.manifest.save()

    def note_fallback(self, slug: str, filename: str, age_s: float) -> None:
        self._alert_once(
            f"fallback:{slug}/{filename}",
            "Episode published with ads",
            f"{filename} has waited {age_s / 3600:.1f}h with no processed "
            f"version, so the feed now points at the original audio.",
            priority="high")

    def check_budgets(self, now: float | None = None) -> None:
        """Warn at 80% of the storage cap or the monthly Class A allowance."""
        month = time.strftime("%Y-%m", time.gmtime(now or time.time()))

        used = self.manifest.total_bytes("audio")
        if used >= self.cap_bytes * ALERT_AT:
            self._alert_once(
                f"storage80:{month}", "R2 storage at 80%",
                f"{used / GB:.1f} GB of the {self.cap_bytes / GB:.0f} GB cap in use.",
                priority="default")

        ops = self.manifest.class_a_this_month(now)
        if ops >= CLASS_A_MONTHLY_LIMIT * ALERT_AT:
            self._alert_once(
                f"classa80:{month}", "R2 Class A ops at 80%",
                f"{ops:,} of {CLASS_A_MONTHLY_LIMIT:,} free Class A operations "
                f"used in {month}.", priority="high")

    # --- entry point --------------------------------------------------------

    def publish_all(self, subscription_urls: list[str]) -> dict[str, str]:
        """Publish every feed. Retention runs first so an episode that has
        aged out is already tombstoned before the feed is rebuilt."""
        self._halted = None
        self._failures = 0

        # One Class B HEAD before doing anything. A dead endpoint or a revoked
        # token otherwise shows up as a failed upload per episode per feed,
        # all saying the same thing; this says it once, up front.
        if not self.client.head_bucket():
            self._halt("bucket unreachable")
            self.alerts.send(
                "r2-unreachable", "R2 bucket unreachable",
                f"head_bucket failed for {self.client.bucket}, so nothing was "
                f"published this run. Downloads and processing continue.",
                priority="high")
            return {}

        self.enforce_retention()
        self._emitted_keys = set()
        published: dict[str, str] = {}
        for url in subscription_urls:
            if self._halted:
                logger.warning("Skipping the remaining feed(s): %s", self._halted)
                break
            try:
                result = self.publish_feed(url, new_pass=False)
                if result:
                    published[url] = result
            except Exception as e:
                logger.warning("Publishing failed for %s: %s", url, e, exc_info=True)
        self.check_budgets()
        self.manifest.save()
        return published
