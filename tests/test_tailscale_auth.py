"""Tailscale identity auth: an allowlisted Tailscale-User-Login from loopback
(tailscale serve) skips Basic auth; anything else still needs the password."""

import json
import os

import pytest

os.environ.update({'FEED_USERNAME': 'testuser', 'FEED_PASSWORD': 'testpass',
                   'EPISODE_TOKEN': 'testtoken', 'DATA_DIR': ''})

import server as server_module  # noqa: E402

ME = "me@example.com"
LOOPBACK = {"REMOTE_ADDR": "127.0.0.1"}
BASIC = {'Authorization': 'Basic dGVzdHVzZXI6dGVzdHBhc3M='}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CUT_POLICY", str(tmp_path / "policy.yaml"))
    monkeypatch.setenv("SUBSCRIPTIONS", str(tmp_path / "subs.opml"))
    monkeypatch.setattr(server_module, "data_dir", str(tmp_path))
    monkeypatch.setattr(server_module, "_feed_username", "testuser")
    monkeypatch.setattr(server_module, "_feed_password", "testpass")
    monkeypatch.setattr(server_module, "_tailscale_users", {ME})
    server_module.app.config['TESTING'] = True
    with server_module.app.test_client() as c:
        yield c


def get(c, login=None, env=LOOPBACK, headers=None):
    h = {**(headers or {}), **({"Tailscale-User-Login": login} if login else {})}
    return c.get('/api/settings.json', headers=h, environ_base=env)


def test_allowlisted_login_from_loopback_needs_no_password(client):
    assert get(client, ME).status_code == 200


def test_login_matching_is_case_insensitive(client):
    assert get(client, ME.upper()).status_code == 200


def test_unlisted_login_still_needs_the_password(client):
    assert get(client, "someone@else.com").status_code == 401
    assert get(client, "someone@else.com", headers=BASIC).status_code == 200


def test_header_is_ignored_unless_the_request_is_from_loopback(client):
    """Only tailscale serve, on this host, can have set it."""
    assert get(client, ME, env={"REMOTE_ADDR": "100.64.0.7"}).status_code == 401


def test_no_allowlist_means_basic_auth_only(client, monkeypatch):
    monkeypatch.setattr(server_module, "_tailscale_users", set())
    assert get(client, ME).status_code == 401
    assert get(client, headers=BASIC).status_code == 200


def test_changes_are_logged_under_the_tailnet_login(client):
    v = get(client, ME).get_json()["version"]
    r = client.put('/api/settings/policy.json', environ_base=LOOPBACK,
                   headers={"Tailscale-User-Login": ME, "X-Skipcastify": "1", "Sec-Fetch-Site": "same-origin"},
                   data=json.dumps({"policy": {"default": ["ad"], "feeds": {}}, "version": v}),
                   content_type="application/json")
    assert r.status_code == 200 and r.get_json()["change"]["user"] == ME
