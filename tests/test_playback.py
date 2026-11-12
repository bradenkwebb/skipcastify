"""Where the app plays an episode from: the enclosure our published feed lists."""

import json

from skipcastify.services.playback import playback
from skipcastify.services.publish_manifest import PublishManifest
from skipcastify.services.state_manager import uncut_marker
from tests.test_feeds_page import AUTH, SECRET, client, feed_key  # noqa: F401

BASE = "https://example.com"


def write_feed(tmp_path, slug, items):
    """A generated R2 feed: (title, enclosure url) per item, and its manifest entry."""
    d = tmp_path / "feeds_r2"
    d.mkdir(parents=True, exist_ok=True)
    body = "".join(f'<item><title>{t}</title><enclosure url="{u}" type="audio/mpeg"/></item>' for t, u in items)
    (d / f"{slug}.xml").write_text(
        f'<?xml version="1.0"?><rss version="2.0"><channel><title>Show</title>{body}</channel></rss>')
    m = PublishManifest(str(tmp_path))
    m.record_put(feed_key(slug), 2048, "feed")
    m.save()


def test_a_processed_episode_plays_our_ad_free_copy(tmp_path):
    ours = f"{BASE}/audio/{SECRET}/odd-lots/odd-lots-the_big_one.mp3"
    write_feed(tmp_path, "odd-lots", [("The Big One", ours)])
    p = playback(str(tmp_path), BASE, "odd-lots", "odd-lots-the_big_one")
    assert p == {"url": ours, "feed_url": f"{BASE}/{feed_key('odd-lots')}", "ad_free": True, "uncut": False}


def test_an_uncut_episode_plays_the_original(tmp_path):
    original = "https://cdn.example.org/ep.mp3"
    write_feed(tmp_path, "odd-lots", [("Quiet One", original)])
    processed = tmp_path / "podcasts" / "processed" / "odd-lots" / "odd-lots-quiet_one.mp3"
    processed.parent.mkdir(parents=True)
    processed.write_bytes(b"")
    uncut_marker(processed).touch()
    p = playback(str(tmp_path), BASE, "odd-lots", "odd-lots-quiet_one")
    assert p["url"] == original and not p["ad_free"] and p["uncut"]


def test_nothing_to_play_without_a_published_feed_or_item(tmp_path):
    assert playback(str(tmp_path), BASE, "odd-lots", "odd-lots-x") is None  # not published
    write_feed(tmp_path, "odd-lots", [("Other", "https://cdn.example.org/o.mp3")])
    assert playback(str(tmp_path), BASE, "odd-lots", "odd-lots-x") is None  # not in the feed
    assert playback(str(tmp_path), BASE, None, "odd-lots-x") is None


def test_episode_json_carries_playback_and_is_never_cached(client, tmp_path):  # noqa: F811
    c, tmp_path = client
    name = "odd-lots-the_big_one"
    events = tmp_path / "metrics"
    events.mkdir()
    (events / "events.jsonl").write_text(json.dumps({
        "event": "episode_complete", "ts": "2026-11-01T10:00:00", "feed_slug": "odd-lots",
        "episode_name": name, "original_duration_s": 600, "spans": []}) + "\n")
    ours = f"{BASE}/audio/{SECRET}/odd-lots/{name}.mp3"
    write_feed(tmp_path, "odd-lots", [("The Big One", ours)])

    r = c.get(f"/api/episode/{name}.json", headers=AUTH)
    assert r.status_code == 200
    assert r.headers["Cache-Control"] == "no-store"
    assert r.get_json()["playback"]["url"] == ours
