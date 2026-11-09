from skipcastify.services import feed_meta
from tests.test_server import client  # noqa: F401  (fixture)

AUTH = {'Authorization': 'Basic dGVzdHVzZXI6dGVzdHBhc3M='}  # testuser:testpass
RSS = b"""<?xml version="1.0"?>
<rss xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"><channel>
<title>My Podcast</title><link>https://mypodcast.example/</link><itunes:author>Host</itunes:author>
<itunes:image href="https://img.example/show.jpg"/>
<item><title>Ep 1</title><link>https://mypodcast.example/ep1</link><itunes:episode>7</itunes:episode>
<description><![CDATA[<p>Guest <b>talk</b>.</p><p>Links: <a href="https://guest.example">guest</a>
<a href="javascript:alert(1)">bad</a> <script>alert(2)</script></p>Plain https://plain.example/x.]]></description></item>
</channel></rss>"""


def setup(tmp, monkeypatch):
    (tmp / 'feeds' / 'mypodcast.xml').write_text(
        '<rss><channel><title>t</title><link>https://upstream.example/rss</link></channel></rss>')
    (tmp / 'podcasts' / 'raw' / 'mypodcast' / 'mypodcast-ep_1.mp3').write_bytes(b'ID3')
    calls = []
    monkeypatch.setattr(feed_meta, '_fetch', lambda url: calls.append(url) or RSS)
    feed_meta._cache.clear()
    return calls


def test_meta_from_upstream_feed_cached(client, monkeypatch):  # noqa: F811
    c, tmp = client
    calls = setup(tmp, monkeypatch)
    assert c.get('/api/episode/mypodcast-ep_1/meta.json').status_code == 401
    for _ in range(2):
        m = c.get('/api/episode/mypodcast-ep_1/meta.json', headers=AUTH).get_json()
    assert calls == ['https://upstream.example/rss']  # second load came from the cache
    assert m['error'] is None
    assert m['show'] == {'title': 'My Podcast', 'website': 'https://mypodcast.example/', 'author': 'Host',
                         'image': 'https://img.example/show.jpg', 'description': []}
    e = m['episode']
    assert (e['title'], e['link'], e['episode']) == ('Ep 1', 'https://mypodcast.example/ep1', '7')
    flat = [r for b in e['notes'] for r in b]
    assert {'text': 'guest', 'href': 'https://guest.example'} in flat
    assert {'text': 'https://plain.example/x', 'href': 'https://plain.example/x'} in flat
    assert not any('javascript' in r.get('href', '') for r in flat)  # unsafe link kept as text only
    assert all(set(r) <= {'text', 'href'} for r in flat)  # no markup survives


def test_missing_episode_and_fetch_failure_are_soft(client, monkeypatch):  # noqa: F811
    c, tmp = client
    setup(tmp, monkeypatch)
    (tmp / 'podcasts' / 'raw' / 'mypodcast' / 'mypodcast-ep_9.mp3').write_bytes(b'ID3')
    m = c.get('/api/episode/mypodcast-ep_9/meta.json', headers=AUTH).get_json()
    assert m['episode'] is None and m['show']['title'] == 'My Podcast' and 'no longer' in m['error']

    def boom(url):
        raise OSError('down')
    monkeypatch.setattr(feed_meta, '_fetch', boom)
    feed_meta._cache.clear()
    m = c.get('/api/episode/mypodcast-ep_1/meta.json', headers=AUTH).get_json()
    assert m['show'] is None and 'OSError' in m['error']
    assert c.get('/api/episode/.hidden/meta.json', headers=AUTH).status_code == 404


def test_new_episode_refetches_but_only_after_a_while(client, monkeypatch):  # noqa: F811
    c, tmp = client
    calls = setup(tmp, monkeypatch)
    (tmp / 'podcasts' / 'raw' / 'mypodcast' / 'mypodcast-ep_2.mp3').write_bytes(b'ID3')
    c.get('/api/episode/mypodcast-ep_1/meta.json', headers=AUTH)
    c.get('/api/episode/mypodcast-ep_2/meta.json', headers=AUTH)  # missing, but the copy is fresh
    assert len(calls) == 1
    url, (ts, summary) = next(iter(feed_meta._cache.items()))
    feed_meta._cache[url] = (ts - feed_meta.MISS_REFRESH_S - 1, summary)  # copy now 10+ min old
    c.get('/api/episode/mypodcast-ep_1/meta.json', headers=AUTH)  # present: still served from cache
    assert len(calls) == 1
    c.get('/api/episode/mypodcast-ep_2/meta.json', headers=AUTH)  # missing and old: refetch
    assert len(calls) == 2


def test_concurrent_requests_share_one_fetch(monkeypatch):
    import threading
    import time as _time
    calls = []

    def slow(url):
        calls.append(url)
        _time.sleep(0.2)
        from tests.test_feed_meta import RSS
        return RSS
    monkeypatch.setattr(feed_meta, '_fetch', slow)
    feed_meta._cache.clear()
    threads = [threading.Thread(target=feed_meta.feed_summary, args=('https://u.example/rss', 'mypodcast')) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(calls) == 1


def test_feed_meta_endpoint(client, monkeypatch):  # noqa: F811
    c, tmp = client
    setup(tmp, monkeypatch)
    assert c.get('/api/feed/mypodcast/meta.json').status_code == 401
    m = c.get('/api/feed/mypodcast/meta.json', headers=AUTH).get_json()
    assert m['error'] is None and m['show']['title'] == 'My Podcast' and m['show']['image'] == 'https://img.example/show.jpg'
    assert c.get('/api/feed/nope/meta.json', headers=AUTH).get_json()['show'] is None
    assert c.get('/api/feed/.hidden/meta.json', headers=AUTH).status_code == 404


def test_signatures_carry_titles_dates_and_cut_time(client, monkeypatch):  # noqa: F811
    import json
    c, tmp = client
    (tmp / 'feeds' / 'mypodcast.xml').write_text(
        '<rss><channel><title>t</title><item><title>Ep 1</title>'
        '<pubDate>Mon, 02 Nov 2026 09:00:00 +0000</pubDate></item></channel></rss>')
    (tmp / 'metrics').mkdir()
    (tmp / 'metrics' / 'events.jsonl').write_text(json.dumps({
        'event': 'episode_complete', 'ts': '2026-11-02T10:00:00', 'feed_slug': 'mypodcast', 'episode_name': 'mypodcast-ep_1',
        'original_duration_s': 600.0, 'spans': [{'start_ms': 0, 'end_ms': 30000, 'label': 'ADVERTISEMENT'}]}) + '\n')
    [feed] = c.get('/api/stats.json', headers=AUTH).get_json()['signatures']
    [ep] = feed['episodes']
    assert (ep['title'], ep['released'][:10], ep['ad_s'], ep['duration_min']) == ('Ep 1', '2026-11-02', 30.0, 10.0)
    assert ep['processed'].startswith('2026-11-02') and ep['spans'] == [{'start': 0.0, 'end': 0.05}]


def test_a_feed_that_vanishes_mid_read_is_soft(client, monkeypatch):  # noqa: F811
    """stat() succeeding does not promise parse() can open the file.

    Cleanup can unlink a generated feed between the two calls, and
    episode_meta calls upstream_url outside its own try, so an OSError there
    reaches the route as a 500 rather than the soft "no upstream feed" answer.
    """
    c, tmp = client
    setup(tmp, monkeypatch)
    feed_meta._upstream.clear()

    real_parse = feed_meta.ET.parse

    def gone(path, *a, **kw):
        raise FileNotFoundError(2, 'No such file or directory', str(path))

    monkeypatch.setattr(feed_meta.ET, 'parse', gone)
    assert feed_meta.upstream_url(str(tmp), 'mypodcast') is None
    r = c.get('/api/episode/mypodcast-ep_1/meta.json', headers=AUTH)
    assert r.status_code == 200
    assert r.get_json()['error'] == 'no upstream feed known for this show'

    # A feed path that is not a readable file at all: EACCES, a stray directory.
    monkeypatch.setattr(feed_meta.ET, 'parse', real_parse)
    feed_meta._upstream.clear()
    (tmp / 'feeds' / 'mypodcast.xml').unlink()
    (tmp / 'feeds' / 'mypodcast.xml').mkdir()
    assert feed_meta.upstream_url(str(tmp), 'mypodcast') is None
    assert c.get('/api/episode/mypodcast-ep_1/meta.json', headers=AUTH).status_code == 200
