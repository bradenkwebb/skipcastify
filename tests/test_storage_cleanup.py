import pytest

from pipeline import Pipeline


@pytest.fixture
def pipeline(tmp_path):
    opml = tmp_path / "subs.opml"
    opml.write_text('<opml version="1.0"><body></body></opml>')
    p = Pipeline(str(opml), str(tmp_path))
    p.storage_high_water, p.storage_low_water = 0, 0  # always trim, all the way
    return p


def mp3(path, size=1000):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * size)
    return path


def test_labeled_raw_audio_survives_cleanup(pipeline, tmp_path):
    raw, done = tmp_path / "podcasts" / "raw" / "show", tmp_path / "podcasts" / "processed" / "show"
    labeled = mp3(raw / "show-ep_1.mp3")
    unlabeled = mp3(raw / "show-ep_2.mp3")
    processed = [mp3(done / "show-ep_1.mp3"), mp3(done / "show-ep_2.mp3")]
    labels = tmp_path / "labels" / "ground_truth"
    labels.mkdir(parents=True)
    (labels / "show-ep_1_labels.json").write_text("{}")

    pipeline._cleanup_old_episodes()

    assert labeled.exists()
    assert not unlabeled.exists()
    assert not any(p.exists() for p in processed)  # processed audio is still fair game
