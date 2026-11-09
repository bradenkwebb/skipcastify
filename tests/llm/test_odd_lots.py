"""LLM regression tests for Odd Lots.

Episode: an_election_day_special_with_nate_silver, which Braden reported as
shipping with ads left in. Four leaks, ~25s in total, and they split in two:
the 4.2s Verbo opening line and the 4.0s Amex opening line are caught
reliably now and were transient, while the two below are reproducible misses
at 0% coverage in every run measured (6 runs on each of two earlier prompts,
4 more on dev's).

Both are confirmed ads by Braden's reviewed hand labels (v2, 2026-11-05):
his ad spans are [0-125460], [985250-1242290], [1912429-2164550] and
[3218510-3341670].

The two misses sit at opposite edges of an ad block, which is the same shape
as the Global News mid-roll pod:

  [0-13740]           a 13.7s sponsored teaser that opens the episode, with no
                      brand, no URL and no CTA — "can regulation not only keep
                      pace but help accelerate it, find out more later in the
                      podcast". It teases a sponsored segment later in the
                      show, so there is nothing in the text to key on; it
                      reads as editorial framing. The Verbo ad that follows it
                      is caught, so the block is flagged from 15400 instead.

  [1239130-1242290]   a 3.2s Indeed sign-off ident, "Need to hire, this is a
                      job for Indeed sponsored jobs", immediately after the
                      full Indeed read. The read itself is caught in full,
                      every run; the span just stops 3.2s short of it.

Run with: pytest tests/llm/test_odd_lots.py --run-llm
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
_EPISODE = "odd-lots-an_election_day_special_with_nate_silver"


def _load_segments(path: Path):
    if not path.exists():
        pytest.skip(f"Fixture not found: {path}")
    with open(path) as f:
        raw = json.load(f)
    from skipcastify.services.audio_processor import Segment
    return [Segment(start=s["start"], end=s["end"], text=s["text"]) for s in raw]


def _detect(segments):
    from skipcastify.services.audio_processor import AudioProcessor
    processor = AudioProcessor("data")
    with tempfile.TemporaryDirectory() as tmp:
        return processor._run_section_llm(segments, tmp, episode_name=_EPISODE)


# --- the sponsored teaser that opens the episode (section 0, offset 0) ---

_TEASER = (0, 13_740)
_PREROLL_REST = (15_400, 125_460)        # Verbo, then the Bloomberg Tech Minute
_PREROLL_EDITORIAL_STARTS = 145_640      # "Hello and welcome to another episode"


@pytest.fixture(scope="module")
def preroll_spans():
    return _detect(_load_segments(
        _FIXTURES / "odd-lots-election_special-sponsor_teaser_preroll_transcript.json"))


class TestSponsorTeaserMustDetect:
    @pytest.mark.xfail(strict=True, reason="known miss: the teaser names no "
                                           "brand and gives no CTA (0% in 14 runs)")
    def test_the_sponsored_teaser_is_flagged(self, preroll_spans):
        """13.7s of sponsored tease shipped at the very top of the episode.

        Rule 2 of the prompt says to flag content inserts "that run 15 seconds
        or longer" and that "a brief 5-second transition line is fine to
        keep". At 13.7s this falls in the gap between those two, which is the
        most likely cause and the reason this is recorded rather than fixed:
        the per-show policy refactor is meant to take these thresholds out of
        the prompt, so moving the floor now would only have to be undone.
        """
        assert covered(preroll_spans, *_TEASER, tolerance_ms=1_000), \
            f"Sponsored teaser {_TEASER} not flagged. Spans: {preroll_spans}"

    def test_the_rest_of_the_preroll_is_still_caught(self, preroll_spans):
        """Must not regress: 92-96% covered across runs."""
        assert covered(preroll_spans, *_PREROLL_REST), \
            f"Pre-roll block {_PREROLL_REST} not covered. Spans: {preroll_spans}"


class TestSponsorTeaserMustNotRemove:
    def test_the_episode_open_is_kept(self, preroll_spans):
        """Joe and Tracy's open must survive fixing the teaser."""
        assert not any_overlap(preroll_spans,
                               _PREROLL_EDITORIAL_STARTS + 2_000, 10 ** 9), \
            f"Editorial after {_PREROLL_EDITORIAL_STARTS}ms was flagged. " \
            f"Spans: {preroll_spans}"


# --- the Indeed sign-off ident (section 2, offset 1135950) ---------------

_IDENT = (103_180, 106_340)
_INDEED_BODY = (85_700, 102_900)
_MIDROLL_EDITORIAL_STARTS = 107_340      # "I feel like we've been talking about this"


@pytest.fixture(scope="module")
def midroll_spans():
    return _detect(_load_segments(
        _FIXTURES / "odd-lots-election_special-indeed_ident_midroll_transcript.json"))


class TestIndeedIdentMustDetect:
    @pytest.mark.xfail(strict=True, reason="known miss: trailing 3.2s ident "
                                           "dropped from the end of the span "
                                           "(0% in 10 runs)")
    def test_the_sign_off_ident_is_flagged(self, midroll_spans):
        """A trailing-edge miss: the ad is caught, its last 3.2s is not.

        Same family as the Global News mid-roll pod. The ident repeats the
        line the ad already used, so there is no new information in it for the
        model to catch — which is why a distance-based fix would get this and
        a text-based one might not.
        """
        assert covered(midroll_spans, *_IDENT, tolerance_ms=500), \
            f"Indeed ident {_IDENT} not flagged. Spans: {midroll_spans}"

    def test_the_indeed_read_itself_is_still_caught(self, midroll_spans):
        """Must not regress: 100% covered in every run measured."""
        assert covered(midroll_spans, *_INDEED_BODY), \
            f"Indeed read {_INDEED_BODY} not covered. Spans: {midroll_spans}"


class TestIndeedIdentMustNotRemove:
    def test_the_conversation_after_the_break_is_kept(self, midroll_spans):
        """Only 1.0s separates the ident from Tracy's next question, so
        whatever fixes the ident must stop inside that gap."""
        assert not any_overlap(midroll_spans,
                               _MIDROLL_EDITORIAL_STARTS + 1_000, 10 ** 9), \
            f"Conversation after {_MIDROLL_EDITORIAL_STARTS}ms was flagged. " \
            f"Spans: {midroll_spans}"
