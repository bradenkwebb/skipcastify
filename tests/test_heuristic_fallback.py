"""The keyword heuristic must only run when the LLM never answered.

`process()` used to choose its classifier with `if llm_ad_spans:`, which cannot
tell "the LLM read the episode and there are no ads" apart from "the LLM never
answered". Every ad-free episode therefore fell through to
`SegmentClassifier.classify_segments`, a keyword matcher whose ad pattern is
`\\b(ad|advertisement|commercial|promotion)\\b` — a bare "ad" anywhere in
ordinary conversation is a cut, and `\\b(discount|promo|code|offer|deal)\\b` is
just as blunt.

That shipped. the-ezra-klein-show-the_end_of_trumpism (processed 2026-11-04)
has no third-party ads; all six LLM sections returned [], and 6.2s of the
conversation was cut anyway because one Whisper segment garbled into "The ad
for people to involve this where he had an ad on from her estranged mother".
Across the event log, 23 episodes since the 2026-07-01 stitcher fix lost audio
this way with span_count 0 — 9.3 minutes in total.
"""

from unittest.mock import MagicMock, patch

import pytest

from skipcastify.models.content import ContentType
from skipcastify.services.audio_processor import AudioProcessor, Segment
from skipcastify.services.segment_classifier import SegmentClassifier

AD_TYPES = {ContentType.ADVERTISEMENT, ContentType.SPONSOR}

# The exact Whisper output that cost the Ezra Klein episode 6.2s, at its real
# offsets in that episode.
_GARBLED_AD_MENTION = ("thing ever. The ad for people to involve this where he "
                       "had an ad on from her estranged mother")
_REAL_SEGMENTS = [
    Segment(start=2_520_000, end=2_526_750, text="To get to your point about investigation and corruption"),
    Segment(start=2_526_750, end=2_532_930, text=_GARBLED_AD_MENTION),
    Segment(start=2_533_410, end=2_541_810, text="and that is the thing I keep coming back to"),
]


def _aggregated_from_process(tmp_path, segments, *, llm_spans=None, llm_raises=False):
    """Run process() with the heavy stages mocked and return the aggregated
    segments it handed to the stitcher — i.e. what it decided to cut."""
    data_dir = tmp_path / "data"
    raw_dir = data_dir / "podcasts" / "raw" / "test-feed"
    raw_dir.mkdir(parents=True)
    episode_path = raw_dir / "test-feed-some_episode.mp3"
    episode_path.write_bytes(b"not really mp3")

    audio = MagicMock(); audio.duration_seconds = 2600.0
    processed = MagicMock(); processed.duration_seconds = 2600.0
    captured = []

    def fake_stitch(self, audio, aggregated_segments):
        captured.extend(aggregated_segments)
        return processed

    llm = (patch.object(AudioProcessor, "_run_section_llm",
                        side_effect=RuntimeError("all 6 LLM section calls failed"))
           if llm_raises else
           patch.object(AudioProcessor, "_run_section_llm", return_value=llm_spans or []))

    processor = AudioProcessor(str(data_dir))
    with patch.object(AudioProcessor, "load_and_validate_audio_file", return_value=audio), \
         patch.object(AudioProcessor, "_export_audio_to_wav"), \
         patch.object(AudioProcessor, "transcribe_with_whisper", return_value=segments), \
         llm, \
         patch.object(AudioProcessor, "cut_and_stitch_audio", fake_stitch), \
         patch("skipcastify.services.audio_processor._source_bitrate", return_value="128k"), \
         patch("skipcastify.services.audio_processor.metrics.log_event"):
        processor.process(str(episode_path), state_manager=MagicMock())

    return captured


class TestNoAdsMeansNoCuts:
    """An empty span list is an answer, not a failure."""

    def test_an_ad_free_episode_keeps_the_garbled_ad_mention(self, tmp_path):
        aggregated = _aggregated_from_process(tmp_path, _REAL_SEGMENTS, llm_spans=[])
        cut = [s for s in aggregated if s.content_type in AD_TYPES]
        assert cut == [], (
            "the LLM returned no spans, so nothing should be cut; instead the "
            f"keyword heuristic marked {[(s.start, s.end) for s in cut]}")

    def test_the_6_2_second_span_is_not_cut(self, tmp_path):
        """The exact span that shipped: [2526750-2532930]."""
        aggregated = _aggregated_from_process(tmp_path, _REAL_SEGMENTS, llm_spans=[])
        for seg in aggregated:
            if seg.content_type in AD_TYPES:
                assert not (seg.start < 2_532_930 and seg.end > 2_526_750), \
                    "the conversation about an attack ad was cut as an advertisement"

    def test_spans_the_llm_does_return_are_still_cut(self, tmp_path):
        """The fix must not disarm detection on episodes that do have ads."""
        aggregated = _aggregated_from_process(
            tmp_path, _REAL_SEGMENTS,
            llm_spans=[{"start_ms": 2_520_000, "end_ms": 2_526_000,
                        "label": "ADVERTISEMENT", "rationale": "x"}])
        cut = [s for s in aggregated if s.content_type in AD_TYPES]
        assert cut, "an LLM-identified span was not cut"
        assert cut[0].start == 2_520_000


class TestTheFallbackStillExists:
    """When the LLM genuinely never answered, the heuristic is still better
    than shipping an unprocessed episode."""

    def test_an_llm_failure_falls_back_to_the_heuristic(self, tmp_path):
        aggregated = _aggregated_from_process(tmp_path, _REAL_SEGMENTS, llm_raises=True)
        cut = [s for s in aggregated if s.content_type in AD_TYPES]
        assert cut, "an LLM outage should still fall back to keyword classification"


class TestAllSectionsFailingIsNotAnEmptyAnswer:
    """_run_section_llm must not report "no ads" when nothing answered."""

    def _processor(self, tmp_path):
        return AudioProcessor(str(tmp_path / "data"))

    def test_total_failure_raises(self, tmp_path):
        processor = self._processor(tmp_path)
        segments = [Segment(start=0, end=5_000, text="hello")]
        with patch("skipcastify.services.audio_processor.llm_utils.call_llm",
                   side_effect=ValueError("OPENAI_API_KEY not set in environment")), \
             patch("skipcastify.services.audio_processor.metrics.log_event"):
            with pytest.raises(RuntimeError, match="section calls failed"):
                processor._run_section_llm(segments, str(tmp_path),
                                          episode_name="ep", feed_slug="f")

    def test_one_answering_section_is_enough(self, tmp_path):
        """A partial outage keeps the sections that did answer."""
        processor = self._processor(tmp_path)
        # Two sections: the section step is 570s, so 1200s of audio spans more
        # than one window.
        segments = [Segment(start=i * 60_000, end=(i + 1) * 60_000, text=f"line {i}")
                    for i in range(20)]

        calls = {"n": 0}

        def fake_call(prompt):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ValueError("boom")
            return MagicMock(
                text='[{"start_ms": 600000, "end_ms": 610000, '
                     '"label": "ADVERTISEMENT", "rationale": "y"}]',
                prompt_tokens=1, completion_tokens=1,
                cached_tokens=0, cost_usd=0.0)

        with patch("skipcastify.services.audio_processor.llm_utils.call_llm",
                   side_effect=fake_call), \
             patch("skipcastify.services.audio_processor.metrics.log_event"):
            spans = processor._run_section_llm(segments, str(tmp_path),
                                               episode_name="ep", feed_slug="f")
        assert calls["n"] > 1, "expected more than one section in this fixture"
        assert [(s["start_ms"], s["end_ms"]) for s in spans] == [(600_000, 610_000)]


class TestWhyTheHeuristicCannotRunUnsupervised:
    """Documents the blast radius, so nobody re-points the fallback at it."""

    @pytest.mark.parametrize("text", [
        _GARBLED_AD_MENTION,
        "that was a really good deal for the voters",
        "it is right there in the tax code",
        "the offer on the table was never serious",
    ])
    def test_ordinary_conversation_trips_the_ad_keywords(self, text):
        content_type, _ = SegmentClassifier()._classify_single_segment(text)
        assert content_type in AD_TYPES, (
            "this test documents a known weakness; if the heuristic improved, "
            "update the module docstring too")


def _preview(tmp_path, segments, *, cached, llm_spans=None, llm_raises=False):
    """Run one of the two preview paths with the heavy stages mocked.

    Returns (aggregated_segments_handed_to_the_stitcher, result_dict).
    """
    data_dir = tmp_path / "data"
    raw_dir = data_dir / "podcasts" / "raw" / "test-feed"
    raw_dir.mkdir(parents=True)
    episode_path = raw_dir / "test-feed-some_episode.mp3"
    episode_path.write_bytes(b"not really mp3")
    preview_path = data_dir / "podcasts" / "processed_preview" / "test-feed-some_episode_preview.mp3"

    audio = MagicMock(); audio.duration_seconds = 2600.0
    processed = MagicMock(); processed.duration_seconds = 2600.0
    captured = []

    def fake_stitch(self, audio, aggregated_segments):
        captured.extend(aggregated_segments)
        return processed

    llm = (patch.object(AudioProcessor, "_run_section_llm",
                        side_effect=RuntimeError("all 6 LLM section calls failed"))
           if llm_raises else
           patch.object(AudioProcessor, "_run_section_llm", return_value=llm_spans or []))

    processor = AudioProcessor(str(data_dir))
    with patch.object(AudioProcessor, "load_and_validate_audio_file", return_value=audio), \
         patch.object(AudioProcessor, "_export_audio_to_wav"), \
         patch.object(AudioProcessor, "transcribe_with_whisper", return_value=segments), \
         llm, \
         patch.object(AudioProcessor, "cut_and_stitch_audio", fake_stitch), \
         patch("skipcastify.services.audio_processor._source_bitrate", return_value="128k"), \
         patch("skipcastify.services.audio_processor.metrics.log_event"):
        if cached:
            result = processor.process_preview_with_segments(
                str(episode_path), str(preview_path), segments)
        else:
            result = processor.process_preview(str(episode_path), str(preview_path))

    return captured, result


@pytest.mark.parametrize("cached", [False, True],
                         ids=["process_preview", "process_preview_with_segments"])
class TestThePreviewPathsAgreeWithProduction:
    """The preview MP3 is what a human reviews before trusting a cut, and
    <episode>_predictions.json is what scripts/evaluate_ad_detection.py scores
    against the hand labels. Both must agree with process() about when the
    keyword heuristic is allowed to run.
    """

    def test_an_ad_free_episode_keeps_the_garbled_ad_mention(self, tmp_path, cached):
        aggregated, _ = _preview(tmp_path, _REAL_SEGMENTS, cached=cached, llm_spans=[])
        cut = [s for s in aggregated if s.content_type in AD_TYPES]
        assert cut == [], (
            "the LLM returned no spans, so the preview should cut nothing; instead "
            f"the keyword heuristic marked {[(s.start, s.end) for s in cut]}")

    def test_predictions_are_not_polluted_by_the_heuristic(self, tmp_path, cached):
        """What eval scores must be the detector's answer, not a keyword hit."""
        _, result = _preview(tmp_path, _REAL_SEGMENTS, cached=cached, llm_spans=[])
        assert result["ad_spans"] == [], (
            "predictions.json would record heuristic spans for an episode the "
            f"LLM declared ad-free: {result['ad_spans']}")

    def test_spans_the_llm_does_return_are_still_cut(self, tmp_path, cached):
        aggregated, result = _preview(
            tmp_path, _REAL_SEGMENTS, cached=cached,
            llm_spans=[{"start_ms": 2_520_000, "end_ms": 2_526_000,
                        "label": "ADVERTISEMENT", "rationale": "x"}])
        cut = [s for s in aggregated if s.content_type in AD_TYPES]
        assert cut, "an LLM-identified span was not cut in the preview"
        assert cut[0].start == 2_520_000
        assert result["ad_spans"][0]["start_ms"] == 2_520_000

    def test_an_llm_failure_still_falls_back_to_the_heuristic(self, tmp_path, cached):
        aggregated, _ = _preview(tmp_path, _REAL_SEGMENTS, cached=cached, llm_raises=True)
        cut = [s for s in aggregated if s.content_type in AD_TYPES]
        assert cut, "an LLM outage should still fall back to keyword classification"
