"""Tests for the pipeline's catch-all failure alert and recovery alert.

No network: the pipeline's collaborators are replaced after construction, and
the alerter's notifier is a recording fake.
"""

import time

import pytest

from pipeline import Pipeline
from skipcastify.utils.alerts import Alerter


@pytest.fixture
def sent():
    out = []

    def notifier(title, message, priority="default"):
        out.append({"title": title, "message": message, "priority": priority})
        return True

    notifier.sent = out
    return notifier


@pytest.fixture
def opml(tmp_path):
    path = tmp_path / "subs.opml"
    path.write_text(
        '<opml version="1.0"><body>'
        '<outline type="rss" xmlUrl="https://example.com/feed.xml"/>'
        '</body></opml>')
    return str(path)


def make_pipeline(tmp_path, opml, sent, fail_stage=None, exc=None):
    """A pipeline whose real work is stubbed out, optionally failing once."""
    alerter = Alerter(str(tmp_path), notifier=sent)
    pipeline = Pipeline(opml, str(tmp_path), alerter=alerter)

    exc = exc or RuntimeError("boom")

    def stub(name):
        def call(*args, **kwargs):
            if fail_stage == name:
                raise exc
        return call

    pipeline.downloader.download_latest = stub("download")
    pipeline._cleanup_old_episodes = stub("cleanup")
    pipeline.feed_manager.generate_feed = stub("feeds")
    pipeline._publish_to_r2 = stub("publish")
    pipeline.publish_to_r2 = True
    return pipeline


# --- failure alerts -------------------------------------------------------

def test_a_clean_run_sends_nothing(tmp_path, opml, sent):
    make_pipeline(tmp_path, opml, sent).run()

    assert sent.sent == []


def test_an_unhandled_failure_alerts_and_still_raises(tmp_path, opml, sent):
    """The exit code is what systemd sees, so the raise must survive."""
    pipeline = make_pipeline(tmp_path, opml, sent, fail_stage="download")

    with pytest.raises(RuntimeError):
        pipeline.run()

    assert len(sent.sent) == 1
    assert sent.sent[0]["priority"] == "high"
    assert "RuntimeError" in sent.sent[0]["message"]


def test_the_alert_names_the_failing_stage(tmp_path, opml, sent):
    pipeline = make_pipeline(tmp_path, opml, sent, fail_stage="publish")

    with pytest.raises(RuntimeError):
        pipeline.run()

    assert "publish" in sent.sent[0]["title"]
    assert pipeline.stage == "publish"


def test_a_feed_level_error_is_not_a_pipeline_failure(tmp_path, opml, sent):
    """generate_feed already degrades per feed; that must stay non-fatal."""
    pipeline = make_pipeline(tmp_path, opml, sent, fail_stage="feeds")

    pipeline.run()                      # no raise

    assert sent.sent == []


def test_the_same_failure_every_15_minutes_alerts_once(tmp_path, opml, sent):
    """96 runs a day must not mean 96 pushes for one broken thing."""
    for _ in range(5):
        pipeline = make_pipeline(tmp_path, opml, sent, fail_stage="download")
        with pytest.raises(RuntimeError):
            pipeline.run()

    assert len(sent.sent) == 1


def test_a_different_failure_alerts_separately(tmp_path, opml, sent):
    for stage in ("download", "cleanup"):
        pipeline = make_pipeline(tmp_path, opml, sent, fail_stage=stage)
        with pytest.raises(RuntimeError):
            pipeline.run()

    assert len(sent.sent) == 2
    assert {"download", "cleanup"} == {s["title"].split(": ")[1] for s in sent.sent}


def test_alert_text_is_scrubbed(tmp_path, opml, sent, monkeypatch):
    """An exception message routinely carries the URL that failed."""
    monkeypatch.setenv("EPISODE_TOKEN", "leaked-token-value-here")
    exc = RuntimeError("404 for https://host/x?token=abc123def "
                       "and /episodes/leaked-token-value-here/a.mp3")
    pipeline = make_pipeline(tmp_path, opml, sent, fail_stage="download", exc=exc)

    with pytest.raises(RuntimeError):
        pipeline.run()

    message = sent.sent[0]["message"]
    assert "abc123def" not in message
    assert "leaked-token-value-here" not in message


# --- recovery -------------------------------------------------------------

def test_the_first_clean_run_after_a_failure_says_so(tmp_path, opml, sent):
    pipeline = make_pipeline(tmp_path, opml, sent, fail_stage="download")
    with pytest.raises(RuntimeError):
        pipeline.run()

    make_pipeline(tmp_path, opml, sent).run()

    assert len(sent.sent) == 2
    assert "recovered" in sent.sent[1]["title"].lower()


def test_only_the_first_clean_run_says_so(tmp_path, opml, sent):
    pipeline = make_pipeline(tmp_path, opml, sent, fail_stage="download")
    with pytest.raises(RuntimeError):
        pipeline.run()
    make_pipeline(tmp_path, opml, sent).run()
    make_pipeline(tmp_path, opml, sent).run()
    make_pipeline(tmp_path, opml, sent).run()

    assert len(sent.sent) == 2


def test_a_recurrence_after_recovery_alerts_again(tmp_path, opml, sent):
    """Otherwise the 6h window from the first episode silences the second."""
    for fail in (True, False, True):
        pipeline = make_pipeline(tmp_path, opml, sent,
                                 fail_stage="download" if fail else None)
        if fail:
            with pytest.raises(RuntimeError):
                pipeline.run()
        else:
            pipeline.run()

    titles = [s["title"] for s in sent.sent]
    assert titles.count("Pipeline failed: download") == 2
