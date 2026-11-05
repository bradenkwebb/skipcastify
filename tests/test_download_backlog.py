"""Tests for deciding what counts as a new episode.

The bug these exist for: on 2026-11-01 the downloader read a four-month
outage as a feed migration, marked 290 Global News episodes as seen and
downloaded none of them. Nothing failed and nothing alerted — the backlog
was dropped on the strength of a log line.
"""

import calendar
import json
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from skipcastify.services.download_episode import (
    CATCH_UP_WINDOW_DAYS,
    EpisodeDownloader,
)


# A fixed clock, so "how old is this episode" is deterministic.
NOW = calendar.timegm((2026, 11, 5, 12, 0, 0, 0, 0, 0))


class FakeEntry:
    """A feed entry, published `days_ago` before NOW (today by default).

    days_ago=None models a feed that publishes no parseable date at all.
    """

    def __init__(self, eid, title=None, days_ago=0.0):
        self.id = eid
        self.title = title or eid
        self.enclosures = [{"href": f"https://cdn.test/{eid}.mp3", "length": "1"}]
        self.days_ago = days_ago

    def get(self, key, default=None):
        if key in ("published_parsed", "updated_parsed"):
            if self.days_ago is None:
                return default
            return time.gmtime(NOW - self.days_ago * 86400)
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


def run(downloader, entries, now=NOW):
    with patch("feedparser.parse", return_value=fake_feed(entries)):
        return downloader.download_latest(now=now)


def seen_ids(tmp_path, url=FEED_URL):
    return set(json.loads((tmp_path / "seen_episodes.json").read_text())[url])


# --- the incident ---------------------------------------------------------

def test_a_long_outage_is_not_mistaken_for_a_feed_migration(downloader, tmp_path, alerts):
    """The exact 2026-11-01 shape: most of the feed unseen, older part seen."""
    entries = [FakeEntry(f"ep{i:03d}") for i in range(290)]
    seed_seen(tmp_path, {f"ep{i:03d}" for i in range(116, 290)})   # 116 unseen

    result = run(downloader, entries)

    assert result[0]["status"] == "downloaded"
    assert len(downloader.downloaded) == 5
    assert not any("ids changed" in a["title"].lower() for a in alerts.sent)


def test_nothing_recent_is_marked_seen_without_being_downloaded(downloader, tmp_path):
    """The actual harm: ids marked seen that we never fetched are gone for good.

    Inside the catch-up window, the only ids that may be added are downloads.
    """
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


# --- one bad entry must not stop everything ------------------------------

class UnidentifiableEntry:
    """A text-only post: no guid, no enclosure, so no stable id exists."""

    def __init__(self, title="A blog post"):
        self.title = title
        self.enclosures = []

    def get(self, key, default=None):
        return default


def test_an_entry_with_no_id_is_skipped_not_fatal(downloader, tmp_path):
    """It used to escape download_latest and take the whole stage with it, so
    one malformed entry in one feed stopped every feed, on every run."""
    entries = [FakeEntry("ep0"), UnidentifiableEntry(), FakeEntry("ep1")]
    seed_seen(tmp_path, set())

    result = run(downloader, entries)

    assert sorted(downloader.downloaded) == ["ep0", "ep1"]
    assert result[0]["status"] == "downloaded"


def test_unidentifiable_entries_do_not_trigger_a_re_baseline(downloader, tmp_path, alerts):
    """They are never recognised, so counting them would make a healthy feed
    of mostly text posts look like an id migration."""
    entries = [FakeEntry(f"ep{i}") for i in range(8)] + \
              [UnidentifiableEntry(f"post {i}") for i in range(20)]
    seed_seen(tmp_path, {f"ep{i}" for i in range(8)})

    result = run(downloader, entries)

    assert result[0]["status"] == "up_to_date"
    assert alerts.sent == []


# --- the catch-up window --------------------------------------------------
#
# Braden's call on 2026-11-05: after downtime, catch up on the last week only.
# A four-month backlog is not worth draining — he will not listen to it.

def _outage_feed():
    """The 2026-11-01 shape, dated: 290 entries, ~2 a day, newest first.

    ep000..ep115 are unseen (the outage), ep116..ep289 already seen. Of the
    unseen, ep000..ep013 fall inside the 7-day window and ep014..ep115 do not.
    """
    return [FakeEntry(f"ep{i:03d}", days_ago=i * 0.5 + 0.1) for i in range(290)]


def test_only_the_last_week_of_a_backlog_is_downloaded(downloader, tmp_path):
    entries = _outage_feed()
    seed_seen(tmp_path, {f"ep{i:03d}" for i in range(116, 290)})

    result = run(downloader, entries)

    assert result[0]["status"] == "downloaded"
    # Newest first, episode_limit per run.
    assert downloader.downloaded == ["ep000", "ep001", "ep002", "ep003", "ep004"]


def test_the_rest_of_the_backlog_is_marked_seen(downloader, tmp_path):
    """Older-than-a-week unseen entries are retired, not left to come back."""
    entries = _outage_feed()
    already = {f"ep{i:03d}" for i in range(116, 290)}
    seed_seen(tmp_path, already)

    run(downloader, entries)

    newly_seen = seen_ids(tmp_path) - already
    # The 5 downloaded, plus the 102 unseen entries older than the window.
    assert newly_seen == set(downloader.downloaded) | {f"ep{i:03d}" for i in range(14, 116)}


def test_the_week_old_backlog_drains_and_then_stops(downloader, tmp_path):
    """Successive runs finish the week and never touch the older episodes."""
    entries = _outage_feed()
    seed_seen(tmp_path, {f"ep{i:03d}" for i in range(116, 290)})

    for _ in range(6):
        run(downloader, entries)

    assert downloader.downloaded == [f"ep{i:03d}" for i in range(14)]


def test_skipped_episodes_are_alerted_once_with_a_count(downloader, tmp_path, alerts):
    entries = _outage_feed()
    seed_seen(tmp_path, {f"ep{i:03d}" for i in range(116, 290)})

    run(downloader, entries)

    skipped = [a for a in alerts.sent if "old episodes skipped" in a["title"].lower()]
    assert len(skipped) == 1
    assert "102" in skipped[0]["message"]
    assert "Test Podcast" in skipped[0]["message"]


def test_the_skipped_alert_is_throttled(downloader, tmp_path, alerts):
    """Every run re-detects nothing new, so the alert must not repeat."""
    entries = _outage_feed()
    seed_seen(tmp_path, {f"ep{i:03d}" for i in range(116, 290)})

    run(downloader, entries)
    before = len(alerts.sent)
    entries2 = [FakeEntry(f"new{i}", days_ago=100) for i in range(20)] + entries
    run(downloader, entries2)

    skipped = [a for a in alerts.sent if "old episodes skipped" in a["title"].lower()]
    assert len(skipped) == 1, "the skipped-episode alert fired twice"
    assert len(alerts.sent) >= before


def test_an_undated_feed_cannot_trigger_a_catch_up(downloader, tmp_path, alerts):
    """No parseable date counts as old: a dateless feed is not an excuse to
    download its whole history."""
    entries = [FakeEntry(f"ep{i:02d}", days_ago=None) for i in range(40)]
    seed_seen(tmp_path, {"ep39"})

    result = run(downloader, entries)

    assert result[0]["status"] == "skipped_old"
    assert downloader.downloaded == []
    assert seen_ids(tmp_path) >= {e.id for e in entries}
    assert any("old episodes skipped" in a["title"].lower() for a in alerts.sent)


def test_a_normal_new_episode_is_still_downloaded(downloader, tmp_path, alerts):
    """The window must not get in the way of the ordinary 15-minute run."""
    entries = [FakeEntry("ep-new", days_ago=0.2)] + \
              [FakeEntry(f"ep{i:02d}", days_ago=i + 1) for i in range(20)]
    seed_seen(tmp_path, {f"ep{i:02d}" for i in range(20)})

    result = run(downloader, entries)

    assert downloader.downloaded == ["ep-new"]
    assert result[0]["status"] == "downloaded"
    assert alerts.sent == []


def test_the_window_boundary(downloader, tmp_path):
    """Just inside the window is downloaded; just outside is retired."""
    entries = [FakeEntry("inside", days_ago=CATCH_UP_WINDOW_DAYS - 0.05),
               FakeEntry("outside", days_ago=CATCH_UP_WINDOW_DAYS + 0.05)]
    seed_seen(tmp_path, {"anchor"})
    entries.append(FakeEntry("anchor", days_ago=30))

    run(downloader, entries)

    assert downloader.downloaded == ["inside"]
    assert "outside" in seen_ids(tmp_path)
