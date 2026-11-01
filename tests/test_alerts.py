"""Tests for throttled alerting and secret scrubbing.

No network: Alerter takes its notifier as a collaborator.
"""

import json
import time

import pytest

from skipcastify.utils.alerts import Alerter, scrub_secrets


@pytest.fixture
def sent():
    out = []

    def notifier(title, message, priority="default"):
        out.append({"title": title, "message": message, "priority": priority})
        return True

    notifier.sent = out
    return notifier


@pytest.fixture
def alerter(tmp_path, sent):
    return Alerter(str(tmp_path), notifier=sent)


# --- throttling -----------------------------------------------------------

def test_first_alert_is_sent(alerter, sent):
    assert alerter.send("fp", "Title", "body") is True
    assert sent.sent[0]["title"] == "Title"


def test_repeat_inside_the_window_is_suppressed(alerter, sent):
    """The timer runs every 15 minutes; one broken thing is one alert."""
    now = time.time()
    alerter.send("fp", "Title", "body", now=now)
    for i in range(1, 20):
        alerter.send("fp", "Title", "body", now=now + i * 900)

    assert len(sent.sent) == 1


def test_alert_fires_again_after_the_window(alerter, sent):
    now = time.time()
    alerter.send("fp", "Title", "body", now=now)
    alerter.send("fp", "Title", "body", now=now + 6 * 3600 + 1)

    assert len(sent.sent) == 2


def test_different_fingerprints_are_independent(alerter, sent):
    alerter.send("a", "A", "body")
    alerter.send("b", "B", "body")

    assert len(sent.sent) == 2


def test_window_survives_a_restart(tmp_path, sent):
    """Each pipeline run is a new process, so the window must be on disk."""
    now = time.time()
    Alerter(str(tmp_path), notifier=sent).send("fp", "T", "b", now=now)
    Alerter(str(tmp_path), notifier=sent).send("fp", "T", "b", now=now + 60)

    assert len(sent.sent) == 1


def test_a_failing_notifier_does_not_raise(tmp_path):
    def boom(title, message, priority="default"):
        raise RuntimeError("ntfy is down")

    alerter = Alerter(str(tmp_path), notifier=boom)
    assert alerter.send("fp", "T", "b") is True      # recorded, not raised


def test_a_failed_send_still_consumes_the_window(tmp_path):
    """A down ntfy must not turn into a push attempt every 15 minutes."""
    calls = []

    def boom(title, message, priority="default"):
        calls.append(title)
        raise RuntimeError("ntfy is down")

    alerter = Alerter(str(tmp_path), notifier=boom)
    now = time.time()
    alerter.send("fp", "T", "b", now=now)
    alerter.send("fp", "T", "b", now=now + 900)

    assert len(calls) == 1


def test_corrupt_state_starts_fresh(tmp_path, sent):
    (tmp_path / "alert_state.json").write_text("{not json")

    assert Alerter(str(tmp_path), notifier=sent).send("fp", "T", "b") is True


def test_expired_windows_are_pruned(alerter):
    now = time.time()
    alerter.send("old", "T", "b", now=now)
    alerter.send("new", "T", "b", now=now + 30 * 3600)

    stored = json.loads(alerter.path.read_text())["sent"]
    assert "old" not in stored
    assert "new" in stored


# --- recovery -------------------------------------------------------------

def test_recovery_alert_fires_once_after_a_failure(alerter, sent):
    alerter.mark_failing("RuntimeError:download", "boom")

    assert alerter.clear_failing() is True
    assert alerter.clear_failing() is False
    assert len(sent.sent) == 1
    assert "recovered" in sent.sent[0]["title"].lower()


def test_no_recovery_alert_without_a_failure(alerter, sent):
    assert alerter.clear_failing() is False
    assert sent.sent == []


def test_recovery_outlives_the_throttle_window(tmp_path, sent):
    """Broken for two days, then fixed: the recovery must still be announced."""
    now = time.time()
    a = Alerter(str(tmp_path), notifier=sent)
    a.send("RuntimeError:download", "Pipeline failed", "boom", now=now)
    a.mark_failing("RuntimeError:download", "boom", now=now)

    later = Alerter(str(tmp_path), notifier=sent)
    assert later.clear_failing(now=now + 2 * 86400) is True
    assert "48.0h" in sent.sent[-1]["message"]


def test_the_same_failure_after_a_recovery_alerts_again(tmp_path, sent):
    """Recovery resets the window: a recurrence is news, not a repeat."""
    now = time.time()
    a = Alerter(str(tmp_path), notifier=sent)
    a.send("RuntimeError:download", "Pipeline failed", "boom", now=now)
    a.mark_failing("RuntimeError:download", "boom", now=now)
    a.clear_failing(now=now + 1800)

    assert a.send("RuntimeError:download", "Pipeline failed", "boom",
                  now=now + 3600) is True


def test_failing_since_is_the_first_failure_not_the_latest(alerter):
    now = time.time()
    alerter.mark_failing("fp", "boom", now=now)
    alerter.mark_failing("fp", "boom again", now=now + 7200)

    assert alerter.failing()["fp"]["since"] == now


# --- scrubbing ------------------------------------------------------------

def test_env_secret_values_are_removed(monkeypatch):
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "s3cr3t-key-material-here")

    out = scrub_secrets("SignatureDoesNotMatch using s3cr3t-key-material-here")

    assert "s3cr3t" not in out
    assert "<R2_SECRET_ACCESS_KEY>" in out


def test_a_short_env_value_is_not_used_as_a_pattern(monkeypatch):
    """Matching a 4-character value would redact ordinary prose."""
    monkeypatch.setenv("EPISODE_TOKEN", "data")

    assert scrub_secrets("could not write data dir") == "could not write data dir"


def test_a_private_feed_token_in_a_url_is_removed():
    """upstream.opml's Substack token is not an env var, so match the shape."""
    url = "https://example.substack.com/feed/private/abc123def456?x=1"

    out = scrub_secrets(f"HTTPError for {url}")

    assert "abc123def456" not in out
    assert "/private/<REDACTED>" in out


def test_a_query_string_token_is_removed():
    out = scrub_secrets("GET https://host/f.xml?token=abc123def&page=2 failed")

    assert "abc123def" not in out
    assert "page=2" in out                       # only the credential goes


def test_url_userinfo_is_removed():
    out = scrub_secrets("auth failed for https://user:hunter2@host/feed.xml")

    assert "hunter2" not in out
    assert "host/feed.xml" in out


def test_long_messages_are_truncated():
    out = scrub_secrets("x" * 5000)

    assert len(out) <= 400


def test_sent_alerts_are_scrubbed(tmp_path, sent, monkeypatch):
    """The scrub has to be on the send path, not left to each caller."""
    monkeypatch.setenv("PUBLIC_PATH_SECRET", "path-secret-value-123")
    alerter = Alerter(str(tmp_path), notifier=sent)

    alerter.send("fp", "Upload failed",
                 "key audio/path-secret-value-123/slug/ep.mp3 failed")

    assert "path-secret-value-123" not in sent.sent[0]["message"]


def test_scrubbing_is_idempotent():
    """Scrubbing happens at the point of logging and again in a log filter.

    Without this, each extra pass matched the `<REDACTED` inside its own
    replacement and appended another `>`.
    """
    once = scrub_secrets("GET /x?token=abc123def&page=2")

    assert scrub_secrets(once) == once
    assert scrub_secrets(scrub_secrets(once)) == once
    assert ">>" not in once


def test_scrubbing_a_path_token_is_idempotent():
    once = scrub_secrets("GET https://example.substack.com/feed/private/abc123")

    assert scrub_secrets(once) == once
    assert ">>" not in once


def test_an_env_secret_scrub_is_idempotent(monkeypatch):
    monkeypatch.setenv("EPISODE_TOKEN", "tok-abcdef123456")
    once = scrub_secrets("GET /episodes/tok-abcdef123456/a/b.mp3")

    assert scrub_secrets(once) == once
    assert "<EPISODE_TOKEN>" in once
