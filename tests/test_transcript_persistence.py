"""Tests that the pipeline persists Whisper transcripts to disk.

Transcript caching existed (TranscriptCache) but was only ever wired into the
preview tool — the production `AudioProcessor.process` never saved transcripts,
so regular pipeline episodes had no transcript on disk. These tests lock in the
fix: every processed episode writes data/transcripts/<episode>_transcript.json.
"""

import json
from unittest.mock import patch, MagicMock

import pytest

from skipcastify.services.audio_processor import AudioProcessor, Segment
from skipcastify.services.transcript_cache import TranscriptCache


def test_transcript_cache_round_trips_segments(tmp_path):
    """save_transcript writes a JSON file that load_cached_transcript reads back."""
    cache = TranscriptCache(str(tmp_path))
    segments = [Segment(start=0, end=5000, text="hello"),
                Segment(start=5000, end=10000, text="world")]

    cache.save_transcript("some-episode", segments)

    path = tmp_path / "some-episode_transcript.json"
    assert path.exists(), "transcript JSON was not written"

    on_disk = json.loads(path.read_text())
    assert on_disk == [
        {"start": 0, "end": 5000, "text": "hello"},
        {"start": 5000, "end": 10000, "text": "world"},
    ]

    loaded = cache.load_cached_transcript("some-episode")
    assert [(s.start, s.end, s.text) for s in loaded] == [
        (0, 5000, "hello"), (5000, 10000, "world")]


def test_transcript_cache_validates_source_hash(tmp_path):
    """With a source_path, the cache hits only when the audio is unchanged."""
    cache = TranscriptCache(str(tmp_path / "cache"))
    audio = tmp_path / "ep.mp3"
    audio.write_bytes(b"version one")
    cache.save_transcript("ep", [Segment(start=0, end=1000, text="hi")],
                          source_path=str(audio))

    # Same audio -> hit.
    assert cache.load_cached_transcript("ep", source_path=str(audio)) is not None
    # No source_path given -> no validation, still a hit (back-compat).
    assert cache.load_cached_transcript("ep") is not None
    # Audio changed -> cache invalidated.
    audio.write_bytes(b"version two is entirely different")
    assert cache.load_cached_transcript("ep", source_path=str(audio)) is None


def _run_process_with_mocks(tmp_path, segments, llm_spans):
    """Run AudioProcessor.process with every heavy stage mocked out.

    Only the transcription -> persistence seam is exercised for real; audio
    decode/encode, Whisper and the LLM are stubbed so the test stays fast and
    offline.
    """
    data_dir = tmp_path / "data"
    raw_dir = data_dir / "podcasts" / "raw" / "test-feed"
    raw_dir.mkdir(parents=True)
    episode_path = raw_dir / "test-feed-some_episode.mp3"
    episode_path.write_bytes(b"not really mp3")

    audio = MagicMock()
    audio.duration_seconds = 123.0
    processed = MagicMock()
    processed.duration_seconds = 100.0

    processor = AudioProcessor(str(data_dir))

    with patch.object(AudioProcessor, "load_and_validate_audio_file", return_value=audio), \
         patch.object(AudioProcessor, "_export_audio_to_wav"), \
         patch.object(AudioProcessor, "transcribe_with_whisper", return_value=segments), \
         patch.object(AudioProcessor, "_run_section_llm", return_value=llm_spans), \
         patch.object(AudioProcessor, "cut_and_stitch_audio", return_value=processed), \
         patch("skipcastify.services.audio_processor._source_bitrate", return_value="128k"), \
         patch("skipcastify.services.audio_processor.metrics.log_event"):
        processor.process(str(episode_path), state_manager=MagicMock())

    return data_dir / "transcripts" / "test-feed-some_episode_transcript.json"


def test_process_persists_transcript(tmp_path):
    """A full process() run writes the transcript to data/transcripts/."""
    segments = [Segment(start=0, end=5000, text="welcome to the show"),
                Segment(start=5000, end=9000, text="today we discuss things")]

    transcript_path = _run_process_with_mocks(tmp_path, segments, llm_spans=[])

    assert transcript_path.exists(), "process() did not persist the transcript"
    on_disk = json.loads(transcript_path.read_text())
    assert on_disk == [
        {"start": 0, "end": 5000, "text": "welcome to the show"},
        {"start": 5000, "end": 9000, "text": "today we discuss things"},
    ]


def test_process_reuses_cached_transcript(tmp_path):
    """When a transcript is already cached, process() must not re-run Whisper."""
    data_dir = tmp_path / "data"
    raw_dir = data_dir / "podcasts" / "raw" / "test-feed"
    raw_dir.mkdir(parents=True)
    episode_path = raw_dir / "test-feed-some_episode.mp3"
    episode_path.write_bytes(b"not really mp3")

    # Pre-seed the cache for this episode, tagged with the audio's hash.
    cache = TranscriptCache(str(data_dir / "transcripts"))
    cache.save_transcript("test-feed-some_episode",
                          [Segment(start=0, end=4000, text="cached content")],
                          source_path=str(episode_path))

    audio = MagicMock(); audio.duration_seconds = 50.0
    processed = MagicMock(); processed.duration_seconds = 40.0
    processor = AudioProcessor(str(data_dir))

    with patch.object(AudioProcessor, "load_and_validate_audio_file", return_value=audio), \
         patch.object(AudioProcessor, "_export_audio_to_wav"), \
         patch.object(AudioProcessor, "transcribe_with_whisper") as mock_transcribe, \
         patch.object(AudioProcessor, "_run_section_llm", return_value=[]), \
         patch.object(AudioProcessor, "cut_and_stitch_audio", return_value=processed), \
         patch("skipcastify.services.audio_processor._source_bitrate", return_value="128k"), \
         patch("skipcastify.services.audio_processor.metrics.log_event"):
        processor.process(str(episode_path), state_manager=MagicMock())

    mock_transcribe.assert_not_called()


def test_process_reruns_when_audio_changed(tmp_path):
    """If the raw audio is replaced (e.g. a re-download with different dynamic
    ads), the name-keyed cache must be invalidated and Whisper must re-run."""
    data_dir = tmp_path / "data"
    raw_dir = data_dir / "podcasts" / "raw" / "test-feed"
    raw_dir.mkdir(parents=True)
    episode_path = raw_dir / "test-feed-some_episode.mp3"
    episode_path.write_bytes(b"download A with ad X")

    # Cache a transcript built from download A.
    cache = TranscriptCache(str(data_dir / "transcripts"))
    cache.save_transcript("test-feed-some_episode",
                          [Segment(start=0, end=4000, text="from download A")],
                          source_path=str(episode_path))

    # The same episode name is re-downloaded with different bytes (different ads).
    episode_path.write_bytes(b"download B with a totally different ad Y")

    audio = MagicMock(); audio.duration_seconds = 50.0
    processed = MagicMock(); processed.duration_seconds = 40.0
    processor = AudioProcessor(str(data_dir))

    with patch.object(AudioProcessor, "load_and_validate_audio_file", return_value=audio), \
         patch.object(AudioProcessor, "_export_audio_to_wav"), \
         patch.object(AudioProcessor, "transcribe_with_whisper",
                      return_value=[Segment(start=0, end=4000, text="from download B")]) as mock_transcribe, \
         patch.object(AudioProcessor, "_run_section_llm", return_value=[]), \
         patch.object(AudioProcessor, "cut_and_stitch_audio", return_value=processed), \
         patch("skipcastify.services.audio_processor._source_bitrate", return_value="128k"), \
         patch("skipcastify.services.audio_processor.metrics.log_event"):
        processor.process(str(episode_path), state_manager=MagicMock())

    mock_transcribe.assert_called_once()


def test_process_persists_transcript_even_with_ads(tmp_path):
    """Transcript is saved regardless of whether the LLM found ad spans."""
    segments = [Segment(start=0, end=5000, text="buy our sponsor product"),
                Segment(start=5000, end=9000, text="and now the real content")]

    transcript_path = _run_process_with_mocks(
        tmp_path, segments,
        llm_spans=[{"start_ms": 0, "end_ms": 5000, "label": "ADVERTISEMENT"}],
    )

    assert transcript_path.exists()
    on_disk = json.loads(transcript_path.read_text())
    assert len(on_disk) == 2
    assert on_disk[0]["text"] == "buy our sponsor product"
