"""process() marks an episode whose audio came back with nothing cut."""

from unittest.mock import MagicMock, patch

import pytest

from skipcastify.services.audio_processor import AudioProcessor, Segment
from skipcastify.services.state_manager import uncut_marker

SEGMENTS = [Segment(start=0, end=5_000, text="hello")]


def run(tmp_path, cut: bool):
    data_dir = tmp_path / "data"
    raw = data_dir / "podcasts" / "raw" / "feed"
    raw.mkdir(parents=True)
    ep = raw / "feed-ep.mp3"
    ep.write_bytes(b"x")
    audio = MagicMock(); audio.duration_seconds = 60.0
    out = MagicMock(); out.duration_seconds = 50.0
    with patch.object(AudioProcessor, "load_and_validate_audio_file", return_value=audio), \
         patch.object(AudioProcessor, "_export_audio_to_wav"), \
         patch.object(AudioProcessor, "transcribe_with_whisper", return_value=SEGMENTS), \
         patch.object(AudioProcessor, "_run_section_llm", return_value=[]), \
         patch.object(AudioProcessor, "cut_and_stitch_audio", lambda self, a, s: out if cut else a), \
         patch("skipcastify.services.audio_processor._source_bitrate", return_value="128k"), \
         patch("skipcastify.services.audio_processor.metrics.log_event"):
        AudioProcessor(str(data_dir)).process(str(ep), state_manager=MagicMock())
    return uncut_marker(data_dir / "podcasts" / "processed" / "feed" / "feed-ep.mp3")


@pytest.mark.parametrize("cut,marked", [(False, True), (True, False)])
def test_marker_only_when_nothing_was_cut(tmp_path, cut, marked):
    assert run(tmp_path, cut).exists() is marked
