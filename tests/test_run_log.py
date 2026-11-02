import json

import pytest

import pipeline
from skipcastify.services import analytics
from skipcastify.services.download_episode import EpisodeDownloader
from skipcastify.services.run_log import KEEP_RUNS, RunRecord, append_run, recent_runs


def test_recent_runs_newest_first_and_skips_junk(tmp_path):
    for i in range(3):
        append_run(tmp_path, {"started": str(i)})
    with open(tmp_path / "metrics" / "runs.jsonl", "a") as f:
        f.write("not json\n")
    assert [r["started"] for r in recent_runs(tmp_path, 10)] == ["2", "1", "0"]
    assert [r["started"] for r in recent_runs(tmp_path, 2)] == ["2"]


def test_recent_runs_without_a_file(tmp_path):
    assert recent_runs(tmp_path) == []


def test_log_is_trimmed(tmp_path, monkeypatch):
    monkeypatch.setattr("skipcastify.services.run_log.KEEP_RUNS", 10)
    for i in range(40):
        append_run(tmp_path, {"started": str(i), "pad": "x" * 600})
    lines = (tmp_path / "metrics" / "runs.jsonl").read_text().splitlines()
    assert len(lines) <= 20 and json.loads(lines[-1])["started"] == "39"


def test_run_record_shape():
    r = RunRecord()
    r.feeds = [{"feed": "a", "status": "up_to_date"}]
    d = r.to_dict("failed", "publish", "ValueError")
    assert d["outcome"] == "failed" and d["stage"] == "publish" and d["error"] == "ValueError"
    assert "+" in d["started"] or "-" in d["started"][10:]  # timezone-aware


class _Feed:
    def __init__(self, title, entries):
        self.feed = type("F", (), {"title": title})()
        self.entries = entries


def _entry(i):
    return {"id": f"e{i}", "title": f"Ep {i}", "enclosures": [{"href": f"https://x/{i}.mp3"}]}


class _E(dict):
    __getattr__ = dict.get


@pytest.fixture
def downloader(tmp_path, monkeypatch):
    opml = tmp_path / "subs.opml"
    opml.write_text('<opml><body><outline type="rss" xmlnsurl="" xmlUrl="https://feed.example/a.rss"/></body></opml>')
    d = EpisodeDownloader(str(opml), str(tmp_path))
    d.subscription_urls = ["https://feed.example/a.rss"]
    return d


def test_download_latest_reports_per_feed_status(downloader, monkeypatch):
    entries = [_E(_entry(i)) for i in range(3)]
    monkeypatch.setattr("skipcastify.services.download_episode.feedparser.parse",
                        lambda url: _Feed("My Show", entries))
    assert downloader.download_latest() == [{"feed": "my-show", "status": "baselined"}]
    assert downloader.download_latest() == [{"feed": "my-show", "status": "up_to_date"}]

    entries.insert(0, _E(_entry(9)))
    monkeypatch.setattr(downloader, "download_episode", lambda e, t: None)
    assert downloader.download_latest() == [
        {"feed": "my-show", "status": "downloaded", "downloaded": 0, "failed": 1}]
    monkeypatch.setattr(downloader, "download_episode", lambda e, t: "path")
    assert downloader.download_latest() == [
        {"feed": "my-show", "status": "downloaded", "downloaded": 1, "failed": 0}]


def test_download_latest_reports_errors_without_the_url(downloader, monkeypatch):
    def boom(url):
        raise ConnectionError(url)
    monkeypatch.setattr("skipcastify.services.download_episode.feedparser.parse", boom)
    assert downloader.download_latest() == [
        {"feed": None, "status": "error", "error": "ConnectionError"}]


def _main_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SUBSCRIPTIONS", str(tmp_path / "none.opml"))
    monkeypatch.setattr(pipeline, "load_dotenv", lambda: None)
    monkeypatch.setattr(pipeline, "setup_logging", lambda d: None)


def test_main_records_ok_and_failed_runs(tmp_path, monkeypatch):
    _main_env(tmp_path, monkeypatch)

    class Fake:
        def __init__(self, *a, run, **k):
            self.run_record, self.stage, self.alerted = run, "download", True

        def run(self):
            self.run_record.feeds = [{"feed": "a", "status": "up_to_date"}]

    monkeypatch.setattr(pipeline, "Pipeline", Fake)
    assert pipeline.main() == 0

    def fail(self):
        self.stage = "publish"
        raise ValueError("https://secret.example/feed?token=abc")
    monkeypatch.setattr(Fake, "run", fail)
    assert pipeline.main() == 1

    failed, ok = recent_runs(tmp_path)
    assert ok["outcome"] == "ok" and ok["feeds"] == [{"feed": "a", "status": "up_to_date"}]
    assert failed["outcome"] == "failed" and failed["stage"] == "publish"
    assert failed["error"] == "ValueError" and "secret" not in json.dumps(failed)


def test_feed_status_uses_latest_check_and_last_download():
    runs = [  # newest first
        {"started": "t3", "feeds": [{"feed": "a", "status": "up_to_date"}]},
        {"started": "t2", "feeds": [{"feed": "a", "status": "downloaded", "downloaded": 1}]},
        {"started": "t1", "feeds": [{"feed": "a", "status": "downloaded", "downloaded": 2},
                                    {"feed": None, "status": "error"}]},
    ]
    assert analytics.feed_status(runs) == [
        {"feed_slug": "a", "status": "up_to_date", "checked": "t3", "last_download": "t2"}]


def test_payload_includes_runs_and_recent_episodes(tmp_path):
    (tmp_path / "metrics").mkdir()
    (tmp_path / "metrics" / "events.jsonl").write_text(json.dumps({
        "ts": "2026-11-01T14:41:12.5", "event": "episode_complete", "feed_slug": "s",
        "episode_name": "s-ep", "original_duration_s": 600,
        "spans": [{"start_ms": 0, "end_ms": 60000, "label": "ADVERTISEMENT"}]}) + "\n")
    append_run(tmp_path, {"started": "x", "feeds": [], "processed": [], "outcome": "ok"})
    p = analytics.build_payload(str(tmp_path))
    assert p["runs"][0]["started"] == "x"
    (ep,) = p["recent_episodes"]
    assert ep["ad_minutes"] == 1.0 and ep["ad_pct"] == 10.0 and ep["ts"].startswith("2026-11-01")


def test_daily_series_fills_idle_days_through_today():
    from datetime import date, timedelta
    today = date.today()
    first = today - timedelta(days=3)
    ev = {"event": "episode_complete", "feed_slug": "s", "spans": []}
    rows = analytics.daily_series([{**ev, "ts": first.isoformat() + "T10:00:00"}])
    assert [r["date"] for r in rows] == [(first + timedelta(days=i)).isoformat() for i in range(4)]
    assert [r["episodes"] for r in rows] == [1, 0, 0, 0]
    assert analytics.daily_series([]) == []


def test_payload_days_filters_episode_stats_but_not_runs(tmp_path):
    from datetime import date, timedelta
    old = (date.today() - timedelta(days=40)).isoformat() + "T10:00:00"
    new = date.today().isoformat() + "T10:00:00"
    (tmp_path / "metrics").mkdir()
    (tmp_path / "metrics" / "events.jsonl").write_text("".join(
        json.dumps({"ts": ts, "event": "episode_complete", "feed_slug": "s",
                    "episode_name": "e", "spans": []}) + "\n" for ts in (old, new)))
    append_run(tmp_path, {"started": "x", "feeds": [], "processed": [], "outcome": "ok"})

    p = analytics.build_payload(str(tmp_path), days=7)
    assert p["overview"]["episodes_processed"] == 1
    assert len(p["daily"]) == 7 and p["daily"][-1]["episodes"] == 1
    assert len(p["runs"]) == 1
    assert analytics.build_payload(str(tmp_path))["overview"]["episodes_processed"] == 2


def test_payload_days_with_no_recent_episodes_still_has_a_daily_axis(tmp_path):
    p = analytics.build_payload(str(tmp_path), days=7)
    assert [r["episodes"] for r in p["daily"]] == [0] * 7
