"""An episode that keeps killing runs is skipped instead of blocking the rest."""

import pytest

from pipeline import MAX_ATTEMPTS
from skipcastify.services.state_manager import StateManager
from tests.test_pipeline_alerts import make_pipeline, opml, sent  # noqa: F401


@pytest.fixture
def episodes(tmp_path):
    feed = tmp_path / "podcasts" / "raw" / "show"
    feed.mkdir(parents=True)
    paths = [feed / "a.mp3", feed / "b.mp3"]
    for p in paths:
        p.write_bytes(b"")
    return paths


def test_attempts_count_until_finished(tmp_path, episodes):
    sm = StateManager(str(tmp_path))
    assert sm.start_attempt(episodes[0]) == 1
    assert sm.start_attempt(episodes[0]) == 2
    sm.finish_attempt(episodes[0])
    assert sm.attempts(episodes[0]) == 0


def run_processing(tmp_path, opml, sent, fail_on):
    pipeline = make_pipeline(tmp_path, opml, sent)
    pipeline.processing_enabled = True
    done = []

    def process(path, _sm):
        if path.name == fail_on:
            raise RuntimeError("killed")
        done.append(path.name)
    pipeline.processor.process = process
    try:
        pipeline.run()
    except RuntimeError:
        pass
    return done


def test_a_failing_episode_is_skipped_after_max_attempts(tmp_path, opml, sent, episodes, monkeypatch):
    monkeypatch.setenv("EPISODE_COOLDOWN_S", "0")
    for _ in range(MAX_ATTEMPTS):
        run_processing(tmp_path, opml, sent, fail_on="a.mp3")
    assert any("keeps failing" in m["title"] for m in sent.sent)
    done = run_processing(tmp_path, opml, sent, fail_on="a.mp3")
    assert "b.mp3" in done
