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
