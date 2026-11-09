"""The downloader asks for a 304 before re-reading a feed.

The pipeline runs every 15 minutes over every subscription, so without HTTP
validators each run re-downloaded every feed's full XML.
"""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from skipcastify.services.download_episode import EpisodeDownloader

FEED_URL = "https://feed.test/rss"


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
    def __init__(self, eid):
        self.id, self.title = eid, eid
        self.enclosures = [{"href": f"https://cdn.test/{eid}.mp3", "length": "1"}]

    def get(self, key, default=None):
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
        downloader.download_latest()
    assert parse.call_args.kwargs == {"etag": None, "modified": None}
    assert _cache(tmp_path)[FEED_URL] == {
        "slug": "my-show", "etag": 'W/"abc"',
        "modified": "Mon, 09 Nov 2026 10:00:00 GMT"}


def test_the_next_fetch_sends_them_back(downloader, tmp_path):
    with patch("feedparser.parse", return_value=reply([FakeEntry("a")], etag='W/"abc"')):
        downloader.download_latest()
    with patch("feedparser.parse", return_value=reply([FakeEntry("a")], etag='W/"abc"')) as parse:
        downloader.download_latest()
    assert parse.call_args.kwargs == {"etag": 'W/"abc"', "modified": None}


def test_a_304_short_circuits_the_feed(downloader, tmp_path):
    """No body is sent with a 304, so feed.entries is empty and must not be
    read as "this show has no episodes"."""
    with patch("feedparser.parse", return_value=reply([FakeEntry("a")], etag='W/"abc"')):
        downloader.download_latest()
    seen_before = (tmp_path / "seen_episodes.json").read_text()

    with patch("feedparser.parse", return_value=reply(status=304)):
        results = downloader.download_latest()

    assert results == [{"feed": "my-show", "status": "up_to_date", "not_modified": True}]
    assert (tmp_path / "seen_episodes.json").read_text() == seen_before
    assert _cache(tmp_path)[FEED_URL]["etag"] == 'W/"abc"', "the validator was dropped"


def test_a_304_does_not_download_anything(downloader, tmp_path):
    downloaded = []
    downloader.download_episode = lambda entry, title: downloaded.append(entry.id)
    with patch("feedparser.parse", return_value=reply([FakeEntry("a")], etag='W/"abc"')):
        downloader.download_latest()
    with patch("feedparser.parse", return_value=reply(status=304)):
        downloader.download_latest()
    assert downloaded == []


def test_a_feed_that_sends_no_validators_stays_unconditional(downloader, tmp_path):
    with patch("feedparser.parse", return_value=reply([FakeEntry("a")])):
        downloader.download_latest()
    assert _cache(tmp_path)[FEED_URL] == {"slug": "my-show"}
    with patch("feedparser.parse", return_value=reply([FakeEntry("a")])) as parse:
        downloader.download_latest()
    assert parse.call_args.kwargs == {"etag": None, "modified": None}


def test_an_unreadable_cache_costs_one_unconditional_fetch(downloader, tmp_path):
    (tmp_path / "feed_cache.json").write_text("{ not json")
    with patch("feedparser.parse", return_value=reply([FakeEntry("a")], etag='W/"x"')) as parse:
        results = downloader.download_latest()
    assert parse.call_args.kwargs == {"etag": None, "modified": None}
    assert results == [{"feed": "my-show", "status": "baselined"}]
    assert _cache(tmp_path)[FEED_URL]["etag"] == 'W/"x"'
