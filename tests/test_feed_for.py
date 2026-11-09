"""Resolving which show an episode belongs to.

The last resort is a name-prefix match, broken by taking the longest slug.
safe_filename keeps hyphens from the episode title, so that tie-break can be
wrong whenever one subscription's slug is a prefix of another's.
"""

from skipcastify.services import labeling
from skipcastify.utils.utils import safe_filename


def _raw(tmp_path, slug, name):
    d = tmp_path / "podcasts" / "raw" / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.mp3").write_bytes(b"ID3")


def test_the_event_log_wins(tmp_path):
    _raw(tmp_path, "other-show", "the-daily-ep1")
    completes = {"the-daily-ep1": {"feed_slug": "the-daily"}}
    assert labeling.feed_for(tmp_path, "the-daily-ep1", completes) == "the-daily"


def test_the_raw_audio_settles_a_prefix_collision(tmp_path):
    """The file's own directory is exact, where the prefix match is a guess."""
    name = safe_filename("Show-and-tell", "the-daily")[: -len(".mp3")]
    assert name == "the-daily-show-and-tell"          # also prefixed by the-daily-show
    (tmp_path / "podcasts" / "raw" / "the-daily-show").mkdir(parents=True)
    _raw(tmp_path, "the-daily", name)

    assert labeling.feed_for(tmp_path, name, {}) == "the-daily"


def test_the_prefix_match_is_the_last_resort(tmp_path):
    """No event, no audio on disk: still answer, still a guess."""
    (tmp_path / "podcasts" / "raw" / "the-daily").mkdir(parents=True)
    assert labeling.feed_for(tmp_path, "the-daily-ep1", {}) == "the-daily"


def test_the_longest_prefix_still_wins_when_nothing_else_can_tell(tmp_path):
    for slug in ("the-daily", "the-daily-show"):
        (tmp_path / "podcasts" / "raw" / slug).mkdir(parents=True)
    assert labeling.feed_for(tmp_path, "the-daily-show-ep1", {}) == "the-daily-show"


def test_an_unknown_episode_is_none(tmp_path):
    (tmp_path / "podcasts" / "raw" / "the-daily").mkdir(parents=True)
    assert labeling.feed_for(tmp_path, "some-other-podcast-ep1", {}) is None


def test_a_missing_raw_directory_is_not_an_error(tmp_path):
    assert labeling.feed_for(tmp_path, "the-daily-ep1", {}) is None


def test_the_event_log_is_consulted_by_default(tmp_path):
    """Called without `completes`, it loads the log itself."""
    assert labeling.feed_for(tmp_path, "the-daily-ep1") is None
