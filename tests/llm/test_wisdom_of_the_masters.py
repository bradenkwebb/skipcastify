"""LLM regression tests for Wisdom of the Masters — a NEGATIVE (ad-free) feed.

This podcast is structurally ad-free: episodes are guided meditations and
readings from spiritual teachers, with no sponsors, no host-read ads, and no
CTAs. The detector must return ZERO ad spans for these episodes. (The feed
previously showed a spurious ~25% "ad density" only because the silence-stripping
stitcher dropped the meditative pauses — not because any ads were detected.)

These cases guard against false positives: a change that makes the prompt more
aggressive (e.g. the pre-roll / lead-in work) must not start flagging quiet
contemplative speech as advertising.

Run with: pytest tests/llm/test_wisdom_of_the_masters.py --run-llm
"""

import json
import tempfile
from pathlib import Path

import pytest
from dotenv import load_dotenv

load_dotenv()
pytestmark = pytest.mark.llm

_FIXTURES = Path(__file__).parent / "fixtures"


def _spans_for(fixture_name: str) -> list[dict]:
    path = _FIXTURES / fixture_name
    if not path.exists():
        pytest.skip(f"Fixture not found: {path}")
    raw = json.loads(path.read_text())
    from skipcastify.services.audio_processor import AudioProcessor, Segment
    segments = [Segment(start=s["start"], end=s["end"], text=s["text"]) for s in raw]
    processor = AudioProcessor("data")
    with tempfile.TemporaryDirectory() as tmp:
        return processor._run_section_llm(segments, tmp, episode_name=fixture_name)


@pytest.fixture(scope="module")
def desert_mothers_spans():
    return _spans_for("wisdom-of-the-masters-sayings_of_the_desert_mothers_christian_mystics_transcript.json")


@pytest.fixture(scope="module")
def ajahn_chah_spans():
    return _spans_for("wisdom-of-the-masters-ajahn_chah_a_guided_meditation_thai_forest_tradition_transcript.json")


class TestMustNotDetect:
    def test_desert_mothers_has_no_ads(self, desert_mothers_spans):
        assert desert_mothers_spans == [], \
            f"Ad-free meditation falsely flagged. Spans: {desert_mothers_spans}"

    def test_ajahn_chah_has_no_ads(self, ajahn_chah_spans):
        assert ajahn_chah_spans == [], \
            f"Ad-free guided meditation falsely flagged. Spans: {ajahn_chah_spans}"
