"""The downloader asks for a 304 before re-reading a feed.

The pipeline runs every 15 minutes over every subscription, so without HTTP
validators each run re-downloaded every feed's full XML.
"""

import calendar
import json
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from skipcastify.services.download_episode import EpisodeDownloader

FEED_URL = "https://feed.test/rss"
# A fixed clock: an entry with no parseable date counts as stale, so the
# entries here need real publish times to be downloadable at all.
NOW = calendar.timegm((2026, 11, 5, 12, 0, 0, 0, 0, 0))


@pytest.fixture
def downloader(tmp_path):
    opml = tmp_path / "subs.opml"
    opml.write_text('<opml version="1.0"><body>'
                    f'<outline type="rss" xmlUrl="{FEED_URL}"/>'
                    '</body></opml>')
    d = EpisodeDownloader(str(opml), str(tmp_path))
    d.download_episode = lambda entry, title: f"/tmp/{entry.id}.mp3"
    return d


class FakeEntry:
    def __init__(self, eid, days_ago=0.0):
        self.id, self.title = eid, eid
        self.enclosures = [{"href": f"https://cdn.test/{eid}.mp3", "length": "1"}]
        self.days_ago = days_ago

    def get(self, key, default=None):
        if key in ("published_parsed", "updated_parsed"):
            return time.gmtime(NOW - self.days_ago * 86400)
        return {"id": self.id}.get(key, default)


def reply(entries=(), *, status=200, etag=None, modified=None, title="My Show"):
    feed = SimpleNamespace(feed=SimpleNamespace(title=title),
                           entries=list(entries), status=status)
    if etag is not None:
        feed.etag = etag
    if modified is not None:
        feed.modified = modified
    return feed


def _cache(tmp_path):
    path = tmp_path / "feed_cache.json"
    return json.loads(path.read_text()) if path.exists() else None


def test_the_first_fetch_is_unconditional_and_stores_the_validators(downloader, tmp_path):
    with patch("feedparser.parse", return_value=reply(
            [FakeEntry("a")], etag='W/"abc"', modified="Mon, 09 Nov 2026 10:00:00 GMT")) as parse:
        downloader.download_latest(now=NOW)
    assert parse.call_args.kwargs == {"etag": None, "modified": None}
    assert _cache(tmp_path)[FEED_URL] == {
        "slug": "my-show", "etag": 'W/"abc"',
        "modified": "Mon, 09 Nov 2026 10:00:00 GMT"}


def test_the_next_fetch_sends_them_back(downloader, tmp_path):
    with patch("feedparser.parse", return_value=reply([FakeEntry("a")], etag='W/"abc"')):
        downloader.download_latest(now=NOW)
    with patch("feedparser.parse", return_value=reply([FakeEntry("a")], etag='W/"abc"')) as parse:
        downloader.download_latest(now=NOW)
    assert parse.call_args.kwargs == {"etag": 'W/"abc"', "modified": None}


def test_a_304_short_circuits_the_feed(downloader, tmp_path):
    """No body is sent with a 304, so feed.entries is empty and must not be
    read as "this show has no episodes"."""
    with patch("feedparser.parse", return_value=reply([FakeEntry("a")], etag='W/"abc"')):
        downloader.download_latest(now=NOW)
    seen_before = (tmp_path / "seen_episodes.json").read_text()

    with patch("feedparser.parse", return_value=reply(status=304)):
        results = downloader.download_latest(now=NOW)

    assert results == [{"feed": "my-show", "status": "up_to_date", "not_modified": True}]
    assert (tmp_path / "seen_episodes.json").read_text() == seen_before
    assert _cache(tmp_path)[FEED_URL]["etag"] == 'W/"abc"', "the validator was dropped"


def test_a_304_does_not_download_anything(downloader, tmp_path):
    downloaded = []
    downloader.download_episode = lambda entry, title: downloaded.append(entry.id)
    with patch("feedparser.parse", return_value=reply([FakeEntry("a")], etag='W/"abc"')):
        downloader.download_latest(now=NOW)
    with patch("feedparser.parse", return_value=reply(status=304)):
        downloader.download_latest(now=NOW)
    assert downloaded == []


def test_a_feed_that_sends_no_validators_stays_unconditional(downloader, tmp_path):
    with patch("feedparser.parse", return_value=reply([FakeEntry("a")])):
        downloader.download_latest(now=NOW)
    assert _cache(tmp_path)[FEED_URL] == {"slug": "my-show"}
    with patch("feedparser.parse", return_value=reply([FakeEntry("a")])) as parse:
        downloader.download_latest(now=NOW)
    assert parse.call_args.kwargs == {"etag": None, "modified": None}


def test_an_unreadable_cache_costs_one_unconditional_fetch(downloader, tmp_path):
    (tmp_path / "feed_cache.json").write_text("{ not json")
    with patch("feedparser.parse", return_value=reply([FakeEntry("a")], etag='W/"x"')) as parse:
        results = downloader.download_latest(now=NOW)
    assert parse.call_args.kwargs == {"etag": None, "modified": None}
    assert results == [{"feed": "my-show", "status": "baselined"}]
    assert _cache(tmp_path)[FEED_URL]["etag"] == 'W/"x"'


class TestAValidatorIsOnlyStoredWhenNothingIsOutstanding:
    """A 304 skips the feed entirely, so storing validators after an
    incomplete run would strand the work. Undownloaded fresh episodes and
    failed downloads both stay unseen on purpose, for the next run to take.
    """

    def _baseline(self, downloader):
        with patch("feedparser.parse", return_value=reply([FakeEntry("old")], etag='W/"1"')):
            downloader.download_latest(now=NOW)

    def test_a_failed_download_keeps_the_next_fetch_unconditional(self, downloader, tmp_path):
        self._baseline(downloader)
        downloader.download_episode = lambda entry, title: None   # every one fails
        with patch("feedparser.parse",
                   return_value=reply([FakeEntry("old"), FakeEntry("new")], etag='W/"2"')):
            results = downloader.download_latest(now=NOW)
        assert results[0]["failed"] == 1
        assert "etag" not in _cache(tmp_path)[FEED_URL], \
            "a 304 next run would strand the episode that failed"

        with patch("feedparser.parse", return_value=reply([FakeEntry("old")], etag='W/"2"')) as parse:
            downloader.download_latest(now=NOW)
        assert parse.call_args.kwargs == {"etag": None, "modified": None}

    def test_a_backlog_past_episode_limit_keeps_it_unconditional(self, downloader, tmp_path):
        self._baseline(downloader)
        downloader.episode_limit = 2
        entries = [FakeEntry("old")] + [FakeEntry(f"n{i}") for i in range(5)]
        with patch("feedparser.parse", return_value=reply(entries, etag='W/"2"')):
            results = downloader.download_latest(now=NOW)
        assert results[0]["downloaded"] == 2            # 3 still waiting
        assert "etag" not in _cache(tmp_path)[FEED_URL], \
            "the rest of the backlog would never be fetched"

    def test_once_the_backlog_clears_the_validator_comes_back(self, downloader, tmp_path):
        self._baseline(downloader)
        downloader.episode_limit = 2
        entries = [FakeEntry("old")] + [FakeEntry(f"n{i}") for i in range(3)]
        with patch("feedparser.parse", return_value=reply(entries, etag='W/"2"')):
            downloader.download_latest(now=NOW)
        assert "etag" not in _cache(tmp_path)[FEED_URL]
        with patch("feedparser.parse", return_value=reply(entries, etag='W/"3"')):
            downloader.download_latest(now=NOW)                 # takes the last one
        assert _cache(tmp_path)[FEED_URL]["etag"] == 'W/"3"'

    def test_the_slug_is_remembered_even_after_an_incomplete_run(self, downloader, tmp_path):
        """It is what names the feed in the run log on a later 304."""
        self._baseline(downloader)
        downloader.download_episode = lambda entry, title: None
        with patch("feedparser.parse",
                   return_value=reply([FakeEntry("old"), FakeEntry("new")], etag='W/"2"')):
            downloader.download_latest(now=NOW)
        assert _cache(tmp_path)[FEED_URL] == {"slug": "my-show"}
