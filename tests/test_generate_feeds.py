"""
Tests for feed generation robustness across different podcast feed structures.
All tests use mocked feedparser data — no network calls.
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from skipcastify.services.generate_feeds import FeedManager


def _make_entry(title="Ep 1", published_parsed=(2025, 1, 1, 12, 0, 0, 0, 0, 0), enclosures=None):
    """Build a minimal mock feedparser entry."""
    entry = MagicMock()
    entry.title = title
    entry.published_parsed = published_parsed
    entry.link = "https://example.com/ep"
    entry.get.side_effect = lambda k, default=None: {"summary": "desc"}.get(k, default)
    entry.enclosures = enclosures if enclosures is not None else [{"length": "12345"}]
    return entry


def _make_feed(entries, image_href="https://example.com/art.jpg"):
    """Build a minimal mock feedparser feed."""
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


def test_feed_without_artwork_falls_back_to_original_url(fm, tmp_path):
    """Feeds with no artwork must still generate successfully and use the original URL."""
    feed = _make_feed([_make_entry()], image_href=None)

    with patch("feedparser.parse", return_value=feed), \
         patch("skipcastify.services.generate_feeds.process_artwork", return_value=None):
        path = fm.generate_feed("https://example.com/feed.xml")

    assert Path(path).exists()
    # The feed XML should not reference our local server for artwork
    content = Path(path).read_text()
    assert "localhost:5000/artwork" not in content


def test_feed_entries_without_pubdate_are_included(fm, tmp_path):
    """Entries with no publish date must be included in the feed — no crash, no silent drop."""
    entry = _make_entry(published_parsed=None)
    feed = _make_feed([entry])

    with patch("feedparser.parse", return_value=feed), \
         patch("skipcastify.services.generate_feeds.process_artwork", return_value=None):
        path = fm.generate_feed("https://example.com/feed.xml")

    content = Path(path).read_text()
    assert "Ep 1" in content


def test_feed_entries_without_enclosure_are_included(fm, tmp_path):
    """Entries with no audio enclosure must not crash feed generation.
    This covers podcasts that publish non-audio entries (e.g. bonus posts)."""
    entry_no_enc = _make_entry(title="Bonus Post", enclosures=[])
    entry_with_enc = _make_entry(title="Normal Episode")
    feed = _make_feed([entry_no_enc, entry_with_enc])

    with patch("feedparser.parse", return_value=feed), \
         patch("skipcastify.services.generate_feeds.process_artwork", return_value=None):
        path = fm.generate_feed("https://example.com/feed.xml")

    content = Path(path).read_text()
    assert "Bonus Post" in content
    assert "Normal Episode" in content
