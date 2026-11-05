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
    """Both of these were xfail(strict=True) known misses until 2026-11-05.

    Example L of the prompt, added for the 9.2s NYT offer in the same episode,
    fixed them as a side effect: 8/8 runs with it, 0/8 without.
    """

    def test_pod_is_caught_from_its_first_word(self, midroll_llm_spans):
        earliest = min((s["start_ms"] for s in midroll_llm_spans), default=None)
        assert earliest is not None and earliest <= _POD_START + 3_000, (
            f"Pod starts at {_POD_START}ms but the earliest span is "
            f"{earliest}ms. Spans: {midroll_llm_spans}")

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

    @pytest.mark.xfail(strict=False, reason="pre-existing span-edge defect: "
                                            "18.2s of the interview is flagged "
                                            "in some runs, Example L or not")
    def test_editorial_after_the_pod_kept(self, midroll_llm_spans):
        """Musk-Trump alliance discussion, resuming at 3:26 in the clip.

        The model emits a further span [205100-226760] over Klein's next
        question, 18.2s past the pod. Measured 2026-11-05 with both prompt
        arms interleaved round-robin, 8 rounds: kept 7/8 with Example L and
        2/8 without it, so the defect is not Example L's and removing the
        example makes it worse. An earlier sequential measurement said the
        opposite and was confounded by drift in the model's dominant output
        mode; see the note in conftest.py. The fix is the text-aware span-edge
        trim, not prompt text: already rejected are a boundary sentence in
        Example L's note (still 2/5) and a "return one span per ad break"
        instruction (dropped pod detection to 2/6). Do not re-run those.
        """
        assert not any_overlap(midroll_llm_spans, _CONTENT_AFTER_START + 2_000,
                               10 ** 9), (
            f"Editorial after the pod was flagged. Spans: {midroll_llm_spans}")


# --- episode 3: NYT house ads, the_end_of_trumpism -----------------------
#
# Braden's ruling changed on 2026-11-05. In June and again on 2026-11-03 the
# call was that NYT self-promotion could stay, on the grounds that it promotes
# the publisher rather than a third party. After hearing this episode he
# reversed it: the spot "was quite long and not read by the host. I would have
# preferred if it were removed." So house ads are now cut.
#
# This episode shipped with two of them, both classified as content:
#   [0-32479]          32.5s pre-roll read by the NYT's publisher, appealing
#                      for news subscriptions. No URL, no promo code, and it
#                      generously tells you to support your local paper too.
#   [1276820-1332480]  55.7s mid-roll read by Anna Martin promoting the shows
#                      tab in the NYT app. No URL either — "it's in the New
#                      York Times app" is the whole CTA — and it spends its
#                      first 20 seconds riffing on the phrase "wherever you
#                      get your podcasts", which Example E of the prompt
#                      protects as editorial.
#
# Both were invisible to the old prompt: 3 runs each, zero spans returned.
# That is not a boundary or a merge-gap problem, it was policy — the prompt
# treated a network promoting its own shows as editorial cross-promotion
# unless there was sponsor attribution, a partner URL or a promo code, and a
# house ad has none of those by definition.
#
# Rule 7 plus Examples J and K now cover it. Both cases are plain guards
# rather than xfails because the fix ships with them: 5/5 runs on the pre-roll
# and 3/3 on the app promo after the change.
#
# One fixture per section, each rebased so the section starts at 0.

_PREROLL_HOUSE_FIXTURE = _FIXTURES / "ezra-klein-end_of_trumpism-publisher_preroll_transcript.json"
_MIDROLL_HOUSE_FIXTURE = _FIXTURES / "ezra-klein-end_of_trumpism-app_promo_midroll_transcript.json"
_HOUSE_EPISODE = "the-ezra-klein-show-the_end_of_trumpism"

_PUBLISHER_SPOT = (0, 32_479)
_PUBLISHER_EDITORIAL_STARTS = 58_980

# section 2, fixture offset 1135600ms
_APP_PROMO = (141_220, 196_880)
_APP_PROMO_EDITORIAL_STARTS = 204_180   # "Okay, can I riff for one second?"


@pytest.fixture(scope="module")
def publisher_preroll_spans():
    segments = _load_segments(_PREROLL_HOUSE_FIXTURE)
    return _detect(segments, _HOUSE_EPISODE + "_publisher_preroll")


@pytest.fixture(scope="module")
def app_promo_spans():
    segments = _load_segments(_MIDROLL_HOUSE_FIXTURE)
    return _detect(segments, _HOUSE_EPISODE + "_app_promo")


class TestPublisherPrerollMustDetect:
    def test_the_publisher_spot_is_flagged(self, publisher_preroll_spans):
        """A produced spot by the publisher, not the host, asking you to
        subscribe. Public-interest framing is not a defence."""
        assert covered(publisher_preroll_spans, *_PUBLISHER_SPOT, tolerance_ms=2_000), \
            f"Publisher pre-roll {_PUBLISHER_SPOT} not flagged. Spans: {publisher_preroll_spans}"


class TestPublisherPrerollMustNotRemove:
    def test_the_episode_opening_is_kept(self, publisher_preroll_spans):
        """Editorial starts at 58980 ("The Big Ten is real...") and the rest of
        this section is the interview. Cutting a house ad must not start eating
        the show."""
        assert not any_overlap(publisher_preroll_spans,
                               _PUBLISHER_EDITORIAL_STARTS + 2_000, 10 ** 9), \
            f"Editorial past {_PUBLISHER_EDITORIAL_STARTS}ms was flagged. " \
            f"Spans: {publisher_preroll_spans}"


class TestAppPromoMustDetect:
    def test_the_app_promo_is_flagged(self, app_promo_spans):
        """55.7s promoting the publisher's own app, with no URL and no sponsor."""
        assert covered(app_promo_spans, *_APP_PROMO, tolerance_ms=2_000), \
            f"NYT app promo {_APP_PROMO} not flagged. Spans: {app_promo_spans}"

    def test_the_wherever_you_get_your_podcasts_opening_is_included(self, app_promo_spans):
        """The spot opens with 20s of riffing on "wherever you get your
        podcasts" before naming the app. Example E protects that phrase in a
        host's outro; it must not protect a produced spot built around it."""
        assert covered(app_promo_spans, _APP_PROMO[0], _APP_PROMO[0] + 20_000,
                       tolerance_ms=2_000), \
            f"The opening of the house ad was not flagged. Spans: {app_promo_spans}"


class TestAppPromoMustNotRemove:
    def test_the_conversation_after_the_promo_is_kept(self, app_promo_spans):
        """The ad's closing line and "Okay, can I riff for one second?" share a
        Whisper segment, so the guard starts after it."""
        assert not any_overlap(app_promo_spans,
                               _APP_PROMO_EDITORIAL_STARTS + 2_000, 10 ** 9), \
            f"Conversation past {_APP_PROMO_EDITORIAL_STARTS}ms was flagged. " \
            f"Spans: {app_promo_spans}"

    def test_the_conversation_before_the_promo_is_kept(self, app_promo_spans):
        """141220ms in, the window opens on the interview — 2.3 minutes of it
        before the house ad starts."""
        assert not any_overlap(app_promo_spans, 0, _APP_PROMO[0] - 2_000), \
            f"Conversation before the house ad was flagged. Spans: {app_promo_spans}"


# --- episode 2 again: the 9.2s NYT subscription offer --------------------
#
# Braden's hand label (reviewed, 2026-11-05) makes the Musk episode's mid-roll
# ad span 1268400-1366940: a 9.2s produced NYT subscription offer, then
# Betterment, SOTFA and Made In. Rule 7 originally had a 15-second floor, so
# the offer sat deliberately outside it and shipped. His call: cut it. The
# floor is gone, and length is no longer part of the house-ad test.
#
# Removing the floor alone changed nothing (0% coverage, 3 runs before and
# after). What moved it was Example L, a worked example of exactly this shape.
# Third time this has held: stated rules alone do not reach this model where a
# worked example does.
#
# Offsets are fixture-relative; section 2 starts at 1135840ms in the episode.

_NYT_PROMO_FIXTURE = _FIXTURES / "ezra-klein-elon_musks_endgame-nyt_promo_midroll_transcript.json"

_NYT_OFFER = (132_560, 141_780)          # "...explore the Times for free"
_NYT_POD_END = 231_100                   # end of Made In, = label end
_NYT_BETTERMENT_END = 171_680            # the sponsor read that is always caught
_NYT_SPONSORS_START = 151_200            # Betterment, already caught
_NYT_EDITORIAL_BEFORE_END = 130_000
_NYT_EDITORIAL_AFTER_START = 233_420


@pytest.fixture(scope="module")
def nyt_promo_spans():
    segments = _load_segments(_NYT_PROMO_FIXTURE)
    return _detect(segments, _MIDROLL_EPISODE)


class TestNytOfferMustDetect:
    def test_the_nine_second_offer_is_flagged(self, nyt_promo_spans):
        """0/3 runs under the 15s floor, 4/4 with Example L."""
        assert covered(nyt_promo_spans, *_NYT_OFFER, tolerance_ms=1_000), \
            f"The NYT subscription offer {_NYT_OFFER} was not flagged. " \
            f"Spans: {nyt_promo_spans}"

    def test_the_break_is_flagged_from_the_offer_not_the_first_sponsor(self, nyt_promo_spans):
        """The offer opens the break; a span starting at Betterment leaves it in."""
        earliest = min((s["start_ms"] for s in nyt_promo_spans), default=None)
        assert earliest is not None and earliest <= _NYT_OFFER[0] + 1_000, (
            f"Break flagged from {earliest}ms, but it opens at {_NYT_OFFER[0]}ms. "
            f"Spans: {nyt_promo_spans}")

    def test_the_betterment_read_is_still_caught(self, nyt_promo_spans):
        """Must not regress: this much was already right before the change.

        Only the Betterment read is guarded. The SOTFA and Made In reads that
        follow it drop in and out run to run (whole-pod coverage measured
        between 39.7% and 89.7% over 6 runs), which predates this change.
        """
        assert covered(nyt_promo_spans, _NYT_SPONSORS_START, _NYT_BETTERMENT_END), \
            f"Betterment read [{_NYT_SPONSORS_START}-{_NYT_BETTERMENT_END}ms] not "\
            f"covered. Spans: {nyt_promo_spans}"


class TestNytOfferMustNotRemove:
    def test_the_interview_before_the_break_is_kept(self, nyt_promo_spans):
        assert not any_overlap(nyt_promo_spans, 0, _NYT_EDITORIAL_BEFORE_END), \
            f"Interview before the break was flagged. Spans: {nyt_promo_spans}"

    def test_the_interview_after_the_break_is_kept(self, nyt_promo_spans):
        assert not any_overlap(nyt_promo_spans,
                               _NYT_EDITORIAL_AFTER_START + 2_000, 10 ** 9), \
            f"Interview after the break was flagged. Spans: {nyt_promo_spans}"
