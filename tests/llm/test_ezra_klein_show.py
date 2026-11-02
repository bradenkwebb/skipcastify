"""LLM regression tests for The Ezra Klein Show.

Two episodes, both covering the same failure mode from opposite ends: an ad
block the detector catches only part of, keeping the rest in the episode.

Episode 1: chris_rufo_thinks_the_right_can_control_this_i_dont
Covers a pre-roll ad that the detector originally under-caught: a ~30s Amaloid
(anti-amyloid drug) spot that opens the episode. It is disguised as a public-
health PSA — a symptom-listing hook ("If you walk into a room and can't remember
why...") that only reveals the brand/CTA ("Visit Amaloid.com") at the very end.
The detector used to flag only the final CTA segment and leave the lead-in in
the episode; it must now flag the whole block from [0].

Episode 2: elon_musks_endgame
Covers a mid-roll sponsor pod the detector still under-catches. See the second
half of this file.

Both use committed transcript-clip fixtures rather than full episode
transcripts, so the tests are reproducible without re-transcribing an
hour-long episode, and each clip is one section — one paid LLM call per run.

Run with: pytest tests/llm/test_ezra_klein_show.py --run-llm
"""

import json
import tempfile
from pathlib import Path

import pytest
from dotenv import load_dotenv

from tests.llm.conftest import any_overlap, covered

load_dotenv()
pytestmark = pytest.mark.llm

_FIXTURES = Path(__file__).parent / "fixtures"


def _load_segments(path: Path):
    if not path.exists():
        pytest.skip(f"Fixture not found: {path}")
    with open(path) as f:
        raw = json.load(f)
    from skipcastify.services.audio_processor import Segment
    return [Segment(start=s["start"], end=s["end"], text=s["text"]) for s in raw]


def _detect(segments, episode_name):
    from skipcastify.services.audio_processor import AudioProcessor
    processor = AudioProcessor("data")
    with tempfile.TemporaryDirectory() as tmp:
        return processor._run_section_llm(segments, tmp, episode_name=episode_name)


# --- episode 1: the Amaloid pre-roll -------------------------------------

_FIXTURE = _FIXTURES / "ezra-klein-chris_rufo-opening_transcript.json"
_EPISODE = "the-ezra-klein-show-chris_rufo_thinks_the_right_can_control_this_i_dont"


@pytest.fixture(scope="module")
def segments():
    return _load_segments(_FIXTURE)


@pytest.fixture(scope="module")
def llm_spans(segments):
    return _detect(segments, _EPISODE)


class TestMustDetect:
    def test_amaloid_preroll_full_span(self, llm_spans):
        """The full Amaloid pre-roll [0-30040ms] must be caught, not just the CTA tail."""
        assert covered(llm_spans, 0, 30_040), \
            f"Amaloid pre-roll [0-30040ms] not fully detected. Spans: {llm_spans}"

    def test_preroll_starts_at_zero(self, llm_spans):
        """The ad must start at the top of the episode, not at the CTA segment (~25.4s)."""
        assert any(s["start_ms"] <= 6_800 for s in llm_spans), \
            f"No span starts at the episode open; lead-in was missed. Spans: {llm_spans}"


class TestMustNotRemove:
    def test_interview_content_kept(self, llm_spans):
        """Editorial content after the ad (Chris Rufo discussion, from ~56s) must stay."""
        assert not any(s["end_ms"] > 56_040 + 4_000 for s in llm_spans), \
            f"Editorial content past 56s was falsely flagged. Spans: {llm_spans}"


# --- episode 2: the mid-roll sponsor pod ---------------------------------
#
# "Elon Musk's Endgame" (published 2026-10-30), processed 2026-11-01. The
# episode has a three-sponsor pod at 53:34 in the original: a Betterment
# tax-loss-harvesting read, then Made In Cookware, then Midi Health.
#
# The detector flagged 53:50-54:54 and missed the first 15.1s. What it kept is
# the Betterment *pitch*; what it cut starts at the legal disclaimer
# ("Investing involves risk, performance not guaranteed..."). So the published
# episode begins the ad, runs 15 seconds of it, then jumps — which is worse
# than leaving the whole pod in.
#
# Same shape as the Amaloid pre-roll above: the tail of an ad block is
# unmistakable, the opening is not, and only the tail gets flagged. Here the
# opening is a single 15s transcript segment sitting 0.6s before a span that
# *was* caught, so nothing about the gap explains the miss.
#
# Offsets below are clip-relative. The clip starts at 51:29 in the episode.

_MIDROLL_FIXTURE = _FIXTURES / "ezra-klein-elon_musks_endgame-midroll_transcript.json"
_MIDROLL_EPISODE = "the-ezra-klein-show-elon_musks_endgame"

_POD_START, _POD_END = 125_279, 205_100      # the whole three-sponsor pod
_BETTERMENT_PITCH_END = 140_420              # the 15.1s that was kept
_POD_TAIL_START = 141_020                    # all that was caught, from here
_CONTENT_BEFORE_END = 107_820                # editorial before the pod
_CONTENT_AFTER_START = 206_600               # editorial after the pod


@pytest.fixture(scope="module")
def midroll_segments():
    return _load_segments(_MIDROLL_FIXTURE)


@pytest.fixture(scope="module")
def midroll_llm_spans(midroll_segments):
    return _detect(midroll_segments, _MIDROLL_EPISODE)


class TestMidrollPodMustDetect:
    """The two assertions that fail today.

    xfail(strict=True) rather than a plain failing test: the paid suite stays
    green-means-green, and the moment detection improves these XPASS, which
    strict mode reports as a failure telling you to drop the marker.
    """

    @pytest.mark.xfail(strict=True, reason="known miss: pod caught only from "
                                           "its disclaimer tail")
    def test_pod_is_caught_from_its_first_word(self, midroll_llm_spans):
        earliest = min((s["start_ms"] for s in midroll_llm_spans), default=None)
        assert earliest is not None and earliest <= _POD_START + 3_000, (
            f"Pod starts at {_POD_START}ms but the earliest span is "
            f"{earliest}ms. Spans: {midroll_llm_spans}")

    @pytest.mark.xfail(strict=True, reason="known miss: the Betterment pitch "
                                           "segment is classified as content")
    def test_the_betterment_pitch_segment_is_flagged(self, midroll_llm_spans):
        """The 15.1s read that survived into the published episode."""
        assert covered(midroll_llm_spans, _POD_START, _BETTERMENT_PITCH_END,
                       tolerance_ms=2_000), (
            f"Betterment pitch [{_POD_START}-{_BETTERMENT_PITCH_END}ms] not "
            f"flagged. Spans: {midroll_llm_spans}")

    def test_the_pod_tail_is_still_caught(self, midroll_llm_spans):
        """Must not regress: this much the detector already gets right."""
        assert covered(midroll_llm_spans, _POD_TAIL_START, _POD_END), (
            f"Pod tail [{_POD_TAIL_START}-{_POD_END}ms] not detected. "
            f"Spans: {midroll_llm_spans}")


class TestMidrollPodMustNotRemove:
    def test_editorial_before_the_pod_kept(self, midroll_llm_spans):
        """South Africa / ethno-nationalism discussion, up to 1:47 in the clip."""
        assert not any_overlap(midroll_llm_spans, 0, _CONTENT_BEFORE_END - 2_000), (
            f"Editorial before the pod was flagged. Spans: {midroll_llm_spans}")

    def test_editorial_after_the_pod_kept(self, midroll_llm_spans):
        """Musk-Trump alliance discussion, resuming at 3:26 in the clip."""
        assert not any_overlap(midroll_llm_spans, _CONTENT_AFTER_START + 2_000,
                               10 ** 9), (
            f"Editorial after the pod was flagged. Spans: {midroll_llm_spans}")
