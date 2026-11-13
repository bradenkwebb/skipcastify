"""A retitled episode keeps its processed audio: files are found by guid, not by the current title."""

import xml.etree.ElementTree as ET
from unittest.mock import MagicMock, patch

from skipcastify.services import episode_files, labeling, playback
from skipcastify.services.download_episode import EpisodeDownloader
from skipcastify.services.generate_feeds import FeedManager
from tests.test_generate_feeds import _make_entry, _make_feed

SLUG = "test-podcast"
GUID = "guid-pt1"


def entry(title, guid=GUID):
    return _make_entry(title=title, extra={"id": guid})


def build(tmp_path, *entries):
    fm = FeedManager("http://localhost:5000", str(tmp_path), episode_token="tok")
    with patch("feedparser.parse", return_value=_make_feed(list(entries))), \
         patch("skipcastify.services.generate_feeds.process_artwork", return_value=None):
        return ET.parse(fm.generate_feed("https://example.com/feed.xml")).getroot()


def processed(tmp_path, name):
    d = tmp_path / "podcasts" / "processed" / SLUG
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_bytes(b"audio")


def enclosure(root):
    return root.find(".//item/enclosure").get("url")


def test_a_retitled_episode_keeps_its_processed_audio(tmp_path):
    processed(tmp_path, f"{SLUG}-pt_1_ep_2226.mp3")
    build(tmp_path, entry("Pt. 1 | Ep. 2226"))           # records the guid for the existing file
    root = build(tmp_path, entry("Pt. 1 | Ep. 2227"))    # the publisher renumbers it
    assert enclosure(root).endswith(f"/{SLUG}-pt_1_ep_2226.mp3")
    assert root.find(".//item/title").text == "Pt. 1 | Ep. 2227"


def test_without_a_record_the_title_still_works(tmp_path):
    processed(tmp_path, f"{SLUG}-ep_1.mp3")
    assert enclosure(build(tmp_path, entry("Ep 1"))).endswith(f"/{SLUG}-ep_1.mp3")


def test_the_first_name_recorded_wins(tmp_path):
    files = {}
    assert episode_files.remember(files, SLUG, GUID, "a.mp3")
    assert not episode_files.remember(files, SLUG, GUID, "b.mp3")
    assert not episode_files.remember(files, SLUG, None, "c.mp3")
    assert files == {SLUG: {GUID: "a.mp3"}}


def test_the_downloader_records_what_it_saved(tmp_path):
    dl = EpisodeDownloader.__new__(EpisodeDownloader)
    dl.data_dir = str(tmp_path)
    e = MagicMock()
    e.title = "Ep 1"
    e.enclosures = [{"href": "https://cdn.example.com/ep.mp3"}]
    e.get.side_effect = lambda k, d=None: {"id": GUID}.get(k, d)
    response = MagicMock()
    response.iter_content.return_value = [b"audio"]
    with patch("requests.get") as get:
        get.return_value.__enter__.return_value = response
        dl.download_episode(e, "Test Podcast")
    assert episode_files.load(tmp_path) == {SLUG: {GUID: f"{SLUG}-ep_1.mp3"}}


def test_dashboard_titles_and_playback_follow_the_guid(tmp_path, monkeypatch):
    processed(tmp_path, f"{SLUG}-ep_1.mp3")
    episode_files.save(tmp_path, {SLUG: {GUID: f"{SLUG}-ep_1.mp3"}})
    build(tmp_path, entry("Ep 1 (renamed)"))
    labeling._release_cache.clear()
    assert labeling.episode_titles(tmp_path)[f"{SLUG}-ep_1"] == "Ep 1 (renamed)"
    feed = tmp_path / "feeds" / f"{SLUG}.xml"
    assert playback._enclosures(tmp_path, feed, SLUG)[f"{SLUG}-ep_1"].endswith(f"/{SLUG}-ep_1.mp3")
