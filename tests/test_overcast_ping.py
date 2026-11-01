"""Tests for the Overcast re-crawl ping.

No network: `ping` takes its requester as a collaborator. The URL it builds
carries PUBLIC_PATH_SECRET, so several of these are about that URL never
reaching a log.
"""

import logging

from skipcastify.services.overcast_ping import PING_URL, ping, ping_url_for

FEED = "https://example.com/feeds/path-secret-value-abcdef/a-show.xml"
SECRET = "path-secret-value-abcdef"


class FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


def recorder(status=200):
    calls = []

    def get(url, timeout=None):
        calls.append({"url": url, "timeout": timeout})
        return FakeResponse(status)

    get.calls = calls
    return get


# --- the request ----------------------------------------------------------

def test_the_feed_url_is_sent_as_an_encoded_urlprefix():
    """overcast.fm/podcasterinfo: urlprefix is required and URL-encoded."""
    url = ping_url_for(FEED)

    assert url.startswith(f"{PING_URL}?urlprefix=")
    assert "https%3A%2F%2Fexample.com%2Ffeeds%2F" in url
    assert "://example.com" not in url.split("urlprefix=")[1]


def test_a_full_feed_url_is_used_not_a_bare_prefix():
    """Overcast asks callers to be as specific as possible."""
    assert ping_url_for(FEED).endswith("a-show.xml")


def test_a_successful_ping_reports_success():
    get = recorder(200)

    assert ping(FEED, requester=get) is True
    assert len(get.calls) == 1


def test_the_timeout_is_passed_through():
    get = recorder(200)

    ping(FEED, timeout=3, requester=get)

    assert get.calls[0]["timeout"] == 3


def test_a_rate_limited_ping_is_not_retried():
    """The API is heavily rate-limited by design; retrying makes it worse."""
    get = recorder(429)

    assert ping(FEED, requester=get) is False
    assert len(get.calls) == 1


def test_a_server_error_reports_failure():
    assert ping(FEED, requester=recorder(503)) is False


def test_a_network_error_is_swallowed():
    """Alerting and pinging must never be able to break a pipeline run."""
    def boom(url, timeout=None):
        raise OSError("connection reset")

    assert ping(FEED, requester=boom) is False


# --- the URL must not reach a log ----------------------------------------

def test_a_failing_status_does_not_log_the_url(caplog):
    caplog.set_level(logging.DEBUG)

    ping(FEED, requester=recorder(429))

    assert SECRET not in caplog.text
    assert "429" in caplog.text


def test_a_successful_ping_does_not_log_the_url(caplog):
    caplog.set_level(logging.DEBUG)

    ping(FEED, requester=recorder(200))

    assert SECRET not in caplog.text


def test_an_exception_message_containing_the_url_is_not_logged(caplog):
    """requests puts the full request URL into its exception messages, which
    is the whole reason this logs the exception's class and not the exception."""
    caplog.set_level(logging.DEBUG)

    def boom(url, timeout=None):
        raise OSError(f"Max retries exceeded with url: {url}")

    ping(FEED, requester=boom)

    assert SECRET not in caplog.text
    assert "OSError" in caplog.text
