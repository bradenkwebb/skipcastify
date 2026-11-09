"""Editing the subscription OPML from /settings, previewing a feed, and the
guard on server-side fetches of user-supplied URLs."""

import json
from unittest.mock import MagicMock, patch

import pytest

from skipcastify.services import subscriptions
from tests.test_settings import AUTH, WRITE, client, state  # noqa: F401

NOW = 1_762_000_000  # fixed clock for the cadence estimate


def feed_xml(n=6, every_days=7, length=60_000_000):
    from email.utils import formatdate
    items = "".join(
        f"<item><title>Ep {i}</title><pubDate>{formatdate(NOW - i * every_days * 86400)}</pubDate>"
        f'<enclosure url="https://cdn.example.com/{i}.mp3" length="{length}" type="audio/mpeg"/></item>'
        for i in range(n))
    return f'<rss version="2.0"><channel><title>A Show!</title>{items}</channel></rss>'.encode()


def test_opml_round_trips(tmp_path):
    path = tmp_path / "subs.opml"
    subs = [{"title": "What's Left", "url": "https://x.example/feed?a=1&b=2"}]
    subscriptions.write(path, subs)
    assert subscriptions.read(path) == subs


@pytest.mark.parametrize("url", ["ftp://example.com/feed", "http://127.0.0.1/feed",
                                 "http://10.0.0.5/feed", "http://localhost:5000/feed", "not a url"])
def test_non_public_urls_are_refused(url):
    with pytest.raises(ValueError):
        subscriptions.check_url(url)


def test_a_redirect_to_a_private_address_is_refused():
    public = [(2, 1, 6, "", ("93.184.216.34", 0))]
    redirect = MagicMock(is_redirect=True, headers={"Location": "http://127.0.0.1/admin"})
    redirect.__enter__ = lambda self: self
    redirect.__exit__ = lambda *a: None
    with patch("socket.getaddrinfo", side_effect=lambda h, p: public if h == "feeds.example.com"
               else [(2, 1, 6, "", ("127.0.0.1", 0))]), \
         patch("requests.get", return_value=redirect):
        with pytest.raises(ValueError):
            subscriptions.fetch("https://feeds.example.com/rss")


def test_summary_estimates_r2_use_from_cadence_and_size():
    s = subscriptions.summarize(feed_xml(n=20, every_days=7), now=NOW)
    assert s["slug"] == "a-show" and s["episodes"] == 20
    assert s["per_window"] == pytest.approx(13 * 14 / 90, abs=0.1)   # 13 weekly eps in 90 days
    assert s["est_bytes"] == pytest.approx(s["per_window"] * 60_000_000, rel=0.02)


def post(c, path, body):
    return c.post(path, headers=WRITE, data=json.dumps(body), content_type="application/json")


def test_add_remove_and_undo_are_logged(client):  # noqa: F811
    c, tmp = client
    subscriptions.write(tmp / "subs.opml", [{"title": "Odd Lots", "url": "https://a.example/odd"}])
    with patch.object(subscriptions, "check_url", side_effect=lambda u: u):
        r = post(c, "/api/settings/subscriptions/add.json",
                 {"url": "https://b.example/new", "title": "New Show", "version": state(c)["subs_version"]})
    assert r.status_code == 200
    assert [s["title"] for s in state(c)["subscriptions"]] == ["Odd Lots", "New Show"]

    r = post(c, "/api/settings/subscriptions/remove.json",
             {"url": "https://a.example/odd", "version": state(c)["subs_version"]})
    assert r.status_code == 200
    s = state(c)
    assert [x["title"] for x in s["subscriptions"]] == ["New Show"]
    assert s["changes"][0]["summary"] == ["unsubscribed: Odd Lots"]

    r = post(c, f"/api/settings/undo/{s['changes'][0]['id']}.json",
             {"versions": {"subscriptions": s["subs_version"]}})
    assert r.status_code == 200
    assert [x["title"] for x in state(c)["subscriptions"]] == ["Odd Lots", "New Show"]


def test_a_stale_list_version_is_refused(client):  # noqa: F811
    c, tmp = client
    subscriptions.write(tmp / "subs.opml", [{"title": "Odd Lots", "url": "https://a.example/odd"}])
    r = post(c, "/api/settings/subscriptions/remove.json", {"url": "https://a.example/odd", "version": "none"})
    assert r.status_code == 409


def test_subscription_writes_refuse_cross_site_requests(client):  # noqa: F811
    c, tmp = client
    r = c.post("/api/settings/subscriptions/preview.json", headers={**AUTH, "X-Skipcastify": "1",
               "Sec-Fetch-Site": "cross-site"}, data=json.dumps({"url": "https://x"}),
               content_type="application/json")
    assert r.status_code == 403


def test_overcast_export_lists_only_unsubscribed_shows(client):  # noqa: F811
    c, tmp = client
    subscriptions.write(tmp / "subs.opml", [{"title": "Odd Lots", "url": "https://a.example/odd"}])
    subscriptions.write(tmp / "upstream.opml", [{"title": "Odd Lots", "url": "https://a.example/odd"},
                                                {"title": "Other", "url": "https://c.example/other"}])
    assert [u["title"] for u in state(c)["upstream"]] == ["Other"]


def test_a_new_subscription_gets_a_policy_row_before_it_is_published(client):  # noqa: F811
    c, tmp = client
    subscriptions.write(tmp / "subs.opml", [{"title": "Brand New!", "url": "https://n.example/rss"}])
    assert {"slug": "brand-new", "title": "Brand New!"} in state(c)["feeds"]
