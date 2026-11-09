"""An LLM span is bounded by the window the model was shown.

The refine_spans prompt asks the model to move end_ms later, so the stage-2
parse is the one most likely to be handed an endpoint past the section. An
unbounded end_ms of 9999999 reaches cut_and_stitch_audio, which clamps to the
episode length and excises everything from the ad to the end of the episode.
"""

from unittest.mock import MagicMock, patch

import pytest

from skipcastify.services import section_processor
from skipcastify.services.audio_processor import AudioProcessor, Segment

# 8 minutes of speech: one 10-minute section, so one refinement pass.
ONE_SECTION = [Segment(start=i * 60_000, end=(i + 1) * 60_000, text=f"line {i}")
               for i in range(8)]
# 20 minutes: three overlapping sections, so three passes over the same span.
THREE_SECTIONS = [Segment(start=i * 60_000, end=(i + 1) * 60_000, text=f"line {i}")
                  for i in range(20)]


def _refine(tmp_path, spans, reply, segments=ONE_SECTION):
    """Run stage 2 with one canned LLM reply and return the resulting spans."""
    processor = AudioProcessor(str(tmp_path / "data"))
    sections = section_processor.make_sections_from_segments(segments)
    resp = MagicMock(text=reply, prompt_tokens=1, completion_tokens=1,
                     cached_tokens=0, cost_usd=0.0)
    with patch("skipcastify.services.audio_processor.llm_utils.call_llm",
               return_value=resp), \
         patch("skipcastify.services.audio_processor.metrics.log_event"):
        return processor._run_refinement_llm(
            spans, sections, str(tmp_path), "ep", "feed"), sections


class TestStageTwoIsBounded:
    def test_a_runaway_end_ms_is_clamped_to_the_section(self, tmp_path):
        spans = [{"start_ms": 120_000, "end_ms": 180_000,
                  "label": "ADVERTISEMENT", "rationale": "pod"}]
        refined, sections = _refine(
            tmp_path, spans,
            '[{"start_ms": 120000, "end_ms": 9999999, "label": "ADVERTISEMENT", '
            '"rationale": "extended"}]')
        assert refined, "the refinement dropped every span"
        worst = max(s["end_ms"] for s in refined)
        episode_end = max(s.end for s in ONE_SECTION)
        assert worst <= episode_end, (
            f"a span runs to {worst}ms, past the end of the episode "
            f"({episode_end}ms) — the whole tail would be cut")
        assert worst <= max(sec.end_ms for sec in sections)

    @pytest.mark.xfail(strict=True, reason="known defect: each overlapping "
                                           "section re-replaces the span, so "
                                           "the clamp only bounds one hop")
    def test_overlapping_sections_do_not_walk_a_span_forward(self, tmp_path):
        """Clamping each answer to its own section bounds one hop, not the
        total.

        Sections step by 9.5 minutes and overlap by 30s, and stage 2 replaces
        every span overlapping a section with that section's answer, so a
        span near a boundary is refined again by the following section. A
        model that keeps asking for a later end_ms therefore walks a span
        forward one section per pass while every single hop stays inside its
        own section. Merging per span instead of replacing the set closes it.
        """
        spans = [{"start_ms": 120_000, "end_ms": 180_000,
                  "label": "ADVERTISEMENT", "rationale": "pod"}]
        refined, _ = _refine(
            tmp_path, spans,
            '[{"start_ms": 120000, "end_ms": 9999999, "label": "ADVERTISEMENT", '
            '"rationale": "extended"}]',
            segments=THREE_SECTIONS)
        assert max(s["end_ms"] for s in refined) < 1_200_000

    def test_a_legitimate_extension_still_happens(self, tmp_path):
        """The clamp must not disarm the refinement it is guarding."""
        spans = [{"start_ms": 120_000, "end_ms": 180_000,
                  "label": "ADVERTISEMENT", "rationale": "pod"}]
        refined, _ = _refine(
            tmp_path, spans,
            '[{"start_ms": 118000, "end_ms": 195000, "label": "ADVERTISEMENT", '
            '"rationale": "starts earlier, ends later"}]')
        assert (refined[0]["start_ms"], refined[0]["end_ms"]) == (118_000, 195_000)

    def test_an_unusable_reply_leaves_the_spans_alone(self, tmp_path):
        spans = [{"start_ms": 120_000, "end_ms": 180_000,
                  "label": "ADVERTISEMENT", "rationale": "pod"}]
        refined, _ = _refine(tmp_path, spans, '[{"start_ms": 500, "end_ms": 400}]')
        assert refined == spans
