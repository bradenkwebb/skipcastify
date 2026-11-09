"""Where GroundTruthStore looks for labels, and when it creates directories."""

from skipcastify.services.ground_truth import GroundTruthLabel, GroundTruthStore


def test_default_follows_data_dir_not_the_cwd(tmp_path, monkeypatch):
    """A store resolved against the cwd reads as empty from any other
    directory, and reports every episode unlabeled."""
    labels = tmp_path / 'data' / 'labels' / 'ground_truth'
    labels.mkdir(parents=True)
    (labels / 'ep_labels.json').write_text(
        '{"episode_name": "ep", "ads": [{"start_ms": 10, "end_ms": 20, '
        '"label": "ADVERTISEMENT", "description": "spot"}]}')

    monkeypatch.setenv('DATA_DIR', str(tmp_path / 'data'))
    monkeypatch.chdir(tmp_path / 'data' / 'labels')  # anywhere but the repo root

    store = GroundTruthStore()
    assert store.list_labeled() == ['ep']
    assert [(l.start_ms, l.end_ms) for l in store.load_labels('ep')] == [(10, 20)]


def test_reading_does_not_create_the_directory(tmp_path, monkeypatch):
    """Creating the directory on read would turn a wrong path into an empty
    one instead of a visible miss."""
    monkeypatch.setenv('DATA_DIR', str(tmp_path / 'nonexistent'))
    store = GroundTruthStore()
    assert store.load_labels('ep') is None
    assert store.list_labeled() == []
    assert not store.has_labels('ep')
    assert not (tmp_path / 'nonexistent').exists()


def test_saving_creates_the_directory(tmp_path, monkeypatch):
    monkeypatch.setenv('DATA_DIR', str(tmp_path / 'data'))
    store = GroundTruthStore()
    store.save_labels('ep', [GroundTruthLabel(10, 20, 'ADVERTISEMENT', 'spot')])
    assert store.list_labeled() == ['ep']
    assert [(l.start_ms, l.end_ms) for l in store.load_labels('ep')] == [(10, 20)]


def test_an_explicit_path_still_wins(tmp_path, monkeypatch):
    monkeypatch.setenv('DATA_DIR', str(tmp_path / 'ignored'))
    store = GroundTruthStore(str(tmp_path / 'explicit'))
    store.save_labels('ep', [GroundTruthLabel(1, 2, 'SPONSOR')])
    assert (tmp_path / 'explicit' / 'ep_labels.json').exists()
    assert not (tmp_path / 'ignored').exists()
