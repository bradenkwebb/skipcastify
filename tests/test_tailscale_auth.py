"""Tailscale identity auth: an allowlisted Tailscale-User-Login from loopback
(tailscale serve), optionally on a named device, skips Basic auth; anything
else still needs the password."""

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
    monkeypatch.setattr(server_module, "_tailscale_users", {ME: None})
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
    monkeypatch.setattr(server_module, "_tailscale_users", {})
    assert get(client, ME).status_code == 401
    assert get(client, headers=BASIC).status_code == 200


def test_changes_are_logged_under_the_tailnet_login(client):
    v = get(client, ME).get_json()["version"]
    r = client.put('/api/settings/policy.json', environ_base=LOOPBACK,
                   headers={"Tailscale-User-Login": ME, "X-Skipcastify": "1", "Sec-Fetch-Site": "same-origin"},
                   data=json.dumps({"policy": {"default": ["ad"], "feeds": {}}, "version": v}),
                   content_type="application/json")
    assert r.status_code == 200 and r.get_json()["change"]["user"] == ME


# --- device-scoped entries (login/device) ----------------------------------

PHONE_IP, LAPTOP_IP = "100.64.0.10", "100.64.0.11"
WHOIS = {PHONE_IP: (ME, "my-phone"), LAPTOP_IP: (ME, "shared-laptop")}


@pytest.fixture
def phone_only(client, monkeypatch):
    monkeypatch.setattr(server_module, "_tailscale_users", {ME: {"my-phone"}})
    monkeypatch.setattr(server_module, "_whois", lambda ip: WHOIS.get(ip))
    return client


def from_ip(c, ip, login=ME):
    return get(c, login, headers={"X-Forwarded-For": ip})


def test_the_allowed_device_needs_no_password(phone_only):
    assert from_ip(phone_only, PHONE_IP).status_code == 200


def test_another_device_of_the_same_user_needs_the_password(phone_only):
    assert from_ip(phone_only, LAPTOP_IP).status_code == 401


def test_no_or_unknown_client_ip_needs_the_password(phone_only):
    assert get(phone_only, ME).status_code == 401
    assert from_ip(phone_only, "100.64.0.99").status_code == 401


def test_whois_must_agree_with_the_login_header(phone_only, monkeypatch):
    monkeypatch.setattr(server_module, "_whois", lambda ip: ("other@github", "my-phone"))
    assert from_ip(phone_only, PHONE_IP).status_code == 401


def test_changes_are_logged_with_the_device(phone_only):
    v = from_ip(phone_only, PHONE_IP).get_json()["version"]
    r = phone_only.put('/api/settings/policy.json', environ_base=LOOPBACK,
                       headers={"Tailscale-User-Login": ME, "X-Forwarded-For": PHONE_IP,
                                "X-Skipcastify": "1", "Sec-Fetch-Site": "same-origin"},
                       data=json.dumps({"policy": {"default": ["ad"], "feeds": {}}, "version": v}),
                       content_type="application/json")
    assert r.get_json()["change"]["user"] == f"{ME}/my-phone"


def test_allowlist_parsing():
    parse = server_module._parse_tailscale_allow
    assert parse("") == {}
    assert parse(" A@github/Phone , a@github/tablet, b@x ") == {"a@github": {"phone", "tablet"}, "b@x": None}
    assert parse("a@github/phone, a@github") == {"a@github": None}      # bare login widens
    assert parse("a@github, a@github/phone") == {"a@github": None}


def test_whois_rejects_non_ips_without_running_anything(monkeypatch):
    monkeypatch.setattr(server_module.subprocess, "run", lambda *a, **k: pytest.fail("ran tailscale"))
    assert server_module._whois("100.64.0.1; rm -rf /") is None
    assert server_module._whois("") is None
