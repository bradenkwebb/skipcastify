"""LLM regression tests for BBC Global News Podcast.

Episode: un_says_over_50_000_missing_after_venezuela_quakes
Ad inventory (fresh download, dynamically injected):
  - [0–50s]      Pre-roll: Snapdragon + Odoo
  - [1075–1101s] Mid-roll: Snapdragon
  - [1101–1175s] Sponsored insert: Business History / American Genius (podcast promo)
  - [1175–1207s] Sponsored insert: Washington Wise / Charles Schwab
  - [1207–1236s] Mid-roll: Wise money transfer
  - [1944–1967s] Post-roll: Snapdragon

Run with: pytest tests/llm/test_global_news_podcast.py --run-llm
"""

import json
import tempfile
from pathlib import Path

import pytest
from dotenv import load_dotenv

from tests.llm.conftest import covered, any_overlap

load_dotenv()
pytestmark = pytest.mark.llm

_TRANSCRIPT = Path("data/transcripts/global-news-podcast-un_says_over_50_000_missing_after_venezuela_quakes_transcript.json")
_EPISODE = "global-news-podcast-un_says_over_50_000_missing_after_venezuela_quakes"


@pytest.fixture(scope="module")
def segments():
    if not _TRANSCRIPT.exists():
        pytest.skip(f"Cached transcript not found: {_TRANSCRIPT}. Run preview_episode.py first.")
    with open(_TRANSCRIPT) as f:
        raw = json.load(f)
    from skipcastify.services.audio_processor import Segment
    return [Segment(start=s["start"], end=s["end"], text=s["text"]) for s in raw]


@pytest.fixture(scope="module")
def llm_spans(segments):
    from skipcastify.services.audio_processor import AudioProcessor
    processor = AudioProcessor("data")
    with tempfile.TemporaryDirectory() as tmp:
        return processor._run_section_llm(segments, tmp, episode_name=_EPISODE)


# ---------------------------------------------------------------------------
# Positive cases: these ad segments must be detected
# ---------------------------------------------------------------------------

class TestMustDetect:
    def test_opening_snapdragon_and_odoo(self, llm_spans):
        """Opening ad block (Snapdragon + Odoo) before the BBC intro."""
        assert covered(llm_spans, 0, 50_000), \
            f"Opening ad block [0-50000ms] not detected. Spans: {llm_spans}"

    def test_midroll_snapdragon(self, llm_spans):
        """Mid-roll Snapdragon ad around 18-minute mark."""
        assert covered(llm_spans, 1_075_000, 1_101_000), \
            f"Mid-roll Snapdragon [1075000-1101000ms] not detected. Spans: {llm_spans}"

    def test_business_history_sponsored_insert(self, llm_spans):
        """'Business History / American Genius' clip — paid promo for another podcast."""
        assert covered(llm_spans, 1_101_000, 1_175_000), \
            f"Business History sponsored insert [1101000-1175000ms] not detected. Spans: {llm_spans}"

    def test_washington_wise_schwab_promo(self, llm_spans):
        """Washington Wise / Charles Schwab podcast promo."""
        assert covered(llm_spans, 1_175_000, 1_207_000), \
            f"Washington Wise promo [1175000-1207000ms] not detected. Spans: {llm_spans}"

    def test_wise_money_transfer_ad(self, llm_spans):
        """Wise money transfer ad read."""
        assert covered(llm_spans, 1_207_000, 1_236_000), \
            f"Wise money transfer ad [1207000-1236000ms] not detected. Spans: {llm_spans}"

    def test_closing_snapdragon(self, llm_spans):
        """Closing Snapdragon ad after the outro."""
        assert covered(llm_spans, 1_944_000, 1_967_000), \
            f"Closing Snapdragon [1944000-1967000ms] not detected. Spans: {llm_spans}"


# ---------------------------------------------------------------------------
# Negative cases: these editorial segments must NOT be removed
# ---------------------------------------------------------------------------

class TestMustNotRemove:
    def test_venezuela_news_report(self, llm_spans):
        """Core news reporting on the Venezuela earthquake."""
        assert not any_overlap(llm_spans, 102_000, 411_000), \
            f"Venezuela earthquake news [102000-411000ms] was falsely flagged. Spans: {llm_spans}"

    def test_world_cup_mathematician(self, llm_spans):
        """Interview with the World Cup-predicting mathematician — genuine content."""
        assert not any_overlap(llm_spans, 1_750_000, 1_900_000), \
            f"World Cup mathematician segment [1750000-1900000ms] was falsely flagged. Spans: {llm_spans}"

    def test_spice_girls_anniversary(self, llm_spans):
        """Spice Girls 30th anniversary feature — cultural content, not an ad."""
        assert not any_overlap(llm_spans, 1_505_000, 1_726_000), \
            f"Spice Girls segment [1505000-1726000ms] was falsely flagged. Spans: {llm_spans}"

    def test_bbc_outro(self, llm_spans):
        """Podcast credits and outro should be kept."""
        assert not any_overlap(llm_spans, 1_902_000, 1_935_000), \
            f"BBC outro [1902000-1935000ms] was falsely flagged. Spans: {llm_spans}"
