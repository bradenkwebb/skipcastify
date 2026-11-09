"""Tests for the r2ctl hand controls.

The point of most of these is what the output must *not* contain: keys embed
PUBLIC_PATH_SECRET, and this is a command whose output gets pasted around.
"""

import time

import pytest

from skipcastify.services.publish_manifest import PublishManifest
from skipcastify.services.r2_breaker import R2Breaker
from skipcastify.tools import r2ctl

SECRET = "path-secret-value-abcdef123456"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(r2ctl, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PUBLIC_PATH_SECRET", SECRET)
    monkeypatch.setenv("NTFY_TOPIC", "a-topic")
    monkeypatch.delenv("R2_MAX_CLASS_A_PER_RUN", raising=False)
    monkeypatch.delenv("R2_MAX_CLASS_A_PER_DAY", raising=False)
    return tmp_path


def test_status_reports_a_closed_breaker(env, capsys):
    assert r2ctl.main(["status"]) == 0

    out = capsys.readouterr().out
    assert "closed" in out


def test_status_reports_a_tripped_breaker_and_how_to_clear_it(env, capsys):
    R2Breaker(str(env)).trip("1234 Class A operations in one run (limit 200)")

    r2ctl.main(["status"])

    out = capsys.readouterr().out
    assert "TRIPPED" in out
    assert "1234 Class A operations" in out
    assert "clear-breaker" in out


def test_status_never_prints_an_object_key(env, capsys):
    """Keys embed the path secret and this output gets pasted into chats."""
    manifest = PublishManifest(str(env))
    manifest.record_put(f"audio/{SECRET}/a-show/ep.mp3", 1024, "audio")
    manifest.save()

    r2ctl.main(["status"])

    out = capsys.readouterr().out
    assert SECRET not in out
    assert "ep.mp3" not in out
    assert "1 live" in out


def test_status_says_when_alerts_are_silent(env, capsys, monkeypatch):
    monkeypatch.delenv("NTFY_TOPIC")

    r2ctl.main(["status"])

    assert "UNSET" in capsys.readouterr().out


def test_status_reports_usage(env, capsys):
    manifest = PublishManifest(str(env))
    manifest.record_put(f"audio/{SECRET}/a-show/ep.mp3", 3 * 1024 ** 3, "audio")
    manifest.bump_class_a(7)
    manifest.mark_reconciled(time.time() - 2 * 3600)
    manifest.save()

    r2ctl.main(["status"])

    out = capsys.readouterr().out
    assert "3.00 GB" in out
    assert "class A today:      7" in out
    assert "2.0h ago" in out


def test_clear_breaker_re_enables_publishing(env, capsys):
    breaker = R2Breaker(str(env))
    breaker.trip("manual test")

    assert r2ctl.main(["clear-breaker"]) == 0

    assert not breaker.tripped
    assert "cleared" in capsys.readouterr().out


def test_clear_breaker_on_a_closed_breaker_is_harmless(env, capsys):
    assert r2ctl.main(["clear-breaker"]) == 0
    assert "not tripped" in capsys.readouterr().out


def test_test_alert_fails_loudly_with_no_topic(env, capsys, monkeypatch):
    monkeypatch.delenv("NTFY_TOPIC")

    assert r2ctl.main(["test-alert"]) == 1
    assert "silent no-op" in capsys.readouterr().err


def test_test_alert_reports_delivery(env, capsys, monkeypatch):
    monkeypatch.setattr(r2ctl, "notify", lambda *a, **k: True)

    assert r2ctl.main(["test-alert"]) == 0
    assert "Delivered" in capsys.readouterr().out


def test_test_alert_reports_a_failure(env, capsys, monkeypatch):
    monkeypatch.setattr(r2ctl, "notify", lambda *a, **k: False)

    assert r2ctl.main(["test-alert"]) == 1
    assert "Not delivered" in capsys.readouterr().out


def test_an_unknown_command_is_rejected(env):
    with pytest.raises(SystemExit):
        r2ctl.main(["frobnicate"])


def test_a_missing_data_dir_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(r2ctl, "load_dotenv", lambda *a, **k: None)
    monkeypatch.delenv("DATA_DIR", raising=False)

    with pytest.raises(SystemExit):
        r2ctl.main(["status"])
