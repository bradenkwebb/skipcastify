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
