"""Tests for serving the app under gunicorn.

Two things are checked here that a normal route test would not catch: that
gunicorn's own entry point can load the app, and that nothing writes a
URL-path secret into the error log — which is the reason there is no access
log in the first place.
"""

import logging
import os
import re
from pathlib import Path

import pytest

os.environ.update({
    'FEED_USERNAME': 'testuser',
    'FEED_PASSWORD': 'testpass',
    'EPISODE_TOKEN': 'testtoken',
    'DATA_DIR': '',
})

import server as server_module                    # noqa: E402

UNIT = Path(__file__).resolve().parents[1] / "systemd" / "skipcastify-server.service"


# --- the entry point ------------------------------------------------------

def test_gunicorn_can_import_the_app():
    """`server:app` is what the unit file passes; resolve it the same way.

    gunicorn's own loader, not a plain import, so a module that imports fine
    but exposes no `app` would still fail here.
    """
    from gunicorn.util import import_app

    app = import_app("server:app")

    assert callable(app)
    assert app is server_module.app


def test_the_app_is_a_wsgi_callable():
    """gunicorn calls app(environ, start_response); Flask instances do."""
    import inspect

    assert len(inspect.signature(server_module.app.__call__).parameters) == 2


# --- the unit file --------------------------------------------------------

def _directives() -> str:
    """The unit's directives with comments stripped.

    Comments matter here: this file explains at length *why* there is no
    --access-logfile, so a naive substring search finds the explanation.
    """
    text = UNIT.read_text()
    # Join the backslash-continued ExecStart into one logical line first.
    text = re.sub(r"\\\n\s*", " ", text)
    return "\n".join(l for l in text.splitlines()
                      if l.strip() and not l.lstrip().startswith("#"))


def _exec_start() -> str:
    line = next(l for l in _directives().splitlines()
                if l.startswith("ExecStart="))
    return line[len("ExecStart="):]


def test_the_unit_runs_gunicorn_not_the_dev_server():
    exec_start = _exec_start()

    assert "/gunicorn" in exec_start
    assert "server:app" in exec_start
    assert "python server.py" not in exec_start


def test_the_unit_has_no_access_log():
    """The legacy /episodes/<token>/ routes carry EPISODE_TOKEN in the path,
    so an access log would persist it to journald."""
    assert "--access-logfile" not in _directives()


def test_the_unit_binds_loopback_only():
    """tailscale serve proxies localhost:5000; anything else exposes the LAN."""
    assert "--bind 127.0.0.1:5000" in _exec_start()


def test_the_unit_uses_threads_rather_than_only_sync_workers():
    """A sync worker notifies the arbiter only between requests, so a 90 MB
    episode taking longer than --timeout gets killed mid-stream — and one
    request at a time would block the dashboard behind two downloads."""
    exec_start = _exec_start()

    assert "--threads" in exec_start
    threads = int(re.search(r"--threads (\d+)", exec_start).group(1))
    assert threads > 1          # > 1 is what selects the gthread worker


def test_the_unit_can_be_reloaded_and_restarts_on_failure():
    text = _directives()

    assert "ExecReload=/bin/kill -HUP $MAINPID" in text
    assert "Restart=on-failure" in text


# --- no secret reaches the error log --------------------------------------

TOKEN = "tok-abcdef123456789"


@pytest.fixture
def token_env(monkeypatch):
    """The scrubber matches values from the environment, as it does in prod."""
    monkeypatch.setenv("EPISODE_TOKEN", TOKEN)
    return TOKEN


@pytest.fixture
def record_factory():
    def make(msg, *args):
        return logging.LogRecord("test", logging.ERROR, __file__, 1, msg,
                                 args, None)
    return make


def test_a_token_in_a_request_path_is_redacted(record_factory, token_env):
    """Flask logs "Exception on {request.path}" for any unhandled view error."""
    record = record_factory("Exception on /episodes/%s/show/ep.mp3 [GET]", TOKEN)

    assert server_module.RedactSecrets().filter(record) is True
    assert TOKEN not in record.getMessage()
    assert "<EPISODE_TOKEN>" in record.getMessage()


def test_a_gunicorn_style_error_line_is_redacted(record_factory, token_env):
    """gunicorn's handle_error logs "Error handling request {method} {uri}"."""
    record = record_factory("Error handling request %s %s", "GET",
                            f"/artwork/{TOKEN}/cover.jpg")

    server_module.RedactSecrets().filter(record)

    assert TOKEN not in record.getMessage()


def test_redaction_does_not_double_format_the_message(record_factory, token_env):
    """The args are folded in, so the formatter must not apply them again."""
    record = record_factory("path %s and %d", f"/x/{TOKEN}", 7)

    server_module.RedactSecrets().filter(record)

    assert record.args == ()
    assert record.getMessage() == "path /x/<EPISODE_TOKEN> and 7"


def test_an_unrelated_record_is_left_alone(record_factory, token_env):
    record = record_factory("Booting worker with pid: %d", 1234)

    server_module.RedactSecrets().filter(record)

    assert record.getMessage() == "Booting worker with pid: 1234"
    assert record.args == (1234,)          # still lazily formatted


def test_an_unformattable_record_does_not_break_logging(record_factory):
    """A filter that raises turns every logging call into an exception."""
    record = record_factory("missing arg %s %s", "only-one")

    assert server_module.RedactSecrets().filter(record) is True


def test_the_filter_is_attached_where_a_path_could_be_formatted():
    """gunicorn.error sets propagate = False, so the root is not enough."""
    installed = server_module.install_log_redaction()

    for logger in (server_module.app.logger,
                   logging.getLogger("gunicorn.error"),
                   logging.getLogger()):
        assert installed in logger.filters
        logger.removeFilter(installed)
        for handler in logger.handlers:
            handler.removeFilter(installed)
