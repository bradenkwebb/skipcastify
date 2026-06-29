import pytest
import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Set env vars before importing server, which reads them at module level
os.environ.update({
    'FEED_USERNAME': 'testuser',
    'FEED_PASSWORD': 'testpass',
    'EPISODE_TOKEN': 'testtoken',
    'DATA_DIR': '',
})

import server as server_module


@pytest.fixture
def client(tmp_path):
    (tmp_path / 'feeds').mkdir()
    (tmp_path / 'podcasts' / 'raw' / 'mypodcast').mkdir(parents=True)
    (tmp_path / 'podcasts' / 'processed' / 'mypodcast').mkdir(parents=True)

    server_module.data_dir = str(tmp_path)
    server_module._feed_username = 'testuser'
    server_module._feed_password = 'testpass'
    server_module._episode_token = 'testtoken'
    server_module.app.config['TESTING'] = True

    with server_module.app.test_client() as c:
        yield c, tmp_path


def test_feed_requires_auth(client):
    """Feed route must reject unauthenticated requests."""
    c, _ = client
    assert c.get('/feeds/mypodcast.xml').status_code == 401


def test_episode_wrong_token_returns_403_not_404(client):
    """Wrong token must return 403, even when the file exists — so callers can't
    distinguish valid vs invalid paths by probing with a bad token."""
    c, tmp_path = client
    (tmp_path / 'podcasts' / 'raw' / 'mypodcast' / 'ep.mp3').write_bytes(b'audio')
    assert c.get('/episodes/wrongtoken/mypodcast/ep.mp3').status_code == 403


def test_episode_served_from_raw_when_no_processed(client):
    """Episodes not yet processed should be served from raw/."""
    c, tmp_path = client
    (tmp_path / 'podcasts' / 'raw' / 'mypodcast' / 'ep.mp3').write_bytes(b'raw audio')
    resp = c.get('/episodes/testtoken/mypodcast/ep.mp3')
    assert resp.status_code == 200
    assert resp.data == b'raw audio'


def test_processed_takes_precedence_over_raw(client):
    """processed/ version must be served even when raw/ copy also exists."""
    c, tmp_path = client
    (tmp_path / 'podcasts' / 'raw' / 'mypodcast' / 'ep.mp3').write_bytes(b'raw audio')
    (tmp_path / 'podcasts' / 'processed' / 'mypodcast' / 'ep.mp3').write_bytes(b'clean audio')
    resp = c.get('/episodes/testtoken/mypodcast/ep.mp3')
    assert resp.status_code == 200
    assert resp.data == b'clean audio'
