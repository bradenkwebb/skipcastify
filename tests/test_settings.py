"""Dashboard editing of policy.yaml: validation, conflicts, the change log,
undo, retrying skipped episodes, and refusing cross-site writes."""

import json
import os

import pytest

os.environ.update({'FEED_USERNAME': 'testuser', 'FEED_PASSWORD': 'testpass',
                   'EPISODE_TOKEN': 'testtoken', 'DATA_DIR': ''})

import server as server_module  # noqa: E402
from skipcastify.services import cut_policy, settings  # noqa: E402
from skipcastify.services.state_manager import StateManager  # noqa: E402

AUTH = {'Authorization': 'Basic dGVzdHVzZXI6dGVzdHBhc3M='}
WRITE = {**AUTH, 'X-Skipcastify': '1', 'Sec-Fetch-Site': 'same-origin'}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CUT_POLICY", str(tmp_path / "policy.yaml"))
    monkeypatch.setenv("SUBSCRIPTIONS", str(tmp_path / "subs.opml"))
    monkeypatch.setenv("UPSTREAM_OPML", str(tmp_path / "upstream.opml"))
    server_module.data_dir = str(tmp_path)
    server_module._feed_username, server_module._feed_password = 'testuser', 'testpass'
    server_module.app.config['TESTING'] = True
    with server_module.app.test_client() as c:
        yield c, tmp_path


def state(c):
    return c.get('/api/settings.json', headers=AUTH).get_json()


def put(c, policy, version, headers=WRITE):
    return c.put('/api/settings/policy.json', headers=headers,
                 data=json.dumps({'policy': policy, 'version': version}), content_type='application/json')


def test_needs_auth(client):
    c, _ = client
    assert c.get('/settings').status_code == 401
    assert c.get('/api/settings.json').status_code == 401


def test_save_writes_policy_and_logs_the_change(client):
    c, tmp = client
    s = state(c)
    assert s['version'] == 'none' and s['policy']['default'] == ['ad', 'self_promo']

    r = put(c, {'default': ['ad', 'self_promo'], 'feeds': {'mormon-stories-podcast': ['ad']}}, s['version'])

    assert r.status_code == 200
    assert cut_policy.cut_labels('mormon-stories-podcast', tmp / 'policy.yaml') == {'ad'}
    (entry,) = state(c)['changes']
    assert entry['user'] == 'testuser'
    assert entry['summary'] == ['mormon-stories-podcast: default → ad']


def test_a_stale_version_is_refused(client):
    c, _ = client
    v = state(c)['version']
    put(c, {'default': ['ad'], 'feeds': {}}, v)
    r = put(c, {'default': ['self_promo'], 'feeds': {}}, v)
    assert r.status_code == 409


def test_invalid_labels_are_refused(client):
    c, _ = client
    r = put(c, {'default': ['ads'], 'feeds': {}}, state(c)['version'])
    assert r.status_code == 400


def test_undo_restores_the_previous_policy_as_a_new_change(client):
    c, tmp = client
    put(c, {'default': ['ad'], 'feeds': {}}, state(c)['version'])
    s = state(c)
    r = c.post(f"/api/settings/undo/{s['changes'][0]['id']}.json", headers=WRITE,
               data=json.dumps({'versions': {'policy': s['version']}}), content_type='application/json')
    assert r.status_code == 200
    assert cut_policy.cut_labels(None, tmp / 'policy.yaml') == {'ad', 'self_promo'}
    assert len(state(c)['changes']) == 2


@pytest.mark.parametrize('headers', [
    AUTH,                                                # no custom header
    {**AUTH, 'X-Skipcastify': '1', 'Sec-Fetch-Site': 'cross-site'},
    {**AUTH, 'X-Skipcastify': '1', 'Sec-Fetch-Site': 'same-site'},
])
def test_cross_site_writes_are_refused(client, headers):
    c, tmp = client
    assert put(c, {'default': ['ad'], 'feeds': {}}, 'none', headers=headers).status_code == 403
    assert not (tmp / 'policy.yaml').exists()


def test_a_broken_hand_edit_is_shown_not_overwritten(client):
    c, tmp = client
    (tmp / 'policy.yaml').write_text("default: {cut: [nope]}\n")
    s = state(c)
    assert 'invalid' in s['error']


def test_retry_clears_only_skipped_episodes(client):
    c, tmp = client
    sm = StateManager(str(tmp))
    ep = sm.raw_dir / 'show' / 'ep.mp3'
    ep.parent.mkdir(parents=True)
    ep.write_bytes(b'')
    sm.start_attempt(ep)

    def retry():
        return c.post('/api/settings/retry.json', headers=WRITE,
                      data=json.dumps({'episode': 'show/ep.mp3'}), content_type='application/json')
    assert retry().status_code == 404          # one failure is not skipped yet
    sm.start_attempt(ep)
    assert state(c)['skipped'] == [{'episode': 'show/ep.mp3', 'attempts': 2}]
    assert retry().status_code == 200
    assert sm.attempts(ep) == 0


def test_written_file_round_trips(tmp_path):
    path = tmp_path / 'policy.yaml'
    settings.save(str(tmp_path), {'default': ['ad'], 'feeds': {'x': []}}, 'none', None, path=path)
    assert settings.snapshot(path) == {'default': ['ad'], 'feeds': {'x': []}}
    assert path.read_text().startswith('# Edited from the dashboard')


def test_a_missing_subscriptions_file_means_no_subscriptions(tmp_path):
    from skipcastify.utils.utils import load_subscriptions
    assert load_subscriptions(str(tmp_path / "subscriptions.opml")) == []


def test_subscriptions_must_be_opml(tmp_path):
    from skipcastify.utils.utils import load_subscriptions
    path = tmp_path / "subs.yaml"
    path.write_text("subscriptions: []\n")
    with pytest.raises(ValueError, match="opml"):
        load_subscriptions(str(path))
