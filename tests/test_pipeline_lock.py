import importlib.util
import sys
import threading
import time
from pathlib import Path

import pytest

import pipeline
from skipcastify.utils.lock import LockHeld, pipeline_lock

ROOT = Path(__file__).resolve().parents[1]


def _hold_lock(data_dir, release: threading.Event, held: threading.Event):
    with pipeline_lock(data_dir):
        held.set()
        release.wait(5)


@pytest.fixture
def held_lock(tmp_path):
    """Hold the lock from another thread (flock conflicts per open file)."""
    release, held = threading.Event(), threading.Event()
    t = threading.Thread(target=_hold_lock, args=(tmp_path, release, held))
    t.start()
    assert held.wait(5)
    yield release
    release.set()
    t.join(5)


def test_second_holder_is_refused(tmp_path, held_lock):
    with pytest.raises(LockHeld):
        with pipeline_lock(tmp_path):
            pass


def test_released_after_an_exception(tmp_path):
    with pytest.raises(RuntimeError):
        with pipeline_lock(tmp_path):
            raise RuntimeError
    with pipeline_lock(tmp_path):
        pass


def test_wait_blocks_until_released(tmp_path, held_lock):
    threading.Timer(0.3, held_lock.set).start()
    start = time.monotonic()
    with pipeline_lock(tmp_path, wait=True):
        assert time.monotonic() - start >= 0.25


def test_pipeline_main_skips_when_locked(tmp_path, held_lock, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SUBSCRIPTIONS", str(tmp_path / "none.opml"))
    monkeypatch.setattr(pipeline, "load_dotenv", lambda: None)
    monkeypatch.setattr(pipeline, "setup_logging", lambda d: None)
    ran = []
    monkeypatch.setattr(pipeline, "Pipeline", lambda *a, **k: ran.append(1))
    assert pipeline.main() == 0
    assert ran == []


def _load_process_episode(monkeypatch, data_dir):
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    spec = importlib.util.spec_from_file_location(
        "process_episode_under_test", ROOT / "scripts" / "process_episode.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "setup_logging", lambda d: None)
    return mod


def test_process_episode_waits_for_the_pipeline(tmp_path, held_lock, monkeypatch):
    mod = _load_process_episode(monkeypatch, tmp_path)
    ep = tmp_path / "ep.mp3"
    ep.write_bytes(b"")
    started = []

    class FakeProcessor:
        def __init__(self, data_dir):
            pass

        def process(self, path, state):
            started.append(time.monotonic())

    monkeypatch.setattr(mod, "AudioProcessor", FakeProcessor)
    monkeypatch.setattr(sys, "argv", ["process_episode.py", str(ep)])
    released_at = []

    def release():
        released_at.append(time.monotonic())
        held_lock.set()

    threading.Timer(0.3, release).start()
    mod.main()
    assert started and started[0] >= released_at[0]


def test_process_episode_skips_an_episode_removed_while_waiting(tmp_path, monkeypatch):
    mod = _load_process_episode(monkeypatch, tmp_path)
    ep = tmp_path / "ep.mp3"
    ep.write_bytes(b"")
    calls = []

    class FakeProcessor:
        def __init__(self, data_dir):
            pass

        def process(self, path, state):
            calls.append(path)

    monkeypatch.setattr(mod, "AudioProcessor", FakeProcessor)
    monkeypatch.setattr(mod, "_collect_paths", lambda args: [ep, tmp_path / "gone.mp3"])
    monkeypatch.setattr(sys, "argv", ["process_episode.py", "--cooldown", "0", str(ep)])
    mod.main()
    assert calls == [str(ep)]
