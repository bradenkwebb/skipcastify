"""GET /api/show/<slug>.json and the web show page."""

from skipcastify.services import subscriptions
from tests.test_episode_detail import EP, setup
from tests.test_settings import AUTH, client  # noqa: F401  (fixture)


def subscribe(tmp, title="MyPodcast", url="https://feeds.example.com/my.xml"):  # slug: mypodcast
    subscriptions.write(tmp / "subs.opml", [{"title": title, "url": url}])


def test_needs_auth_and_rejects_unknown_or_unsafe_slugs(client):  # noqa: F811
    c, tmp = client
    assert c.get('/api/show/mypodcast.json').status_code == 401
    assert c.get('/api/show/nobody.json', headers=AUTH).status_code == 404
    assert c.get('/api/show/..%2Fetc.json', headers=AUTH).status_code == 404
    assert c.get('/dashboard/show/..%2Fetc', headers=AUTH).status_code == 404


def test_a_subscribed_show_with_nothing_processed_yet(client):  # noqa: F811
    c, tmp = client
    subscribe(tmp, title="Brand New!", url="https://feeds.example.com/new.xml")
    d = c.get('/api/show/brand-new.json', headers=AUTH).get_json()
    assert d["title"] == "Brand New!" and d["subscribed"]
    assert d["stats"] is None and d["recent_episodes"] == [] and d["signature"] == []
    assert d["status"]["published"] is False
    assert d["policy"] == {"cut": ["ad", "self_promo"], "uses_default": True}


def test_a_show_with_history(client):  # noqa: F811
    c, tmp = client
    (tmp / "podcasts" / "raw" / "mypodcast").mkdir(parents=True)
    setup(tmp)
    subscribe(tmp)
    d = c.get('/api/show/mypodcast.json', headers=AUTH).get_json()
    assert d["stats"]["episodes"] == 1 and d["stats"]["total_ad_minutes"] > 0
    assert [e["episode"] for e in d["recent_episodes"]] == [EP]
    assert d["signature"][0]["episode"] == EP and d["signature"][0]["spans"]


def test_a_per_show_policy_shows_as_its_own(client):  # noqa: F811
    c, tmp = client
    subscribe(tmp)
    (tmp / "policy.yaml").write_text("feeds:\n  mypodcast:\n    cut: [ad]\n")
    d = c.get('/api/show/mypodcast.json', headers=AUTH).get_json()
    assert d["policy"] == {"cut": ["ad"], "uses_default": False}


def test_the_page_embeds_its_slug(client):  # noqa: F811
    c, tmp = client
    r = c.get('/dashboard/show/mypodcast', headers=AUTH)
    assert r.status_code == 200 and b'"mypodcast"' in r.data
