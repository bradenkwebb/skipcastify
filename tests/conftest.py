import pytest


def pytest_addoption(parser):
    parser.addoption("--run-llm", action="store_true", default=False,
                     help="Run LLM integration tests (makes real API calls, costs money)")


def pytest_configure(config):
    config.addinivalue_line("markers", "llm: marks tests that make real LLM API calls")


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--run-llm"):
        skip = pytest.mark.skip(reason="Pass --run-llm to run LLM integration tests")
        for item in items:
            if "llm" in item.keywords:
                item.add_marker(skip)


class NetworkBlocked(BaseException):
    """Raised when a test reaches for the network. See no_outbound_http."""


@pytest.fixture(autouse=True)
def no_outbound_http(request, monkeypatch):
    """Fail loudly if a test makes a real HTTP request.

    Two things in this codebase now talk to third parties on the happy path:
    the publisher pings overcast.fm, and the alerter pushes to ntfy.sh. Worse,
    server.py calls load_dotenv() at import, so importing it puts the real
    NTFY_TOPIC into the environment for the whole session — which means a
    forgotten fake does not fail, it quietly contacts someone else's server
    from the test suite, with a URL that embeds our path secret.

    The LLM tests are exempt: they are explicitly about real API calls and are
    skipped unless --run-llm is passed.
    """
    if "llm" in request.keywords:
        return

    import requests

    def blocked(*args, **kwargs):
        # BaseException, not Exception, on purpose: the code under test
        # deliberately swallows every Exception around network calls so that
        # alerting and pinging can never break a pipeline run. A guard that
        # raised Exception would therefore be caught and logged, and the test
        # would pass while still having tried to reach the internet.
        raise NetworkBlocked(
            "A test tried to make a real HTTP request. Inject a fake "
            "(notifier=, pinger=, requester=) instead of reaching the network."
        )

    monkeypatch.setattr(requests.Session, "request", blocked)
    monkeypatch.setattr(requests, "get", blocked)
    monkeypatch.setattr(requests, "post", blocked)
