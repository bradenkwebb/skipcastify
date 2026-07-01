"""Tests for cut_and_stitch_audio: it must remove ONLY ad ranges and keep the
rest of the audio (content, music, silence, pauses) intact."""

import pytest
from pydub import AudioSegment
from pydub.generators import Sine

from skipcastify.services.audio_processor import AudioProcessor
from skipcastify.services.segment_classifier import AggregatedSegment
from skipcastify.models.content import ContentType


def _seg(start, end, content_type):
    return AggregatedSegment(start=start, end=end, text=[""],
                             content_type=content_type, segment_count=1)


@pytest.fixture
def processor():
    return AudioProcessor("data")


def test_only_ad_range_removed_gaps_preserved(processor):
    """Non-ad audio outside the speech segments (the gaps) must be kept.

    10s of silence with a 440Hz tone at [3000-5000] marked as an ad. The old
    behavior rebuilt from content segments and would yield 4s; the new behavior
    removes only the 2s ad, keeping the 8s of everything-else (including the
    [2000-3000], [5000-6000], [8000-10000] gaps that no segment covers).
    """
    fr = 44100  # keep every piece at the same frame rate so ms boundaries align
    audio = (AudioSegment.silent(duration=3000, frame_rate=fr)
             + Sine(440, sample_rate=fr).to_audio_segment(duration=2000)   # the "ad" at 3-5s
             + AudioSegment.silent(duration=5000, frame_rate=fr))
    assert len(audio) == 10000

    segments = [
        _seg(0, 2000, ContentType.CONTENT),
        _seg(3000, 5000, ContentType.ADVERTISEMENT),
        _seg(6000, 8000, ContentType.CONTENT),
    ]
    result = processor.cut_and_stitch_audio(audio, segments)

    assert len(result) == 8000, "should remove only the 2s ad, keeping gaps"
    assert result.max_dBFS == float("-inf"), "the tone (ad) should be gone; rest is silence"


def test_no_ads_returns_original_unchanged(processor):
    audio = AudioSegment.silent(duration=5000)
    segments = [_seg(0, 2000, ContentType.CONTENT), _seg(3000, 5000, ContentType.CONTENT)]
    result = processor.cut_and_stitch_audio(audio, segments)
    assert len(result) == 5000


def test_overlapping_ad_ranges_merged(processor):
    """Overlapping ad spans are merged so their union is removed exactly once."""
    audio = AudioSegment.silent(duration=10000)
    segments = [
        _seg(3000, 5000, ContentType.ADVERTISEMENT),
        _seg(4000, 6000, ContentType.SPONSOR),   # overlaps the previous
    ]
    result = processor.cut_and_stitch_audio(audio, segments)
    assert len(result) == 7000, "union [3000-6000] (3s) removed once"


def test_empty_segments_returns_original(processor):
    audio = AudioSegment.silent(duration=4000)
    assert len(processor.cut_and_stitch_audio(audio, [])) == 4000


def test_ad_at_end_removed(processor):
    """An ad running to the end of the file is removed without index errors."""
    audio = AudioSegment.silent(duration=8000)
    segments = [_seg(0, 6000, ContentType.CONTENT), _seg(6000, 8000, ContentType.ADVERTISEMENT)]
    result = processor.cut_and_stitch_audio(audio, segments)
    assert len(result) == 6000
