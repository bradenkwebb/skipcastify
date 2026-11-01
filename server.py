import logging
import os
import time
from functools import wraps
from flask import (Flask, send_from_directory, abort, request, Response,
                   jsonify, make_response, render_template)
from dotenv import load_dotenv

from skipcastify.services import analytics
from skipcastify.services.published_feeds import published_feeds
from skipcastify.utils.alerts import scrub_secrets

load_dotenv()

app = Flask(__name__)

data_dir = os.environ.get("DATA_DIR")
_feed_username = os.environ.get("FEED_USERNAME")
_feed_password = os.environ.get("FEED_PASSWORD")
_episode_token = os.environ.get("EPISODE_TOKEN")
_public_base_url = os.environ.get("PUBLIC_BASE_URL", "")
_publish_to_r2 = os.environ.get("PUBLISH_TO_R2", "false").lower() == "true"


class RedactSecrets(logging.Filter):
    """Last-resort scrub for log records nobody here formatted.

    Two things write a request path to the error log without being asked:

      * Flask logs ``Exception on {request.path} [{method}]`` for any
        unhandled view exception (flask/app.py log_exception);
      * gunicorn's handle_error logs ``Error handling request {method} {uri}``
        (gunicorn/workers/base.py).

    The legacy ``/episodes/<token>/`` and ``/artwork/<token>/`` routes carry
    EPISODE_TOKEN in the path, so either one would put it in journald, where
    it persists for the life of the journal.

    This covers log *messages*. A traceback that embeds a secret in an
    exception argument is not rewritten, so this is a safety net under the
    request log below, not a substitute for scrubbing at the point of use.
    """

    def __init__(self, scrubber=scrub_secrets) -> None:
        super().__init__()
        self._scrub = scrubber

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            # A malformed record must not raise out of a filter: that turns a
            # logging call anywhere in the process into an exception.
            return True
        scrubbed = self._scrub(message)
        if scrubbed != message:
            # The args are folded into the message above, so they must not be
            # applied a second time by the formatter.
            record.msg = scrubbed
            record.args = ()
        return True


def install_log_redaction(scrubber=scrub_secrets) -> RedactSecrets:
    """Attach RedactSecrets wherever a request path could be formatted.

    A filter on a logger only sees records logged *on that logger*:
    propagation runs an ancestor's handlers, not its filters. So this attaches
    to the two loggers known to format a request path — Flask's app logger and
    ``gunicorn.error``, which sets ``propagate = False`` and would otherwise
    bypass anything on the root — and to whatever handlers exist, which is
    what catches a record propagated up from any other module.
    """
    redactor = RedactSecrets(scrubber)
    for logger in (app.logger, logging.getLogger("gunicorn.error"),
                   logging.getLogger()):
        logger.addFilter(redactor)
        for handler in logger.handlers:
            handler.addFilter(redactor)
    return redactor


# Accessing app.logger is what makes Flask attach its stderr handler, so this
# has to come before the level is set below.
install_log_redaction()

# gunicorn configures only its own loggers, leaving the root logger with no
# handlers — so Flask's logger is NOTSET, inherits WARNING from the root, and
# would drop every INFO line of the request log below. Set it explicitly.
app.logger.setLevel(logging.INFO)


@app.template_filter("mb")
def _mb(num_bytes: int) -> str:
    if num_bytes >= 1024 ** 3:
        return f"{num_bytes / 1024 ** 3:.1f} GB"
    return f"{num_bytes / 1024 ** 2:.0f} MB"


@app.template_filter("ago")
def _ago(when: float) -> str:
    seconds = max(0.0, time.time() - float(when))
    if seconds < 3600:
        return f"{seconds / 60:.0f}m ago"
    if seconds < 86400:
        return f"{seconds / 3600:.0f}h ago"
    return f"{seconds / 86400:.0f}d ago"


# --- request log ----------------------------------------------------------
#
# This replaces gunicorn's access log rather than supplementing it. gunicorn
# would write the raw request line, and the legacy /episodes/<token>/ and
# /artwork/<token>/ routes carry EPISODE_TOKEN in the path, so its access log
# would persist a credential to journald for the life of the journal. Logging
# here instead means the path goes through scrub_secrets first.
#
# 401s are logged too, deliberately: a tailnet-only service still wants
# failed-login visibility, and that is the one line you would go looking for.


@app.before_request
def _start_timer():
    request.environ['skipcastify.start'] = time.monotonic()


@app.after_request
def _log_request(response):
    started = request.environ.get('skipcastify.start')
    duration_ms = (time.monotonic() - started) * 1000 if started else -1.0

    target = scrub_secrets(request.path)
    if request.query_string:
        target += "?" + scrub_secrets(request.query_string.decode('latin-1'))

    # The username only. Never the password, and never the Authorization
    # header, which is the password in a trivially reversible encoding.
    auth = request.authorization
    who = f" user={auth.username}" if auth and auth.username else ""

    app.logger.info("%s %s -> %s %.1fms%s", request.method, target,
                    response.status_code, duration_ms, who)
    return response


def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or auth.username != _feed_username or auth.password != _feed_password:
            return Response(
                'Unauthorized',
                401,
                {'WWW-Authenticate': 'Basic realm="Skipcastify"'},
            )
        return f(*args, **kwargs)
    return decorated


@app.route('/feeds/<podcast_name>.xml')
@require_auth
def serve_feed(podcast_name):
    path = os.path.join(data_dir, 'feeds')
    filename = f'{podcast_name}.xml'
    if not os.path.exists(os.path.join(path, filename)):
        return abort(404)
    return send_from_directory(path, filename)


@app.route('/artwork/<token>/<path:filename>')
def serve_artwork(token, filename):
    if token != _episode_token:
        return abort(403)
    path = os.path.join(data_dir, 'artwork')
    if not os.path.exists(os.path.join(path, filename)):
        return abort(404)
    return send_from_directory(path, filename)


@app.route('/dashboard')
@require_auth
def dashboard():
    return render_template('dashboard.html')


@app.route('/feeds')
@require_auth
def feeds_page():
    """The R2 feed URLs, for copying on a phone.

    Overcast only takes a feed URL in its mobile app, and these URLs are long
    and contain a random secret, so retyping one is not an option.

    Every URL on this page is effectively a password: the bucket is
    world-readable, so holding one is holding the audio. Hence the headers
    below — no-store so it is not left in a disk cache, no-referrer so the
    URL cannot leak through a Referer header, and a CSP that permits no
    outbound requests at all, which both forbids a third-party script from
    reading these URLs out of the DOM and makes adding a CDN to this template
    fail loudly rather than quietly widen the exposure.
    """
    resp = make_response(render_template(
        'feeds.html',
        feeds=published_feeds(data_dir, _public_base_url),
        publishing_enabled=_publish_to_r2,
    ))
    resp.headers['Cache-Control'] = 'no-store'
    resp.headers['Referrer-Policy'] = 'no-referrer'
    resp.headers['Content-Security-Policy'] = (
        "default-src 'none'; style-src 'unsafe-inline'; "
        "script-src 'unsafe-inline'; form-action 'none'; base-uri 'none'"
    )
    return resp


@app.route('/api/stats.json')
@require_auth
def stats_json():
    return jsonify(analytics.build_payload(data_dir))


@app.route('/episodes/<token>/<podcast_name>/<path:filename>')
def serve_episode(token, podcast_name, filename):
    if token != _episode_token:
        return abort(403)
    for subdir in ('processed', 'raw'):
        path = os.path.join(data_dir, 'podcasts', subdir, podcast_name)
        if os.path.exists(os.path.join(path, filename)):
            return send_from_directory(path, filename)
    return abort(404)


if __name__ == '__main__':
    # Local development only. In production this app is served by gunicorn
    # (see systemd/skipcastify-server.service), which imports `server:app`
    # directly and never reaches this block.
    #
    # threaded=True so a slow episode download doesn't block feed/artwork/other
    # requests — podcast apps open several connections at once. The gunicorn
    # unit keeps that property with --threads rather than sync workers.
    #
    # Loopback by default: tailscale serve proxies localhost:5000, so binding
    # every interface exposes the server to the LAN for no benefit. Override
    # with BIND_HOST=0.0.0.0 if it ever needs to be reachable directly.
    app.run(host=os.environ.get('BIND_HOST', '127.0.0.1'),
            port=int(os.environ.get('BIND_PORT', '5000')),
            threaded=True)
