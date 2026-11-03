"""Tests for deciding what counts as a new episode.

The bug these exist for: on 2026-11-01 the downloader read a four-month
outage as a feed migration, marked 290 Global News episodes as seen and
downloaded none of them. Nothing failed and nothing alerted — the backlog
was dropped on the strength of a log line.
"""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from skipcastify.services.download_episode import EpisodeDownloader


class FakeEntry:
    def __init__(self, eid, title=None):
        self.id = eid
        self.title = title or eid
        self.enclosures = [{"href": f"https://cdn.test/{eid}.mp3", "length": "1"}]

    def get(self, key, default=None):
        return {"id": self.id}.get(key, default)


def fake_feed(entries, title="Test Podcast"):
    return SimpleNamespace(feed=SimpleNamespace(title=title), entries=entries)


@pytest.fixture
def alerts():
    sent = []

    def notifier(title, message, priority="default"):
        sent.append({"title": title, "message": message, "priority": priority})
        return True

    notifier.sent = sent
    return notifier


FEED_URL = "https://feed.test/rss"


@pytest.fixture
def downloader(tmp_path, alerts):
    from skipcastify.utils.alerts import Alerter
    opml = tmp_path / "subs.opml"
    opml.write_text('<opml version="1.0"><body>'
                    f'<outline type="rss" xmlUrl="{FEED_URL}"/>'
                    '</body></opml>')
    d = EpisodeDownloader(str(opml), str(tmp_path), episode_limit=5,
                          alerter=Alerter(str(tmp_path), notifier=alerts))
    d.downloaded = []

    def fake_download(entry, podcast_title):
        if entry.id in getattr(d, "fail_ids", set()):
            return None
        d.downloaded.append(entry.id)
        return f"/tmp/{entry.id}.mp3"

    d.download_episode = fake_download
    return d


def seed_seen(tmp_path, ids, url=FEED_URL):
    (tmp_path / "seen_episodes.json").write_text(json.dumps({url: sorted(ids)}))


def run(downloader, entries):
    with patch("feedparser.parse", return_value=fake_feed(entries)):
        return downloader.download_latest()


def seen_ids(tmp_path, url=FEED_URL):
    return set(json.loads((tmp_path / "seen_episodes.json").read_text())[url])


# --- the incident ---------------------------------------------------------

def test_a_long_outage_is_not_mistaken_for_a_feed_migration(downloader, tmp_path, alerts):
    """The exact 2026-11-01 shape: most of the feed unseen, older part seen."""
    entries = [FakeEntry(f"ep{i:03d}") for i in range(290)]
    seed_seen(tmp_path, {f"ep{i:03d}" for i in range(116, 290)})   # 174 unseen

    result = run(downloader, entries)

    assert result[0]["status"] == "downloaded"
    assert len(downloader.downloaded) == 5
    assert not any("ids changed" in a["title"].lower() for a in alerts.sent)


def test_nothing_is_marked_seen_without_being_downloaded(downloader, tmp_path):
    """The actual harm: ids marked seen that we never fetched are gone for good."""
    entries = [FakeEntry(f"ep{i:03d}") for i in range(290)]
    already = {f"ep{i:03d}" for i in range(116, 290)}
    seed_seen(tmp_path, already)

    run(downloader, entries)

    newly_seen = seen_ids(tmp_path) - already
    assert newly_seen == set(downloader.downloaded)


def test_a_backlog_drains_over_successive_runs(downloader, tmp_path):
    """No episode may be skipped; later runs must pick up the remainder."""
    entries = [FakeEntry(f"ep{i:02d}") for i in range(23)]
    seed_seen(tmp_path, set())

    for _ in range(6):
        run(downloader, entries)

    assert sorted(downloader.downloaded) == sorted(e.id for e in entries)


def test_a_large_backlog_is_reported(downloader, tmp_path, alerts):
    entries = [FakeEntry(f"ep{i:02d}") for i in range(40)]
    seed_seen(tmp_path, set())

    run(downloader, entries)

    assert any("backlog" in a["title"].lower() for a in alerts.sent)


def test_an_ordinary_run_is_quiet(downloader, tmp_path, alerts):
    entries = [FakeEntry(f"ep{i:02d}") for i in range(12)]
    seed_seen(tmp_path, {f"ep{i:02d}" for i in range(2, 12)})       # 2 new

    result = run(downloader, entries)

    assert len(downloader.downloaded) == 2
    assert result[0]["status"] == "downloaded"
    assert alerts.sent == []


# --- a genuine id migration ----------------------------------------------

def test_a_real_id_migration_still_re_baselines(downloader, tmp_path):
    """Every id rewritten: the seen set stops matching the feed entirely."""
    entries = [FakeEntry(f"libsyn-{i}") for i in range(40)]
    seed_seen(tmp_path, {f"art19-{i}" for i in range(40)})

    result = run(downloader, entries)

    assert result[0]["status"] == "rebaselined"
    assert downloader.downloaded == []
    assert seen_ids(tmp_path) >= {e.id for e in entries}


def test_a_re_baseline_is_alerted_loudly(downloader, tmp_path, alerts):
    """It discards everything the feed offers, so it must never be silent
    again — that silence is what hid the 2026-11-01 drop for two days."""
    entries = [FakeEntry(f"libsyn-{i}") for i in range(40)]
    seed_seen(tmp_path, {f"art19-{i}" for i in range(40)})

    run(downloader, entries)

    assert len(alerts.sent) == 1
    assert alerts.sent[0]["priority"] == "high"
    assert "Test Podcast" in alerts.sent[0]["message"]


def test_a_tiny_feed_is_not_re_baselined(downloader, tmp_path):
    """Below episode_limit there is no backlog risk worth the guard, and a
    new short feed would otherwise never download anything."""
    entries = [FakeEntry(f"new-{i}") for i in range(3)]
    seed_seen(tmp_path, {"old-1", "old-2"})

    run(downloader, entries)

    assert sorted(downloader.downloaded) == ["new-0", "new-1", "new-2"]


# --- unchanged behaviour --------------------------------------------------

def test_a_brand_new_feed_is_baselined_and_downloads_nothing(downloader, tmp_path):
    """Start fresh: a feed we have never seen gets its history marked seen."""
    entries = [FakeEntry(f"ep{i}") for i in range(30)]

    result = run(downloader, entries)

    assert result[0]["status"] == "baselined"
    assert downloader.downloaded == []
    assert len(seen_ids(tmp_path)) == 30


def test_a_failed_download_is_retried_next_run(downloader, tmp_path):
    entries = [FakeEntry("ep0"), FakeEntry("ep1")]
    seed_seen(tmp_path, set())
    downloader.fail_ids = {"ep0"}

    run(downloader, entries)
    assert "ep0" not in seen_ids(tmp_path)

    downloader.fail_ids = set()
    run(downloader, entries)

    assert "ep0" in downloader.downloaded


def test_up_to_date_when_nothing_is_new(downloader, tmp_path):
    entries = [FakeEntry(f"ep{i}") for i in range(10)]
    seed_seen(tmp_path, {e.id for e in entries})

    result = run(downloader, entries)

    assert result[0]["status"] == "up_to_date"
    assert downloader.downloaded == []
