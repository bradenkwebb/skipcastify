import json

from skipcastify.services import labeling
from tests.test_server import client  # noqa: F401  (fixture)

AUTH = {'Authorization': 'Basic dGVzdHVzZXI6dGVzdHBhc3M='}  # testuser:testpass
EP = 'mypodcast-ep_1'
SEGS = [{'start': 0, 'end': 4000, 'text': 'This episode is brought to you by Acme.'},
        {'start': 4000, 'end': 9000, 'text': 'Use code SKIP.'},
        {'start': 9000, 'end': 15000, 'text': 'Welcome back to the show.'}]


def ev(event, ts, **kw):
    return json.dumps({'event': event, 'ts': ts, 'feed_slug': 'mypodcast', 'episode_name': EP, **kw}) + '\n'


def setup(tmp, with_section=True):
    (tmp / 'transcripts').mkdir()
    (tmp / 'transcripts' / f'{EP}_transcript.json').write_text(json.dumps(SEGS))
    (tmp / 'podcasts' / 'raw' / 'mypodcast' / f'{EP}.mp3').write_bytes(b'ID3')
    (tmp / 'metrics').mkdir()
    (tmp / 'metrics' / 'events.jsonl').write_text(
        ev('episode_start', '2026-11-01T10:00:00', raw_duration_s=15.0)
        + ev('llm_section', '2026-11-01T10:00:05', cost_usd=0.001, prompt_tokens=100)
        + ev('episode_complete', '2026-11-01T10:00:09', original_duration_s=15.0, ads_removed_s=9.0, span_count=1,
             spans=[{'start_ms': 500, 'end_ms': 8000, 'label': 'ADVERTISEMENT'}])
        + ev('episode_start', '2026-11-02T10:00:00')  # a retry that never finished
        + ev('llm_section', '2026-11-02T10:00:05', cost_usd=0.002, prompt_tokens=200)
        + json.dumps({'event': 'llm_section', 'episode_name': 'other', 'cost_usd': 9}) + '\n')
    if with_section:
        d = tmp / 'podcasts' / 'raw' / 'mypodcast' / 'transcripts'
        d.mkdir()
        (d / f'{EP}_section_0_response.txt').write_text(
            'Raw LLM Response for section 0 [0ms-600000ms]:\n' + '=' * 100 + '\n'
            + json.dumps([{'start_ms': 500, 'end_ms': 8000, 'label': 'ADVERTISEMENT', 'rationale': 'Acme read with a code.'}])
            + '\n' + '=' * 100 + '\n')


def test_routes_require_auth(client):  # noqa: F811
    c, tmp = client
    setup(tmp)
    for url in (f'/dashboard/episode/{EP}', f'/api/episode/{EP}.json'):
        assert c.get(url).status_code == 401
        assert c.get(url, headers=AUTH).status_code == 200


def test_unknown_or_traversal_404(client):  # noqa: F811
    c, tmp = client
    setup(tmp)
    for name in ('nope', '..%2F..%2Fetc', '.hidden'):
        assert c.get(f'/api/episode/{name}.json', headers=AUTH).status_code == 404
    assert c.get('/dashboard/episode/.hidden', headers=AUTH).status_code == 404


def test_detail_cuts_runs_and_reasons(client):  # noqa: F811
    c, tmp = client
    setup(tmp)
    d = c.get(f'/api/episode/{EP}.json', headers=AUTH).get_json()
    assert d['feed'] == 'mypodcast' and d['has_audio'] and d['has_transcript']
    assert [(r['completed'] is not None, r['llm_calls'], r['cost_usd']) for r in d['runs']] == [(False, 1, 0.002), (True, 1, 0.001)]
    assert d['latest']['ads_removed_s'] == 9.0 and d['latest']['cost_usd'] == 0.001
    [cut] = d['cuts']
    assert cut['rationales'] == ['Acme read with a code.']
    assert (cut['first_line'], cut['last_line']) == ('This episode is brought to you by Acme.', 'Use code SKIP.')
    assert cut['lines'] == [{'start_ms': 0, 'text': 'This episode is brought to you by Acme.'}, {'start_ms': 4000, 'text': 'Use code SKIP.'}]
    assert d['labels'] is None and d['score'] is None


def test_score_only_when_reviewed(client):  # noqa: F811
    c, tmp = client
    setup(tmp, with_section=False)
    labeling.save_labels(tmp, EP, [{'start_ms': 0, 'end_ms': 15000, 'label': 'ad'}], reviewed=False)
    d = c.get(f'/api/episode/{EP}.json', headers=AUTH).get_json()
    assert d['labels']['reviewed'] is False and d['score'] is None and d['cuts'][0]['rationales'] == []
    labeling.save_labels(tmp, EP, [{'start_ms': 0, 'end_ms': 15000, 'label': 'ad'}], reviewed=True)
    s = c.get(f'/api/episode/{EP}.json', headers=AUTH).get_json()['score']
    assert (s['missed_ms'], s['wrong_ms']) == (6000, 0)  # the cut touches lines 1-2; line 3 was missed


def test_title_from_feed(client):  # noqa: F811
    c, tmp = client
    setup(tmp)
    (tmp / 'feeds' / 'mypodcast.xml').write_text(
        '<rss><channel><title>t</title><item><title>Ep 1</title></item></channel></rss>')
    assert c.get(f'/api/episode/{EP}.json', headers=AUTH).get_json()['title'] == 'Ep 1'


def test_recent_episodes_carry_feed_titles(client):  # noqa: F811
    c, tmp = client
    setup(tmp)
    (tmp / 'feeds' / 'mypodcast.xml').write_text(
        '<rss><channel><title>t</title><item><title>Ep 1</title></item></channel></rss>')
    [row] = c.get('/api/stats.json', headers=AUTH).get_json()['recent_episodes']
    assert (row['episode'], row['title']) == (EP, 'Ep 1')


def test_root_redirects_to_dashboard(client):  # noqa: F811
    c, _ = client
    r = c.get('/')
    assert r.status_code == 302 and r.headers['Location'].endswith('/dashboard')
