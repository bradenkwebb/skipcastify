import json

import pytest

from skipcastify.services import labeling
from skipcastify.services.ground_truth import GroundTruthStore
from tests.test_server import client  # noqa: F401  (fixture)

AUTH = {'Authorization': 'Basic dGVzdHVzZXI6dGVzdHBhc3M='}  # testuser:testpass
EP = 'mypodcast-ep_1'
SEGS = [{'start': 0, 'end': 4000, 'text': 'This episode is brought to you by Acme.'},
        {'start': 4000, 'end': 9000, 'text': 'Use code SKIP.'},
        {'start': 9000, 'end': 15000, 'text': 'Welcome back to the show.'}]


@pytest.fixture
def data(client):  # noqa: F811
    c, tmp = client
    (tmp / 'transcripts').mkdir()
    (tmp / 'transcripts' / f'{EP}_transcript.json').write_text(json.dumps(SEGS))
    (tmp / 'podcasts' / 'raw' / 'mypodcast' / f'{EP}.mp3').write_bytes(b'ID3' + b'\0' * 2000)
    (tmp / 'metrics').mkdir()
    (tmp / 'metrics' / 'events.jsonl').write_text(json.dumps({
        'event': 'episode_complete', 'ts': '2026-11-01T10:00:00', 'feed_slug': 'mypodcast',
        'episode_name': EP, 'spans': [{'start_ms': 4100, 'end_ms': 8000, 'label': 'ADVERTISEMENT'}]}) + '\n')
    return c, tmp


def put(c, spans, reviewed=False, headers=None):
    h = {**AUTH, 'X-Skipcastify': '1'} if headers is None else headers
    return c.put(f'/api/label/{EP}.json', json={'spans': spans, 'reviewed': reviewed}, headers=h)


def test_routes_require_auth(data):
    c, _ = data
    for url in ('/label', f'/label/{EP}', '/api/label/episodes.json', f'/api/label/{EP}.json', f'/api/label/{EP}/audio'):
        assert c.get(url).status_code == 401, url


def test_unknown_or_traversal_episode_404(data):
    c, _ = data
    for name in ('nope', '..%2F..%2Fetc', '.hidden'):
        assert c.get(f'/api/label/{name}.json', headers=AUTH).status_code == 404


def test_episode_list_and_load(data):
    c, _ = data
    eps = c.get('/api/label/episodes.json', headers=AUTH).get_json()
    assert [(e['name'], e['feed'], e['has_predictions'], e['labeled_spans']) for e in eps] == \
        [(EP, 'mypodcast', True, None)]
    d = c.get(f'/api/label/{EP}.json', headers=AUTH).get_json()
    assert d['segments'] == SEGS and d['has_audio'] and d['spans'] == [] and d['feed'] == 'mypodcast'
    assert d['predictions'] == [{'start_ms': 4100, 'end_ms': 8000, 'label': 'ADVERTISEMENT'}]


def test_save_round_trip_and_ground_truth_compat(data):
    c, tmp = data
    spans = [{'start_ms': 4000, 'end_ms': 9000, 'label': 'ad'},
             {'start_ms': 0, 'end_ms': 4000, 'label': 'self_promo'}]
    r = put(c, spans, reviewed=True)
    assert r.status_code == 200
    doc = json.loads(labeling.label_path(tmp, EP).read_text())
    assert doc['reviewed'] and doc['version'] == 2
    assert [s['label'] for s in doc['spans']] == ['self_promo', 'ad']  # sorted
    assert c.get('/api/label/episodes.json', headers=AUTH).get_json()[0]['reviewed']
    # The evaluator sees ads and self-promo (both should be cut), not intro/outro.
    labeling.save_labels(tmp, EP, spans + [{'start_ms': 9000, 'end_ms': 15000, 'label': 'intro_outro'}], True)
    gt = GroundTruthStore(str(tmp / 'labels' / 'ground_truth')).load_labels(EP)
    assert sorted((g.start_ms, g.end_ms) for g in gt) == [(0, 4000), (4000, 9000)]


def test_empty_reviewed_save_records_no_ads(data):
    c, tmp = data
    assert put(c, [], reviewed=True).status_code == 200
    assert json.loads(labeling.label_path(tmp, EP).read_text())['spans'] == []


@pytest.mark.parametrize('bad', [
    [{'start_ms': 5, 'end_ms': 1, 'label': 'ad'}],
    [{'start_ms': 0, 'end_ms': 1, 'label': 'ADVERTISEMENT'}],
    [{'start_ms': 0, 'end_ms': 1, 'label': 'uncertain'}],
    [{'start_ms': 0, 'label': 'ad'}],
    'nope',
])
def test_save_rejects_bad_spans(data, bad):
    c, tmp = data
    assert put(c, bad).status_code == 400
    assert not labeling.label_path(tmp, EP).exists()


def test_save_needs_csrf_header(data):
    c, tmp = data
    assert put(c, [], headers=AUTH).status_code == 403
    form = c.put(f'/api/label/{EP}.json', data='spans=', headers={**AUTH, 'X-Skipcastify': '1'})
    assert form.status_code == 403
    assert not labeling.label_path(tmp, EP).exists()


def test_audio_serves_raw_file_with_ranges(data):
    c, _ = data
    r = c.get(f'/api/label/{EP}/audio', headers={**AUTH, 'Range': 'bytes=0-9'})
    assert r.status_code == 206 and r.data == b'ID3' + b'\0' * 7 and r.mimetype == 'audio/mpeg'


def test_feed_inferred_without_events(data):
    c, tmp = data
    (tmp / 'metrics' / 'events.jsonl').unlink()
    d = c.get(f'/api/label/{EP}.json', headers=AUTH).get_json()
    assert d['has_audio'] and d['predictions'] == []


def test_list_uses_feed_release_dates_and_durations(data):
    c, tmp = data
    (tmp / 'transcripts' / 'mypodcast-ep_2_transcript.json').write_text(json.dumps(SEGS))
    (tmp / 'feeds' / 'mypodcast.xml').write_text(
        '<rss><channel><title>t</title>'
        '<item><title>Ep 1</title><pubDate>Mon, 02 Nov 2026 09:00:00 +0000</pubDate></item>'
        '<item><title>Ep 2</title><pubDate>Mon, 05 Jan 2026 09:00:00 GMT</pubDate></item>'
        '</channel></rss>')
    with open(tmp / 'metrics' / 'events.jsonl', 'a') as f:
        f.write(json.dumps({'event': 'episode_start', 'episode_name': EP, 'raw_duration_s': 3094.7}) + '\n')
    eps = c.get('/api/label/episodes.json', headers=AUTH).get_json()
    assert [(e['name'], e['released'][:10], e['duration_s']) for e in eps] == \
        [(EP, '2026-11-02', 3094.7), ('mypodcast-ep_2', '2026-01-05', None)]


def test_unknown_release_date_falls_back_to_transcribed(data):
    c, _ = data
    e = c.get('/api/label/episodes.json', headers=AUTH).get_json()[0]
    assert e['released'] is None and e['date'] == e['transcribed']


class TestLabelSource:
    SPAN = [{'start_ms': 0, 'end_ms': 9000, 'label': 'ad'}]

    def test_hand_labels_are_the_default(self, tmp_path):
        doc = labeling.save_labels(tmp_path, EP, self.SPAN, reviewed=True)
        assert doc['source'] == labeling.MANUAL

    def test_a_derived_source_is_recorded(self, tmp_path):
        doc = labeling.save_labels(tmp_path, EP, self.SPAN, reviewed=True,
                                   source=labeling.DAI_DIFF)
        assert doc['source'] == labeling.DAI_DIFF
        assert json.loads(labeling.label_path(tmp_path, EP).read_text())['source'] \
            == labeling.DAI_DIFF

    def test_an_unknown_source_is_refused(self, tmp_path):
        with pytest.raises(ValueError):
            labeling.save_labels(tmp_path, EP, self.SPAN, reviewed=True, source='guess')

    def test_a_file_without_a_source_reads_as_a_hand_label(self):
        assert labeling.label_source({'spans': []}) == labeling.MANUAL

    def test_an_unrecognised_stored_source_reads_as_a_hand_label(self):
        assert labeling.label_source({'source': 'something_else'}) == labeling.MANUAL
