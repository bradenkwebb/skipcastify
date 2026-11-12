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
from pathlib import Path

import pytest
from dotenv import load_dotenv

from tests.llm.conftest import any_overlap, covered, detect

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
    return detect(segments, _EPISODE)


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
    return detect(preroll_segments, _PREROLL_EPISODE)


# Same prompt bytes, same account: on 2026-11-05 the dominant answer flipped
# within hours (pitch 0/24, CTA 24/24 at ~20:30 UTC; bodies 5/6, CTA 1/6 at 22:17).
_SCHWAB_FLAKY = "output mode drifts over time on a pinned model; record, don't gate"


class TestSchwabPrerollMustDetect:
    @pytest.mark.xfail(strict=False, reason=_SCHWAB_FLAKY)
    def test_the_schwab_pitch_is_flagged(self, preroll_llm_spans):
        """The 19.2s that shipped on 2026-11-02.

        Fails if the detector goes back to matching only the CTA line.
        """
        assert covered(preroll_llm_spans, *_SCHWAB_PITCH, tolerance_ms=2_000), \
            f"Schwab pitch {_SCHWAB_PITCH} not flagged. Spans: {preroll_llm_spans}"

    @pytest.mark.xfail(strict=False, reason=_SCHWAB_FLAKY)
    def test_the_whole_preroll_pod_is_one_unbroken_span(self, preroll_llm_spans):
        """No holes. A hole is what postprocessing then has to paper over, and
        whether it manages to depends on an arbitrary 15s merge gap."""
        assert covered(preroll_llm_spans, 0, _SCHWAB_CTA[1], tolerance_ms=2_000), \
            f"Pre-roll pod has a hole. Spans: {preroll_llm_spans}"

    @pytest.mark.xfail(strict=False, reason=_SCHWAB_FLAKY)
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

    @pytest.mark.xfail(strict=False, reason=_SCHWAB_FLAKY)
    def test_the_schwab_cta_is_still_caught(self, preroll_llm_spans):
        """Must not regress: this much the detector already gets right."""
        assert any_overlap(preroll_llm_spans, *_SCHWAB_CTA), \
            f"Schwab CTA {_SCHWAB_CTA} not detected. Spans: {preroll_llm_spans}"


class TestSchwabPrerollMustNotRemove:
    def test_the_news_bulletin_is_kept(self, preroll_llm_spans):
        """Fixing the pre-roll must not start eating the programme."""
        assert not any_overlap(preroll_llm_spans, _NEWS_STARTS + 2_000, 10 ** 9), \
            f"News content past {_NEWS_STARTS}ms was flagged. Spans: {preroll_llm_spans}"


# --- the Claude/Schwab mid-roll pod miss, found 2026-11-03 ---------------
#
# Same episode as the pre-roll case above, and the bigger miss of the two:
# 71.2s of the mid-roll pod shipped in the published episode.
#
# The pod runs 1041.4s-1237.2s (196s): Cheekies/Coca-Cola -> a Schwab brand
# ad -> Claude -> Wise. Production cut [1041.4-1108.4] and [1179.7-1237.2]
# and kept everything between, which is the Schwab "choice matters" read in
# full plus the entire Claude pitch up to its CTA.
#
# No single section holds the pod: it is 196s long and the section overlap is
# only 30s. Section 1 (570-1170s) holds its first 133s and section 2
# (1140-1740s) its last 99s, so the union of the two calls did cover it and
# complete detection was available. What happened instead is that each call
# dropped the part of the pod nearest its own section edge: section 1 ended
# its span mid-Schwab and section 2 started its span at the Claude CTA,
# skipping the 41.4s of Claude pitch that opens that window.
#
# So there is one fixture per section, each rebased so the section starts at
# 0. The two trailing segments that would spill past 600000ms are dropped,
# which only moves section 1's cut-off ~10s earlier — the real section also
# ended mid-Claude-ad.

_MIDROLL_SEC1_FIXTURE = (Path(__file__).parent / "fixtures"
                         / "global-news-trump_endorsement-midroll_sec1_transcript.json")
_MIDROLL_SEC2_FIXTURE = (Path(__file__).parent / "fixtures"
                         / "global-news-trump_endorsement-midroll_sec2_transcript.json")

# section 1 (fixture offset 568540ms)
_M1_COKE_POD = (472_819, 539_900)        # caught in production
_M1_SCHWAB_BRAND = (539_900, 558_420)    # kept; "that's just a small taste of
                                         # what Schwab offers... Visit Schwab.com"
_M1_CLAUDE_TO_EDGE = (558_780, 595_180)  # kept; runs to the window edge
_M1_NEWS_ENDS = 470_000

# section 2 (fixture offset 1138280ms)
_M2_CLAUDE_PITCH = (0, 41_380)           # kept; the window opens inside the ad
_M2_CLAUDE_CTA_AND_WISE = (41_380, 98_920)   # all that was caught
_M2_NEWS_STARTS = 98_920


def _spans_for(fixture: Path, episode: str):
    if not fixture.exists():
        pytest.skip(f"Fixture not found: {fixture}")
    with open(fixture) as f:
        raw = json.load(f)
    from skipcastify.services.audio_processor import Segment
    segments = [Segment(start=s["start"], end=s["end"], text=s["text"]) for s in raw]
    return detect(segments, episode)


@pytest.fixture(scope="module")
def midroll_sec1_spans():
    return _spans_for(_MIDROLL_SEC1_FIXTURE, _PREROLL_EPISODE + "_midroll_sec1")


@pytest.fixture(scope="module")
def midroll_sec2_spans():
    return _spans_for(_MIDROLL_SEC2_FIXTURE, _PREROLL_EPISODE + "_midroll_sec2")


class TestMidrollPodTrailingEdge:
    """Section 1: the pod continues to the end of the window.

    The two edge tests are xfail(strict=False) on purpose. Over four runs of
    this fixture, two stopped the span at 533920 (mid-Schwab, even earlier
    than production did) and two caught both the Schwab read and the Claude
    ad. A strict xfail would XPASS half the time and a plain assert would go
    red half the time; a non-strict xfail reports which way each run went
    without either. The intermittency is itself the finding — the trailing
    edge of a section is where this detector is unreliable.
    """

    @pytest.mark.xfail(strict=False, reason="trailing-edge miss, ~50% of runs")
    def test_the_schwab_brand_ad_is_flagged(self, midroll_sec1_spans):
        """The 18.5s Schwab read that shipped."""
        assert covered(midroll_sec1_spans, *_M1_SCHWAB_BRAND, tolerance_ms=2_000), \
            f"Schwab brand ad {_M1_SCHWAB_BRAND} not flagged. Spans: {midroll_sec1_spans}"

    @pytest.mark.xfail(strict=False, reason="trailing-edge miss, ~50% of runs")
    def test_the_claude_ad_running_past_the_window_is_flagged(self, midroll_sec1_spans):
        """An ad that is still going when the window ends is still an ad."""
        assert covered(midroll_sec1_spans, *_M1_CLAUDE_TO_EDGE, tolerance_ms=2_000), \
            f"Claude ad {_M1_CLAUDE_TO_EDGE} not flagged. Spans: {midroll_sec1_spans}"

    def test_the_coke_pod_is_still_caught(self, midroll_sec1_spans):
        """Must not regress: this much production got right."""
        assert covered(midroll_sec1_spans, *_M1_COKE_POD), \
            f"Coca-Cola pod {_M1_COKE_POD} not detected. Spans: {midroll_sec1_spans}"


class TestMidrollPodTrailingEdgeMustNotRemove:
    def test_the_midterms_news_is_kept(self, midroll_sec1_spans):
        assert not any_overlap(midroll_sec1_spans, 0, _M1_NEWS_ENDS), \
            f"News content before {_M1_NEWS_ENDS}ms was flagged. Spans: {midroll_sec1_spans}"


class TestMidrollPodLeadingEdge:
    """Section 2: the window opens 41.4s into the Claude ad."""

    @pytest.mark.xfail(strict=True,
                       reason="leading-edge miss: the span starts at the CTA "
                              "segment and is never expanded back through the "
                              "pitch that opens the window (7/7 runs)")
    def test_the_claude_pitch_opening_the_section_is_flagged(self, midroll_sec2_spans):
        """41.4s of Claude pitch, from the first line of the window to the CTA.

        Rule 6 of the prompt already says to expand backward from a hard CTA
        through the whole contiguous lead-in, and the CTA is right there at
        41380. It is not applied when the lead-in reaches the start of the
        window — every run returns exactly one span, [41380-98920].

        Already tried and rejected: adding a rule to the prompt spelling out
        that the window is one slice of a longer episode, that it may open
        mid-ad, and that start_ms then belongs at the first segment rather
        than at the CTA. Three more runs, same single [41380-98920] span. So
        the leading edge does not yield to a stated rule; it would need a
        worked example, or a text-aware second pass over the gaps between
        flagged spans. Do not re-run that experiment.
        """
        assert covered(midroll_sec2_spans, *_M2_CLAUDE_PITCH, tolerance_ms=2_000), \
            f"Claude pitch {_M2_CLAUDE_PITCH} not flagged. Spans: {midroll_sec2_spans}"

    def test_the_claude_cta_and_wise_ad_are_caught(self, midroll_sec2_spans):
        """Must not regress: this is the span production returned."""
        assert covered(midroll_sec2_spans, *_M2_CLAUDE_CTA_AND_WISE,
                       tolerance_ms=2_000), \
            f"Claude CTA + Wise {_M2_CLAUDE_CTA_AND_WISE} not detected. " \
            f"Spans: {midroll_sec2_spans}"


class TestMidrollPodLeadingEdgeMustNotRemove:
    def test_the_gaza_report_is_kept(self, midroll_sec2_spans):
        """The news resumes inside the last ad segment, so the guard starts
        just past it."""
        assert not any_overlap(midroll_sec2_spans, _M2_NEWS_STARTS + 2_000, 10 ** 9), \
            f"News content past {_M2_NEWS_STARTS}ms was flagged. " \
            f"Spans: {midroll_sec2_spans}"
