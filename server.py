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
    """Scrub secrets from log messages Flask and gunicorn format on errors
    (both include the request path). Tracebacks are not rewritten."""

    def __init__(self, scrubber=scrub_secrets) -> None:
        super().__init__()
        self._scrub = scrubber

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            # Never raise from a filter.
            return True
        scrubbed = self._scrub(message)
        if scrubbed != message:
            # Args are already folded into the message.
            record.msg = scrubbed
            record.args = ()
        return True


def install_log_redaction(scrubber=scrub_secrets) -> RedactSecrets:
    """Attach RedactSecrets to the Flask, gunicorn.error and root loggers and
    their handlers (logger filters don't apply to propagated records)."""
    redactor = RedactSecrets(scrubber)
    for logger in (app.logger, logging.getLogger("gunicorn.error"),
                   logging.getLogger()):
        logger.addFilter(redactor)
        for handler in logger.handlers:
            handler.addFilter(redactor)
    return redactor


install_log_redaction()
# Under gunicorn the root logger stays at WARNING; the request log is INFO.
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


# Request log, in place of gunicorn's access log so paths get scrubbed.


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

    # Username only, never the password or Authorization header.
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
    # Local development only; production runs gunicorn (see systemd/).
    app.run(host=os.environ.get('BIND_HOST', '127.0.0.1'),
            port=int(os.environ.get('BIND_PORT', '5000')),
            threaded=True)
