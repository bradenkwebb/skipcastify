"""LLM regression test for The Ezra Klein Show.

Episode: chris_rufo_thinks_the_right_can_control_this_i_dont
Covers a pre-roll ad that the detector originally under-caught: a ~30s Amaloid
(anti-amyloid drug) spot that opens the episode. It is disguised as a public-
health PSA — a symptom-listing hook ("If you walk into a room and can't remember
why...") that only reveals the brand/CTA ("Visit Amaloid.com") at the very end.
The detector used to flag only the final CTA segment and leave the lead-in in
the episode; it must now flag the whole block from [0].

Uses a committed opening-clip fixture (first ~75s) rather than the full episode
transcript, so the test is reproducible without re-transcribing the hour-long
episode.

Run with: pytest tests/llm/test_ezra_klein_show.py --run-llm
"""

import json
import tempfile
from pathlib import Path

import pytest
from dotenv import load_dotenv

from tests.llm.conftest import covered

load_dotenv()
pytestmark = pytest.mark.llm

_FIXTURE = Path(__file__).parent / "fixtures" / "ezra-klein-chris_rufo-opening_transcript.json"
_EPISODE = "the-ezra-klein-show-chris_rufo_thinks_the_right_can_control_this_i_dont"


@pytest.fixture(scope="module")
def segments():
    if not _FIXTURE.exists():
        pytest.skip(f"Fixture not found: {_FIXTURE}")
    with open(_FIXTURE) as f:
        raw = json.load(f)
    from skipcastify.services.audio_processor import Segment
    return [Segment(start=s["start"], end=s["end"], text=s["text"]) for s in raw]


@pytest.fixture(scope="module")
def llm_spans(segments):
    from skipcastify.services.audio_processor import AudioProcessor
    processor = AudioProcessor("data")
    with tempfile.TemporaryDirectory() as tmp:
        return processor._run_section_llm(segments, tmp, episode_name=_EPISODE)


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
