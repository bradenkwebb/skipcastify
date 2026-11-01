import feedparser
from feedgen.feed import FeedGenerator
import os
from datetime import datetime, timezone
import logging
from mutagen.mp3 import MP3

from skipcastify.utils.utils import slugify, safe_filename
from skipcastify.services.artwork_processor import process_artwork

logger = logging.getLogger(__name__)


def _itunes_safe_image(url: str | None) -> str | None:
    """feedgen's itunes_image() raises 'Image file must be png or jpg' unless the
    URL ends in exactly .jpg or .png. Return the URL only if it qualifies, else
    None — so an unsupported image (.jpeg, a query string, no extension) is
    skipped instead of letting the exception abort the entire feed."""
    if url and (url.endswith('.jpg') or url.endswith('.png')):
        return url
    return None


class FeedManager:
    def __init__(self, server_base_url: str, data_dir: str, episode_token: str = "",
                 feeds_subdir: str = "feeds") -> None:
        self.server_base_url = server_base_url.rstrip('/')
        self.episode_token = episode_token
        self.data_dir = data_dir
        self.feeds_subdir = feeds_subdir

    # --- URL and inclusion hooks -------------------------------------------
    # These exist so a subclass can retarget the feed at different storage
    # (see R2FeedManager) without duplicating generate_feed's 100 lines of
    # per-podcast metadata handling. The base implementations are exactly the
    # behaviour that was previously inline.

    def _artwork_url(self, filename: str) -> str:
        return f"{self.server_base_url}/artwork/{self.episode_token}/{filename}"

    def _entry_audio(self, entry, slug: str, ep_filename: str):
        """Resolve one entry's enclosure.

        Returns ``(audio_url, length, local_path)``, where local_path is the
        file to read an accurate duration from or None when the episode isn't
        held locally. Returning ``None`` instead of a tuple withholds the entry
        from the feed entirely — the base manager never does that, but
        R2FeedManager uses it to keep unprocessed episodes out of the feed.
        """
        local_path = self._local_episode_path(slug, ep_filename)
        if local_path:
            audio_url = f"{self.server_base_url}/episodes/{self.episode_token}/{slug}/{ep_filename}"
            return audio_url, str(os.path.getsize(local_path)), local_path
        audio_url, length = self._original_audio_url(entry)
        return audio_url, length, None

    def _local_episode_path(self, slug: str, filename: str) -> str | None:
        """Return path to local episode file (processed preferred), or None if not downloaded."""
        for subdir in ('processed', 'raw'):
            path = os.path.join(self.data_dir, 'podcasts', subdir, slug, filename)
            if os.path.exists(path):
                return path
        return None

    def _original_audio_url(self, entry) -> tuple[str | None, str]:
        """Extract original audio URL and length from a feedparser entry."""
        if entry.enclosures:
            enc = entry.enclosures[0]
            return enc.get('href'), enc.get('length') or '0'
        for link in entry.get('links', []):
            if link.get('rel') == 'enclosure':
                return link.get('href'), link.get('length') or '0'
        return None, '0'

    def generate_feed(self, subscription_url: str) -> str:
        feed = feedparser.parse(subscription_url)
        title = feed.feed.title
        slug = slugify(title)

        fg = FeedGenerator()
        fg.load_extension('podcast')
        fg.title(title)
        fg.link(href=subscription_url)
        fg.description(feed.feed.get("description") or feed.feed.get("subtitle") or title)

        # Every feed this pipeline produces is private. The tailnet ones sit
        # behind Basic Auth on a host no crawler can reach; the R2 ones are
        # world-readable objects guarded only by a secret in the path.
        #
        # itunes:block makes Overcast treat the feed as private, and what that
        # buys is the removal of the Share and Recommend buttons from inside
        # the app — the one path by which a single tap could hand a secret
        # feed URL to someone else. It is *not* what keeps these feeds out of
        # Overcast's search: that already follows from never submitting them
        # to Apple Podcasts, which is the directory Overcast filters search
        # and recommendations against. See https://overcast.fm/podcasterinfo.
        #
        # Set here rather than only in R2FeedManager so there is one answer
        # for every feed we generate and no chance of the two drifting.
        fg.podcast.itunes_block(True)

        image_url = feed.feed.get('image', {}).get('href')
        if image_url:
            artwork_dir = os.path.join(self.data_dir, 'artwork')
            filename = process_artwork(image_url, artwork_dir, slug)
            if filename:
                local_artwork_url = self._artwork_url(filename)
                fg.image(url=local_artwork_url, title=title, link=subscription_url)
                fg.podcast.itunes_image(local_artwork_url)
            else:
                fg.image(url=image_url, title=title, link=subscription_url)
                safe_image = _itunes_safe_image(image_url)
                if safe_image:
                    fg.podcast.itunes_image(safe_image)
                else:
                    logger.debug("Skipping feed itunes:image (unsupported URL: %s)", image_url)

        author = (feed.feed.get('author_detail', {}).get('name')
                  or feed.feed.get('author')
                  or feed.feed.get('itunes_author'))
        if author:
            fg.podcast.itunes_author(author)

        if feed.feed.get('language'):
            fg.language(feed.feed.language)

        explicit_raw = feed.feed.get('itunes_explicit')
        if explicit_raw in (True, 'yes', 'true', 'explicit'):
            fg.podcast.itunes_explicit('yes')
        elif explicit_raw in (False, 'no', 'false', 'clean'):
            fg.podcast.itunes_explicit('no')

        tags = feed.feed.get('tags', [])
        if tags:
            primary = tags[0].get('term', '')
            if primary:
                fg.podcast.itunes_category(primary)

        for entry in feed.entries:
            ep_filename = safe_filename(entry.title, slug)
            decision = self._entry_audio(entry, slug, ep_filename)
            if decision is None:
                logger.debug("Withholding '%s' from %s feed", entry.title, title)
                continue
            audio_url, length, local_path = decision

            fe = fg.add_entry()
            fe.title(entry.title)
            fe.description(entry.get("summary", ""))

            # Episode identity. With no <guid>, podcast apps fall back to
            # keying on the enclosure URL — and our enclosure URL changes as an
            # episode moves from its original audio to our processed copy, so
            # the same episode would read as a brand-new one: a duplicate in
            # the app and a second download. Carrying the upstream guid through
            # keeps identity stable across that switch.
            #
            # Same rule as EpisodeDownloader._entry_id: upstream guid first,
            # original audio URL as the fallback. The fallback is deliberately
            # the *original* URL, never ours, because ours is the thing that
            # moves. permalink=False because the string is what establishes
            # identity and many podcast guids are not resolvable URLs.
            original_url, _original_len = self._original_audio_url(entry)
            guid = entry.get('id') or entry.get('guid') or original_url
            if guid:
                fe.guid(guid, permalink=False)

            if entry.get('published_parsed'):
                fe.pubDate(datetime(*entry.published_parsed[:6], tzinfo=timezone.utc))
            else:
                logger.warning(f"Episode '{entry.title}' of {title} has no publish date.")

            if hasattr(entry, "link"):
                fe.link(href=entry.link)

            # Pass through per-episode artwork (e.g. BBC Global News). We use the
            # original image URL directly rather than re-framing each one, which
            # would mean downloading every episode image on every feed-gen cycle.
            # Guard it: feedgen rejects any URL not ending in .jpg/.png, and an
            # unsupported episode image must not abort the whole feed.
            ep_image = _itunes_safe_image(entry.get('image', {}).get('href'))
            if ep_image:
                fe.podcast.itunes_image(ep_image)

            if audio_url:
                try:
                    fe.enclosure(url=audio_url, type="audio/mpeg", length=length)
                except Exception as e:
                    logger.warning(f"No usable enclosure for '{entry.title}' from {title}: {e}")

            if local_path:
                try:
                    seconds = int(MP3(local_path).info.length)
                    duration = f"{seconds // 3600:02}:{(seconds % 3600) // 60:02}:{seconds % 60:02}"
                except Exception as e:
                    logger.warning(f"Could not read duration from {local_path}: {e}")
                    duration = entry.get('itunes_duration')
            else:
                duration = entry.get('itunes_duration')
            if duration:
                fe.podcast.itunes_duration(duration)

            # Only propagate an episode-level itunes:author when the original
            # had one explicitly. feedparser's generic entry.author falls back
            # to <dc:creator>/<author> (e.g. the hosts' names), which podcast
            # apps display in place of the feed-level author — diverging from
            # how the original feed appeared.
            ep_author = entry.get('itunes_author')
            if ep_author:
                fe.podcast.itunes_author(ep_author)

            if entry.get('itunes_episode'):
                fe.podcast.itunes_episode(str(entry.itunes_episode))
            if entry.get('itunes_season'):
                fe.podcast.itunes_season(str(entry.itunes_season))

            ep_explicit_raw = entry.get('itunes_explicit')
            if ep_explicit_raw in (True, 'yes', 'true', 'explicit'):
                fe.podcast.itunes_explicit('yes')
            elif ep_explicit_raw in (False, 'no', 'false', 'clean'):
                fe.podcast.itunes_explicit('no')

        feed_path = os.path.join(self.data_dir, self.feeds_subdir, f'{slug}.xml')
        os.makedirs(os.path.dirname(feed_path), exist_ok=True)
        fg.rss_file(feed_path, pretty=True)
        return feed_path
