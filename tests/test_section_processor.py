"""Sections are the window the model is shown, so their bounds are load-bearing.

The bounds are quoted into the prompt ("section 1140000ms-1740000ms") and are
what an LLM span is clamped to, so a section must not claim time the audio
does not have.
"""

from skipcastify.services import section_processor
from skipcastify.services.audio_processor import Segment


def _sections(minutes):
    segments = [Segment(start=i * 60_000, end=(i + 1) * 60_000, text=f"line {i}")
                for i in range(minutes)]
    return segments, section_processor.make_sections_from_segments(segments)


def test_no_section_runs_past_the_end_of_the_audio():
    segments, sections = _sections(20)
    audio_end = max(s.end for s in segments)
    assert sections, "20 minutes of speech produced no sections"
    overrun = [(s.start_ms, s.end_ms) for s in sections if s.end_ms > audio_end]
    assert not overrun, (
        f"audio ends at {audio_end}ms but sections claim {overrun}")


def test_the_last_section_ends_exactly_at_the_audio_end():
    segments, sections = _sections(20)
    assert sections[-1].end_ms == max(s.end for s in segments)


def test_a_short_episode_is_one_section_of_its_own_length():
    """Shorter than one window: the section is the episode, not 10 minutes."""
    segments, sections = _sections(3)
    assert len(sections) == 1
    assert (sections[0].start_ms, sections[0].end_ms) == (0, 180_000)


def test_sections_still_overlap():
    """The 30s overlap is what gives a boundary-straddling ad a second look."""
    _, sections = _sections(20)
    for earlier, later in zip(sections, sections[1:]):
        assert later.start_ms < earlier.end_ms


def test_every_segment_lands_in_a_section():
    segments, sections = _sections(20)
    covered = {i for s in sections for i in s.segment_indices}
    assert covered == set(range(len(segments)))
