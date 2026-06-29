"""
Section processing utilities:
- Chunk Whisper segments into ~10-minute windows with overlap
- Simple binary keyword-based section classifier to mark sections for LLM review
- Helpers to format a section as a time-coded transcript string for the LLM prompt
"""

from dataclasses import dataclass
from typing import List, Tuple
import math
import re
import logging

logger = logging.getLogger(__name__)


@dataclass
class Section:
    start_ms: int
    end_ms: int
    segment_indices: List[int]
    text_lines: List[str]  # lines already formatted as "[start - end] text"


DEFAULT_SECTION_MS = 10 * 60 * 1000  # 10 minutes
DEFAULT_OVERLAP_MS = 30 * 1000  # 30 seconds

# Simple keyword patterns to mark sections for review
SECTION_KEYWORD_PATTERNS = [
    r"\bbrought to you by\b",
    r"\bsponsored by\b",
    r"\buse code\b",
    r"\bvisit [a-z0-9_-]+\.(com|io|co)\b",
    r"\bpromo\b",
    r"\bdiscount\b",
    r"\bthank you to\b",
    r"\bthis episode is brought to you\b",
]


def make_sections_from_segments(
    segments: List,  # list of Segment objects with start/end/text (ms)
    section_ms: int = DEFAULT_SECTION_MS,
    overlap_ms: int = DEFAULT_OVERLAP_MS,
) -> List[Section]:
    """Chunk segments into sections and return list of Section objects.

    segments: list of objects with start (ms), end (ms), text (str)
    """
    if not segments:
        return []

    audio_end = max(s.end for s in segments)
    step = section_ms - overlap_ms
    if step <= 0:
        raise ValueError("section_ms must be greater than overlap_ms")

    sections: List[Section] = []
    start = 0
    seg_count = len(segments)

    while start < audio_end:
        end = start + section_ms
        # find segment indices whose start is < end and end > start (overlap)
        idxs = []
        lines = []
        for i, seg in enumerate(segments):
            if seg.start < end and seg.end > start:
                idxs.append(i)
                # Clip to section bounds for display, but keep original times
                lines.append(f"[{seg.start} - {seg.end}] {seg.text}")
        if idxs:
            sections.append(Section(start_ms=start, end_ms=end, segment_indices=idxs, text_lines=lines))
        start += step
    logger.info("Created %d sections from segments (section_ms=%d, overlap_ms=%d)", len(sections), section_ms, overlap_ms)
    return sections


def section_to_prompt_text(section: Section) -> str:
    """Format a section for LLM input: join time-coded lines into one string."""
    return "\n".join(section.text_lines)


def is_section_suspicious(section: Section) -> bool:
    """Simple binary classifier: True if section likely contains an ad.

    Uses keyword pattern presence. Returns True if any pattern matches.
    """
    joined = "\n".join(section.text_lines).lower()
    for pat in SECTION_KEYWORD_PATTERNS:
        if re.search(pat, joined, re.IGNORECASE):
            return True
    return False
