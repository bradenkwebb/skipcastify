"""
Tests for feed generation robustness across different podcast feed structures.
All tests use mocked feedparser data — no network calls.
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from skipcastify.services.generate_feeds import FeedManager


def _make_entry(title="Ep 1", published_parsed=(2025, 1, 1, 12, 0, 0, 0, 0, 0), enclosures=None):
    entry = MagicMock()
    entry.title = title
    entry.published_parsed = published_parsed
    entry.link = "https://example.com/ep"
    entry.get.side_effect = lambda k, default=None: {"summary": "desc"}.get(k, default)
    entry.enclosures = enclosures if enclosures is not None else [
        {"length": "12345", "href": "https://cdn.example.com/ep.mp3"}
    ]
    return entry


def _make_feed(entries, image_href="https://example.com/art.jpg"):
    feed = MagicMock()
    feed.feed.title = "Test Podcast"
    image = {"href": image_href} if image_href else {}
    feed.feed.get.side_effect = lambda k, default=None: {
        "description": "A podcast", "image": image
    }.get(k, default)
    feed.entries = entries
    return feed


@pytest.fixture
def fm(tmp_path):
    return FeedManager(
        server_base_url="http://localhost:5000",
        data_dir=str(tmp_path),
        episode_token="tok",
    )


def test_episode_url_uses_server_when_file_downloaded(fm, tmp_path):
    """When a local raw file exists, the enclosure URL should point to our server."""
    raw_dir = tmp_path / 'podcasts' / 'raw' / 'test-podcast'
    raw_dir.mkdir(parents=True)
    (raw_dir / 'test-podcast-ep_1.mp3').write_bytes(b'audio')

    feed = _make_feed([_make_entry(title="Ep 1")])
    with patch("feedparser.parse", return_value=feed), \
         patch("skipcastify.services.generate_feeds.process_artwork", return_value=None):
        path = fm.generate_feed("https://example.com/feed.xml")

    content = Path(path).read_text()
    assert "localhost:5000/episodes" in content
    assert "cdn.example.com" not in content


def test_episode_url_falls_back_to_original_when_not_downloaded(fm, tmp_path):
    """When no local file exists, the enclosure URL should be the original CDN URL."""
    feed = _make_feed([_make_entry(title="Ep 1")])
    with patch("feedparser.parse", return_value=feed), \
         patch("skipcastify.services.generate_feeds.process_artwork", return_value=None):
        path = fm.generate_feed("https://example.com/feed.xml")

    content = Path(path).read_text()
    assert "cdn.example.com/ep.mp3" in content
    assert "localhost:5000/episodes" not in content


def test_processed_file_takes_precedence_over_raw(fm, tmp_path):
    """Processed version of an episode should be served even when raw also exists."""
    slug = 'test-podcast'
    filename = f'{slug}-ep_1.mp3'
    (tmp_path / 'podcasts' / 'raw' / slug).mkdir(parents=True)
    (tmp_path / 'podcasts' / 'raw' / slug / filename).write_bytes(b'raw')
    (tmp_path / 'podcasts' / 'processed' / slug).mkdir(parents=True)
    (tmp_path / 'podcasts' / 'processed' / slug / filename).write_bytes(b'clean')

    feed = _make_feed([_make_entry(title="Ep 1")])
    with patch("feedparser.parse", return_value=feed), \
         patch("skipcastify.services.generate_feeds.process_artwork", return_value=None):
        path = fm.generate_feed("https://example.com/feed.xml")

    content = Path(path).read_text()
    assert "localhost:5000/episodes" in content


def test_feed_without_artwork_generates_successfully(fm, tmp_path):
    """Feeds with no artwork must still generate without crashing."""
    feed = _make_feed([_make_entry()], image_href=None)
    with patch("feedparser.parse", return_value=feed), \
         patch("skipcastify.services.generate_feeds.process_artwork", return_value=None):
        path = fm.generate_feed("https://example.com/feed.xml")

    assert Path(path).exists()
    assert "localhost:5000/artwork" not in Path(path).read_text()


def test_entries_without_pubdate_are_included(fm, tmp_path):
    """Entries with no publish date must be included — no crash, no silent drop."""
    feed = _make_feed([_make_entry(published_parsed=None)])
    with patch("feedparser.parse", return_value=feed), \
         patch("skipcastify.services.generate_feeds.process_artwork", return_value=None):
        path = fm.generate_feed("https://example.com/feed.xml")

    assert "Ep 1" in Path(path).read_text()


def test_entries_without_enclosure_are_included(fm, tmp_path):
    """Entries with no audio enclosure must not crash feed generation."""
    entry_no_enc = _make_entry(title="Bonus Post", enclosures=[])
    entry_with_enc = _make_entry(title="Normal Episode")
    feed = _make_feed([entry_no_enc, entry_with_enc])

    with patch("feedparser.parse", return_value=feed), \
         patch("skipcastify.services.generate_feeds.process_artwork", return_value=None):
        path = fm.generate_feed("https://example.com/feed.xml")

    content = Path(path).read_text()
    assert "Bonus Post" in content
    assert "Normal Episode" in content
