
from dataclasses import dataclass
from enum import Enum


class ContentType(Enum):
    """Enum representing different content types within a podcast episode."""
    INTRO = "intro"
    CONTENT = "content"
    ADVERTISEMENT = "advertisement"
    SPONSOR = "sponsor_message"
    OUTRO = "outro"


@dataclass
class AudioSegment:
    """Represents a segment of audio within an episode."""
    start_time: float  # in seconds
    end_time: float  # in seconds
    segment_type: ContentType
    confidence_score: float = 1.0  # confidence in classification (0.0-1.0)
    
    @property
    def duration(self) -> float:
        """Return the duration of this segment in seconds."""
        return self.end_time - self.start_time