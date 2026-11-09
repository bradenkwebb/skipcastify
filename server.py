import ipaddress
import json
import logging
import os
import subprocess
import time
from functools import wraps

import yaml
from dotenv import load_dotenv
from flask import (
    Flask,
    Response,
    abort,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    send_file,
    send_from_directory,
)

from skipcastify.services import (
    analytics,
    cut_policy,
    feed_meta,
    labeling,
    settings,
    subscriptions,
)
from skipcastify.services.artwork_thumbs import thumbnail
from skipcastify.services.episode_detail import episode_detail
from skipcastify.services.published_feeds import published_feeds
from skipcastify.services.state_manager import StateManager
from skipcastify.utils.alerts import scrub_secrets

load_dotenv()

app = Flask(__name__)

data_dir = os.environ.get("DATA_DIR")
_feed_username = os.environ.get("FEED_USERNAME")
_feed_password = os.environ.get("FEED_PASSWORD")


def _parse_tailscale_allow(raw: str) -> dict[str, set[str] | None]:
    """'login' allows any of that user's devices; 'login/device' only that device."""
    allow: dict[str, set[str] | None] = {}
    for entry in raw.split(","):
        login, _, device = entry.strip().lower().partition("/")
        if not login:
            continue
        if not device:
            allow[login] = None
        elif allow.get(login, set()) is not None:
            allow.setdefault(login, set()).add(device)
    return allow


# Tailnet logins (optionally /device) that skip Basic auth. Empty: Basic auth only.
_tailscale_users = _parse_tailscale_allow(os.environ.get("TAILSCALE_USERS", ""))
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
    who = f" user={_user()}" if _user() else ""

    app.logger.info("%s %s -> %s %.1fms%s", request.method, target,
                    response.status_code, duration_ms, who)
    return response


def _tailscale_user() -> str | None:
    """The allowlisted tailnet login `tailscale serve` vouches for, if any.

    Serve strips client-sent identity headers and sets its own, and sets none for
    tagged devices. Trusted only from loopback, where gunicorn listens behind serve.
    """
    if not _tailscale_users or request.remote_addr not in ("127.0.0.1", "::1"):
        return None
    login = (request.headers.get("Tailscale-User-Login") or "").strip().lower()
    if login not in _tailscale_users:
        return None
    devices = _tailscale_users[login]
    if devices is None:
        return login
    # Serve sets X-Forwarded-For to the client's tailnet IP (inbound values are dropped).
    who = _whois(request.headers.get("X-Forwarded-For", "").strip())
    if who and who[0] == login and who[1] in devices:
        return f"{login}/{who[1]}"
    return None


_whois_cache: dict[str, tuple[float, tuple[str, str] | None]] = {}


def _whois(ip: str) -> tuple[str, str] | None:
    """(login, device) for a tailnet IP via the local tailscaled; cached 5 minutes."""
    try:
        ip = str(ipaddress.ip_address(ip))
    except ValueError:
        return None
    hit = _whois_cache.get(ip)
    if hit and time.monotonic() - hit[0] < 300:
        return hit[1]
    try:
        out = subprocess.run(["tailscale", "whois", "--json", ip], capture_output=True,
                             text=True, timeout=3, check=True).stdout
        d = json.loads(out)
        who = (d["UserProfile"]["LoginName"].lower(), d["Node"]["ComputedName"].lower())
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        who = None
    _whois_cache[ip] = (time.monotonic(), who)
    return who


def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if _tailscale_user():
            return f(*args, **kwargs)
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


@app.route('/feeds/art/<slug>.jpg')
@require_auth
def feed_art(slug):
    path = thumbnail(data_dir, slug) if labeling.valid_name(slug) else None
    if path is None:
        return abort(404)
    resp = send_from_directory(path.parent, path.name)
    resp.headers['Cache-Control'] = 'private, max-age=86400'
    return resp


@app.route('/artwork/<token>/<path:filename>')
def serve_artwork(token, filename):
    if token != _episode_token:
        return abort(403)
    path = os.path.join(data_dir, 'artwork')
    if not os.path.exists(os.path.join(path, filename)):
        return abort(404)
    return send_from_directory(path, filename)


@app.route('/')
def root():
    return redirect('/dashboard')


@app.route('/dashboard')
@require_auth
def dashboard():
    return render_template('dashboard.html')


@app.route('/dashboard/episode/<episode>')
@require_auth
def episode_page(episode):
    if not labeling.valid_name(episode):
        return abort(404)
    return render_template('episode.html', episode=episode)


@app.route('/api/feed/<slug>/meta.json')
@require_auth
def feed_meta_json(slug):
    """Show-level info (title, artwork, website) from the feed's cached upstream copy."""
    if not labeling.valid_name(slug):
        return abort(404)
    return jsonify(feed_meta.show_meta(data_dir, slug))


@app.route('/api/episode/<episode>/meta.json')
@require_auth
def episode_meta_json(episode):
    """Description and links from the show's live RSS feed, fetched on demand."""
    if not labeling.valid_name(episode):
        return abort(404)
    feed = labeling.feed_for(data_dir, episode)
    return jsonify(feed_meta.episode_meta(data_dir, feed, episode))


@app.route('/api/episode/<episode>.json')
@require_auth
def episode_json(episode):
    detail = episode_detail(data_dir, episode)
    return jsonify(detail) if detail else abort(404)


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
        "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; "
        "script-src 'unsafe-inline'; form-action 'none'; base-uri 'none'"
    )
    return resp


@app.route('/api/feeds.json')
@require_auth
def feeds_json():
    """The published feeds as JSON, for the iOS app. Same secrecy as /feeds: never cache."""
    feeds = [{
        "slug": f.slug, "title": f.title, "url": f.url, "episodes": f.episodes,
        "audio_bytes": f.audio_bytes, "last_audio_upload": f.last_audio_upload,
        "feed_uploaded": f.feed_uploaded, "add_links": f.add_links,
    } for f in published_feeds(data_dir, _public_base_url)]
    resp = jsonify(publishing_enabled=_publish_to_r2, feeds=feeds)
    resp.headers['Cache-Control'] = 'no-store'
    return resp


def _write_refused():
    """None if this looks like a same-origin write from our own page.

    Browsers attach Basic auth cross-site, so: a custom header and a JSON body
    both force a CORS preflight (never approved here), and Sec-Fetch-Site,
    when sent, must say same-origin.
    """
    site = request.headers.get('Sec-Fetch-Site')
    if (request.headers.get('X-Skipcastify') != '1' or not request.is_json
            or (site is not None and site not in ('same-origin', 'none'))):
        return abort(403)
    return None


def _user():
    if login := _tailscale_user():
        return login
    return request.authorization.username if request.authorization else None


@app.route('/settings')
@require_auth
def settings_page():
    resp = make_response(render_template('settings.html'))
    resp.headers['Cache-Control'] = 'no-store'
    resp.headers['Content-Security-Policy'] = (
        "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; "
        "script-src 'unsafe-inline'; connect-src 'self'; form-action 'none'; base-uri 'none'"
    )
    return resp


@app.route('/api/settings.json')
@require_auth
def settings_json():
    path = cut_policy.policy_path()
    try:
        policy, error = settings.snapshot(path), None
    except (ValueError, yaml.YAMLError) as e:  # a bad hand edit: show it, don't overwrite it
        policy, error = {"default": [], "feeds": {}}, f"policy.yaml is invalid: {e}"
    skipped = StateManager(data_dir).skipped()
    subs_path = subscriptions.opml_path()
    subs = subscriptions.read(subs_path)
    return jsonify(
        version=settings.version(path), policy=policy, error=error,
        labels=list(cut_policy.LABELS), feeds=settings.feeds(data_dir, policy, subs),
        changes=settings.changes(data_dir),
        skipped=[{"episode": k, "attempts": n} for k, n in sorted(skipped.items())],
        subscriptions=_subscription_rows(subs), subs_version=settings.version(subs_path),
        upstream=[u for u in subscriptions.read(subscriptions.upstream_path())
                  if u["url"] not in {s["url"] for s in subs}],
        r2=_r2_usage(),
    )


def _subscription_rows(subs):
    status = {r["feed_slug"]: r for r in analytics.feed_status(analytics.recent_runs(data_dir, 500))}
    published = {f.slug for f in published_feeds(data_dir, _public_base_url)}
    rows = []
    for sub in subs:
        slug = settings.feed_slug(data_dir, sub)
        st = status.get(slug, {})
        rows.append({**sub, "slug": slug, "checked": st.get("checked"),
                     "last_download": st.get("last_download"), "published": slug in published})
    return rows


def _r2_usage():
    used = sum(f.audio_bytes for f in published_feeds(data_dir, _public_base_url))
    return {"used_bytes": used, "cap_bytes": int(float(os.environ.get("R2_CAP_GB", 7)) * 1024 ** 3)}


@app.route('/api/settings/policy.json', methods=['PUT'])
@require_auth
def settings_policy():
    if (refused := _write_refused()) is not None:
        return refused
    body = request.get_json(silent=True) or {}
    try:
        entry = settings.save(data_dir, body.get('policy'), body.get('version'), _user())
    except settings.Conflict as e:
        return jsonify(error=str(e)), 409
    except ValueError as e:
        return jsonify(error=str(e)), 400
    return jsonify(change=entry)


@app.route('/api/settings/undo/<int:change_id>.json', methods=['POST'])
@require_auth
def settings_undo(change_id):
    if (refused := _write_refused()) is not None:
        return refused
    body = request.get_json(silent=True) or {}
    try:
        entry = settings.undo(data_dir, change_id, body.get('versions') or {}, _user())
    except KeyError:
        return abort(404)
    except settings.Conflict as e:
        return jsonify(error=str(e)), 409
    return jsonify(change=entry)


@app.route('/api/settings/subscriptions/search.json', methods=['POST'])
@require_auth
def subscriptions_search():
    if (refused := _write_refused()) is not None:
        return refused
    term = ((request.get_json(silent=True) or {}).get('term') or '').strip()
    if not term:
        return jsonify(results=[])
    try:
        return jsonify(results=subscriptions.search(term[:100]))
    except Exception as e:
        return jsonify(error=f"search failed ({type(e).__name__})"), 502


@app.route('/api/settings/subscriptions/preview.json', methods=['POST'])
@require_auth
def subscriptions_preview():
    """Fetches the feed server-side, so it gets the same guard as a write."""
    if (refused := _write_refused()) is not None:
        return refused
    url = (request.get_json(silent=True) or {}).get('url') or ''
    try:
        url = subscriptions.check_url(url)
        summary = subscriptions.summarize(subscriptions.fetch(url))
    except ValueError as e:
        return jsonify(error=str(e)), 400
    except Exception as e:
        return jsonify(error=f"couldn't load that feed ({type(e).__name__})"), 502
    subscribed = {s['url'] for s in subscriptions.read(subscriptions.opml_path())}
    return jsonify(url=url, already=url in subscribed, **summary)


@app.route('/api/settings/subscriptions/<action>.json', methods=['POST'])
@require_auth
def subscriptions_change(action):
    if action not in ('add', 'remove'):
        return abort(404)
    if (refused := _write_refused()) is not None:
        return refused
    body = request.get_json(silent=True) or {}
    url, version = body.get('url') or '', body.get('version')
    try:
        if action == 'add':
            url = subscriptions.check_url(url)
            title = (body.get('title') or '').strip()[:200] or url
            entry = settings.add_subscription(data_dir, url, title, version, _user())
        else:
            entry = settings.remove_subscription(data_dir, url, version, _user())
    except settings.Conflict as e:
        return jsonify(error=str(e)), 409
    except KeyError:
        return abort(404)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    return jsonify(change=entry)


@app.route('/api/settings/retry.json', methods=['POST'])
@require_auth
def settings_retry():
    """Clear a skipped episode's failure count so the next run tries it again."""
    if (refused := _write_refused()) is not None:
        return refused
    episode = (request.get_json(silent=True) or {}).get('episode')
    sm = StateManager(data_dir)
    if episode not in sm.skipped():
        return abort(404)
    sm.finish_attempt(sm.raw_dir / episode)
    app.logger.info("retry %s requested by %s", episode, _user())
    return jsonify(ok=True)


@app.route('/api/stats.json')
@require_auth
def stats_json():
    days = request.args.get('days', type=int)
    if days is not None and days <= 0:
        days = None
    return jsonify(analytics.build_payload(data_dir, days))


@app.route('/label')
@app.route('/label/<episode>')
@require_auth
def label_page(episode=None):
    if episode is not None and not labeling.valid_episode(data_dir, episode):
        return abort(404)
    return render_template('label.html', episode=episode)


@app.route('/api/label/episodes.json')
@require_auth
def label_episodes():
    return jsonify(labeling.list_episodes(data_dir))


@app.route('/api/label/<episode>.json', methods=['GET', 'PUT'])
@require_auth
def label_episode(episode):
    if not labeling.valid_episode(data_dir, episode):
        return abort(404)
    if request.method == 'GET':
        return jsonify(labeling.load_episode(data_dir, episode))
    # Browsers attach Basic auth cross-site; a custom header forces a CORS
    # preflight, which this server never approves.
    if request.headers.get('X-Skipcastify') != '1' or not request.is_json:
        return abort(403)
    body = request.get_json(silent=True) or {}
    try:
        doc = labeling.save_labels(data_dir, episode, body.get('spans'), body.get('reviewed', False))
    except ValueError as e:
        return jsonify(error=str(e)), 400
    return jsonify(doc)


@app.route('/api/label/<episode>/audio')
@require_auth
def label_audio(episode):
    if not labeling.valid_episode(data_dir, episode):
        return abort(404)
    path = labeling.raw_audio_path(data_dir, episode)
    if path is None:
        return abort(404)
    return send_file(path, mimetype='audio/mpeg', conditional=True)


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
