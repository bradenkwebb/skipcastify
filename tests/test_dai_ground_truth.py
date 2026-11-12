"""Checks that stop a bad rendering diff from becoming ground truth."""

import json
import os

from scripts import dai_ground_truth as gt
from skipcastify.services import labeling

PLAUSIBLE = [{"start_ms": 0, "end_ms": 60_000, "label": "ad"},
             {"start_ms": 1_000_000, "end_ms": 1_120_000, "label": "ad"}]
EPISODE_MS = 5_152_000.0
REFERENCE_MS = 4_658_000.0
DECLARED_S = 4659.0


class TestDeclaredDuration:
    def test_hours_minutes_seconds(self):
        assert gt.declared_duration_s({"itunes_duration": "01:17:39"}) == 4659

    def test_minutes_and_seconds(self):
        assert gt.declared_duration_s({"itunes_duration": "17:39"}) == 1059

    def test_plain_seconds(self):
        assert gt.declared_duration_s({"itunes_duration": "4659"}) == 4659

    def test_a_missing_duration_is_unknown(self):
        assert gt.declared_duration_s({}) is None

    def test_an_unparsable_duration_is_unknown(self):
        assert gt.declared_duration_s({"itunes_duration": "about an hour"}) is None


class TestCheck:
    def test_a_plausible_diff_passes(self):
        assert gt.check(PLAUSIBLE, REFERENCE_MS, EPISODE_MS, DECLARED_S) is None

    def test_a_truncated_reference_is_refused(self):
        """A short reference aligns as a perfect prefix and reads the missing
        tail as one enormous ad."""
        assert gt.check(PLAUSIBLE, 2_000_000.0, EPISODE_MS, DECLARED_S) is not None

    def test_a_reference_longer_than_declared_passes(self):
        """A declared duration describes the master, and a reference may carry
        house spots that the master does not."""
        assert gt.check(PLAUSIBLE, REFERENCE_MS, EPISODE_MS, 4_480.0) is None

    def test_a_rounded_declared_duration_still_passes(self):
        assert gt.check(PLAUSIBLE, REFERENCE_MS, EPISODE_MS, 4620.0) is None

    def test_an_unknown_declared_duration_is_not_held_against_it(self):
        assert gt.check(PLAUSIBLE, REFERENCE_MS, EPISODE_MS, None) is None

    def test_identical_renderings_are_refused(self):
        reason = gt.check([], EPISODE_MS, EPISODE_MS, None)
        assert reason is not None and "hand labels" in reason

    def test_an_implausible_share_is_refused(self):
        half = [{"start_ms": 0, "end_ms": int(EPISODE_MS * 0.5), "label": "ad"}]
        assert gt.check(half, REFERENCE_MS, EPISODE_MS, None) is not None

    def test_an_over_long_break_is_refused(self):
        long_break = [{"start_ms": 0, "end_ms": 700_000, "label": "ad"}]
        assert gt.check(long_break, REFERENCE_MS, EPISODE_MS, None) is not None


class TestFeedUrls:
    def test_outlines_are_keyed_by_slug(self, tmp_path):
        opml = tmp_path / "subs.opml"
        opml.write_text('<opml><body><outline text="feeds">'
                        '<outline type="rss" title="Odd Lots" xmlUrl="https://a/f.xml"/>'
                        '<outline type="rss" title="Pod Save the World" xmlUrl="https://b/f.xml"/>'
                        '</outline></body></opml>')
        assert gt.feed_urls([opml]) == {"odd-lots": "https://a/f.xml",
                                        "pod-save-the-world": "https://b/f.xml"}

    def test_the_first_file_wins_a_repeated_feed(self, tmp_path):
        one, two = tmp_path / "one.opml", tmp_path / "two.opml"
        for path, url in ((one, "https://first/f.xml"), (two, "https://second/f.xml")):
            path.write_text(f'<opml><body><outline type="rss" title="Odd Lots" xmlUrl="{url}"/></body></opml>')
        assert gt.feed_urls([one, two])["odd-lots"] == "https://first/f.xml"

    def test_a_missing_file_is_ignored(self, tmp_path):
        assert gt.feed_urls([tmp_path / "absent.opml"]) == {}


class TestEpisodesFor:
    def _episode(self, data_dir, slug, name, transcript=True):
        raw = data_dir / "podcasts" / "raw" / slug
        raw.mkdir(parents=True, exist_ok=True)
        (raw / f"{name}.mp3").write_bytes(b"\x00")
        if transcript:
            tdir = data_dir / "transcripts"
            tdir.mkdir(parents=True, exist_ok=True)
            (tdir / f"{name}_transcript.json").write_text(json.dumps([]))

    def test_only_episodes_with_a_cached_transcript(self, tmp_path):
        self._episode(tmp_path, "show", "show-with")
        self._episode(tmp_path, "show", "show-without", transcript=False)
        found = gt.episodes_for(tmp_path, "show", [], 10)
        assert [p.stem for p in found] == ["show-with"]

    def test_newest_first(self, tmp_path):
        for n, name in enumerate(("show-old", "show-mid", "show-new")):
            self._episode(tmp_path, "show", name)
            path = tmp_path / "podcasts" / "raw" / "show" / f"{name}.mp3"
            stamp = 1_700_000_000 + n * 86400
            os.utime(path, (stamp, stamp))
        found = gt.episodes_for(tmp_path, "show", [], 10)
        assert [p.stem for p in found] == ["show-new", "show-mid", "show-old"]

    def test_the_limit_is_respected(self, tmp_path):
        for name in ("show-a", "show-b", "show-c"):
            self._episode(tmp_path, "show", name)
        assert len(gt.episodes_for(tmp_path, "show", [], 2)) == 2

    def test_a_substring_filter_narrows_it(self, tmp_path):
        self._episode(tmp_path, "show", "show-keep_me")
        self._episode(tmp_path, "show", "show-other")
        found = gt.episodes_for(tmp_path, "show", ["keep_me"], 10)
        assert [p.stem for p in found] == ["show-keep_me"]

    def test_an_unknown_feed_has_no_episodes(self, tmp_path):
        assert gt.episodes_for(tmp_path, "absent", [], 10) == []


class TestProtection:
    """Only an unreviewed file this script wrote may be replaced."""

    SPAN = [{"start_ms": 0, "end_ms": 9000, "label": "ad"}]

    def test_an_episode_with_no_labels_is_open(self, tmp_path):
        assert gt.protected_because(tmp_path, "show-ep") is None

    def test_a_file_this_script_wrote_and_nobody_reviewed_can_be_replaced(self, tmp_path):
        labeling.save_labels(tmp_path, "show-ep", self.SPAN, reviewed=False,
                             source=labeling.DAI_DIFF)
        assert gt.protected_because(tmp_path, "show-ep") is None

    def test_hand_labels_are_protected(self, tmp_path):
        labeling.save_labels(tmp_path, "show-ep", self.SPAN, reviewed=True)
        assert "hand labels" in gt.protected_because(tmp_path, "show-ep")

    def test_unreviewed_hand_labels_are_protected(self, tmp_path):
        labeling.save_labels(tmp_path, "show-ep", self.SPAN, reviewed=False)
        assert "hand labels" in gt.protected_because(tmp_path, "show-ep")

    def test_confirmed_derived_labels_are_protected(self, tmp_path):
        """Confirming in /label keeps source dai_diff, and a fresh diff would
        undo the confirmation and any relabelling with it."""
        labeling.save_labels(tmp_path, "show-ep", self.SPAN, reviewed=False,
                             source=labeling.DAI_DIFF)
        labeling.save_labels(tmp_path, "show-ep",
                             [{"start_ms": 0, "end_ms": 9000, "label": "self_promo"}],
                             reviewed=True,
                             source=labeling.stored_source(tmp_path, "show-ep"))
        assert "already reviewed" in gt.protected_because(tmp_path, "show-ep")

    def test_a_label_without_a_source_counts_as_a_hand_label(self, tmp_path):
        """Every label file predating the source field was written by hand."""
        labeling.save_labels(tmp_path, "show-ep", self.SPAN, reviewed=True)
        path = labeling.label_path(tmp_path, "show-ep")
        doc = json.loads(path.read_text())
        doc.pop("source")
        path.write_text(json.dumps(doc))
        assert "hand labels" in gt.protected_because(tmp_path, "show-ep")

    def test_an_unreadable_label_file_is_protected(self, tmp_path):
        path = labeling.label_path(tmp_path, "show-ep")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{ truncated")
        assert "unreadable" in gt.protected_because(tmp_path, "show-ep")
