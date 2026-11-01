"""Tests for the /feeds page.

Every URL this page renders is effectively a password, so about half of these
are tests of what must *not* happen: no unauthenticated access, no caching,
no third-party script that could read the URLs out of the DOM.
"""

import os
import re

import pytest

os.environ.update({
    'FEED_USERNAME': 'testuser',
    'FEED_PASSWORD': 'testpass',
    'EPISODE_TOKEN': 'testtoken',
    'DATA_DIR': '',
})

import server as server_module                                   # noqa: E402
from skipcastify.services.publish_manifest import PublishManifest  # noqa: E402

SECRET = "path-secret-value-abcdef123456"
AUTH = {'Authorization': 'Basic dGVzdHVzZXI6dGVzdHBhc3M='}       # testuser:testpass


@pytest.fixture
def client(tmp_path):
    server_module.data_dir = str(tmp_path)
    server_module._feed_username = 'testuser'
    server_module._feed_password = 'testpass'
    server_module._public_base_url = 'https://example.com'
    server_module._publish_to_r2 = True
    server_module.app.config['TESTING'] = True
    with server_module.app.test_client() as c:
        yield c, tmp_path


def feed_key(slug, secret=SECRET):
    return f"feeds/{secret}/{slug}.xml"


def audio_key(slug, filename, secret=SECRET):
    return f"audio/{secret}/{slug}/{filename}"


def write_local_feed(tmp_path, slug, title):
    d = tmp_path / "feeds_r2"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{slug}.xml").write_text(
        '<?xml version="1.0"?><rss version="2.0"><channel>'
        f'<title>{title}</title><link>https://x.test/</link>'
        '<item><title>An episode</title></item>'
        '</channel></rss>')


def publish(tmp_path, slug, title="A Show", episodes=0, secret=SECRET):
    """Put a feed in the manifest, as a successful upload would."""
    manifest = PublishManifest(str(tmp_path))
    manifest.record_put(feed_key(slug, secret), 2048, "feed")
    for i in range(episodes):
        manifest.record_put(audio_key(slug, f"ep{i}.mp3", secret),
                            30 * 1024 ** 2, "audio")
    manifest.save()
    write_local_feed(tmp_path, slug, title)
    return manifest


def get_page(c):
    return c.get('/feeds', headers=AUTH)


# --- access and secret handling -------------------------------------------

def test_the_page_requires_auth(client):
    c, _ = client
    assert c.get('/feeds').status_code == 401


def test_the_page_is_never_cached(client, tmp_path):
    """It holds a credential; a disk cache would outlive the session."""
    c, tmp_path = client
    publish(tmp_path, "a-show")

    resp = get_page(c)

    assert resp.status_code == 200
    assert resp.headers['Cache-Control'] == 'no-store'


def test_the_url_cannot_leak_through_a_referer(client, tmp_path):
    c, tmp_path = client
    publish(tmp_path, "a-show")

    resp = get_page(c)

    assert resp.headers['Referrer-Policy'] == 'no-referrer'
    assert 'name="referrer" content="no-referrer"' in resp.get_data(as_text=True)


def test_the_page_permits_no_outbound_requests(client, tmp_path):
    """A third-party script on this page could read the URLs out of the DOM."""
    c, tmp_path = client
    publish(tmp_path, "a-show")

    csp = get_page(c).headers['Content-Security-Policy']

    assert "default-src 'none'" in csp
    assert 'https:' not in csp


def test_the_page_loads_nothing_from_a_cdn(client, tmp_path):
    """The dashboard pulls Chart.js from jsdelivr; this page must not."""
    c, tmp_path = client
    publish(tmp_path, "a-show")

    html = get_page(c).get_data(as_text=True)

    assert not re.search(r'<script[^>]+src=', html)
    assert not re.search(r'<link[^>]+href="https?://', html)
    assert 'cdn.' not in html


def test_a_hostile_feed_title_is_escaped(client, tmp_path):
    """Titles come from upstream feeds, which we do not control."""
    c, tmp_path = client
    publish(tmp_path, "a-show", title="Ads &amp; &lt;script&gt;alert(1)&lt;/script&gt;")

    html = get_page(c).get_data(as_text=True)

    assert '<script>alert(1)</script>' not in html
    assert '&lt;script&gt;' in html


# --- which feeds are listed ----------------------------------------------

def test_a_published_feed_is_listed_with_its_full_url(client, tmp_path):
    c, tmp_path = client
    publish(tmp_path, "odd-lots", title="Odd Lots", episodes=3)

    html = get_page(c).get_data(as_text=True)

    assert "Odd Lots" in html
    assert f"https://example.com/feeds/{SECRET}/odd-lots.xml" in html
    assert "3 ad-free episodes" in html
    assert "90 MB" in html


def test_a_locally_generated_feed_that_was_never_uploaded_is_not_listed(client, tmp_path):
    """The XML exists the moment a feed is generated, which is before it is
    uploaded and stays true if the upload failed. Subscribing to a URL whose
    object is not there is a 404, and teaches the app the feed is broken."""
    c, tmp_path = client
    publish(tmp_path, "uploaded", title="Uploaded Show")
    write_local_feed(tmp_path, "never-uploaded", "Never Uploaded")

    html = get_page(c).get_data(as_text=True)

    assert "Uploaded Show" in html
    assert "Never Uploaded" not in html
    assert "never-uploaded.xml" not in html


def test_a_tombstoned_feed_is_not_listed(client, tmp_path):
    c, tmp_path = client
    manifest = publish(tmp_path, "gone", title="Gone Show")
    manifest.record_delete([feed_key("gone")])
    manifest.save()

    html = get_page(c).get_data(as_text=True)

    assert "Gone Show" not in html


# --- empty states ---------------------------------------------------------

def test_publishing_switched_off_explains_itself(client, tmp_path):
    c, tmp_path = client
    server_module._publish_to_r2 = False
    publish(tmp_path, "a-show", title="A Show")

    html = get_page(c).get_data(as_text=True)

    assert "Publishing is switched off" in html
    assert "A Show" not in html
    assert SECRET not in html


def test_nothing_published_yet_explains_itself(client, tmp_path):
    c, _ = client

    html = get_page(c).get_data(as_text=True)

    assert "Nothing published yet" in html
    assert "r2ctl status" in html


# --- the Overcast shortcut ------------------------------------------------


def test_the_page_offers_the_overcast_shortcut(client, tmp_path):
    c, tmp_path = client
    publish(tmp_path, "a-show")

    html = get_page(c).get_data(as_text=True)

    assert "overcast://x-callback-url/add?url=" in html
    assert "Add to Overcast" in html
