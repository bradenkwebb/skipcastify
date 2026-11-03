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
        """Interview with the World Cup-predicting mathematician — genuine content.

        Window ends at 1887000, where the interview ends and the end-of-show
        cross-promo for the BBC Sports Podcast begins. That cross-promo
        ('listen to the BBC World Service Sports Podcast... available wherever
        you get your podcasts') is borderline ad/editorial and acceptable to
        remove either way, so it is intentionally outside the protected window;
        this test guards the genuine interview, not the outro.
        """
        assert not any_overlap(llm_spans, 1_750_000, 1_887_000), \
            f"World Cup mathematician interview [1750000-1887000ms] was falsely flagged. Spans: {llm_spans}"

    def test_spice_girls_anniversary(self, llm_spans):
        """Spice Girls 30th anniversary feature — cultural content, not an ad."""
        assert not any_overlap(llm_spans, 1_505_000, 1_726_000), \
            f"Spice Girls segment [1505000-1726000ms] was falsely flagged. Spans: {llm_spans}"

    def test_bbc_outro(self, llm_spans):
        """Podcast credits and outro should be kept."""
        assert not any_overlap(llm_spans, 1_902_000, 1_935_000), \
            f"BBC outro [1902000-1935000ms] was falsely flagged. Spans: {llm_spans}"


# --- the Schwab pre-roll miss, 2026-11-02 --------------------------------
#
# Episode: trump_celebrates_shock_endorsement_ahead_of_us_midterms
#
# The pre-roll pod is BBC notice -> Odoo -> Charles Schwab. In the published
# episode the 19.2s Schwab pitch survived and only its CTA was cut, so the ad
# shipped. Braden confirmed it should have been caught.
#
# The saved raw response from that run shows why, and it is not what the
# symptom suggests. The model returned three spans and every one of them was
# a URL line: [0-3420], [16560-18600] ("Join Odo today at Odo.com") and
# [41740-44840] ("Listen at Schwab.com slash Washington Wise"). It matched
# CTAs, not ads. The Odoo *body* was only removed because merge_nearby_spans
# bridged the 13.1s hole to the CTA; the Schwab CTA sat 21s from the previous
# span, over the 15s merge gap, so its body stayed in.
#
# That degradation is NOT reproducible. Same prompt, same model, same section,
# temperature 0, five consecutive runs: all five cover the Schwab pitch in
# full. So this is a guard against the detector dropping back to URL matching,
# not a known-failing case — which is worth having precisely because the
# failure was transient and nothing would otherwise notice it recurring.
#
# The fixture is section 0 exactly as the pipeline builds it (0-600000ms),
# because a 151s clip around the pod was caught correctly even on the first
# try; the degradation only appeared at full section size.

_PREROLL_FIXTURE = (Path(__file__).parent / "fixtures"
                    / "global-news-trump_endorsement-preroll_transcript.json")
_PREROLL_EPISODE = "global-news-podcast-trump_celebrates_shock_endorsement_ahead_of_us_midterms"

_ODOO = (5_980, 20_720)
_SCHWAB_PITCH = (21_660, 40_860)      # kept; this is the miss
_SCHWAB_CTA = (41_740, 44_840)        # all that was caught
_NEWS_STARTS = 50_000


@pytest.fixture(scope="module")
def preroll_segments():
    if not _PREROLL_FIXTURE.exists():
        pytest.skip(f"Fixture not found: {_PREROLL_FIXTURE}")
    with open(_PREROLL_FIXTURE) as f:
        raw = json.load(f)
    from skipcastify.services.audio_processor import Segment
    return [Segment(start=s["start"], end=s["end"], text=s["text"]) for s in raw]


@pytest.fixture(scope="module")
def preroll_llm_spans(preroll_segments):
    from skipcastify.services.audio_processor import AudioProcessor
    processor = AudioProcessor("data")
    with tempfile.TemporaryDirectory() as tmp:
        return processor._run_section_llm(preroll_segments, tmp,
                                          episode_name=_PREROLL_EPISODE)


class TestSchwabPrerollMustDetect:
    def test_the_schwab_pitch_is_flagged(self, preroll_llm_spans):
        """The 19.2s that shipped on 2026-11-02.

        Fails if the detector goes back to matching only the CTA line.
        """
        assert covered(preroll_llm_spans, *_SCHWAB_PITCH, tolerance_ms=2_000), \
            f"Schwab pitch {_SCHWAB_PITCH} not flagged. Spans: {preroll_llm_spans}"

    def test_the_whole_preroll_pod_is_one_unbroken_span(self, preroll_llm_spans):
        """No holes. A hole is what postprocessing then has to paper over, and
        whether it manages to depends on an arbitrary 15s merge gap."""
        assert covered(preroll_llm_spans, 0, _SCHWAB_CTA[1], tolerance_ms=2_000), \
            f"Pre-roll pod has a hole. Spans: {preroll_llm_spans}"

    def test_the_ad_bodies_are_detected_not_just_their_urls(self, preroll_llm_spans):
        """The specific 2026-11-02 failure: every returned span was a URL line.

        Detection that only finds CTAs leaves the actual pitch to be rescued
        by a distance heuristic, which is how the Schwab read got through.
        """
        longest = max((s["end_ms"] - s["start_ms"] for s in preroll_llm_spans),
                      default=0)
        assert longest > 10_000, (
            f"No span longer than 10s — the detector is matching CTAs rather "
            f"than ads. Spans: {preroll_llm_spans}")

    def test_the_odoo_ad_is_still_caught(self, preroll_llm_spans):
        """Must not regress."""
        assert covered(preroll_llm_spans, *_ODOO), \
            f"Odoo ad {_ODOO} not detected. Spans: {preroll_llm_spans}"

    def test_the_schwab_cta_is_still_caught(self, preroll_llm_spans):
        """Must not regress: this much the detector already gets right."""
        assert any_overlap(preroll_llm_spans, *_SCHWAB_CTA), \
            f"Schwab CTA {_SCHWAB_CTA} not detected. Spans: {preroll_llm_spans}"


class TestSchwabPrerollMustNotRemove:
    def test_the_news_bulletin_is_kept(self, preroll_llm_spans):
        """Fixing the pre-roll must not start eating the programme."""
        assert not any_overlap(preroll_llm_spans, _NEWS_STARTS + 2_000, 10 ** 9), \
            f"News content past {_NEWS_STARTS}ms was flagged. Spans: {preroll_llm_spans}"
