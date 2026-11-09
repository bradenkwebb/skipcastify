"""
Segment classification service for identifying ad segments in podcast transcriptions.

Keyword-based heuristics. This runs only when every LLM section call failed —
never as a second opinion on an LLM that answered.
"""

import logging
import re
from dataclasses import dataclass
from typing import List
from skipcastify.models.content import ContentType


logger = logging.getLogger(__name__)


@dataclass
class ClassifiedSegment:
    """A segment with its classification and confidence score."""
    start: int              # milliseconds
    end: int                # milliseconds
    text: str
    content_type: ContentType
    confidence: float       # 0.0-1.0


@dataclass
class AggregatedSegment:
    """An aggregated segment containing one or more consecutive segments of same type."""
    start: int              # milliseconds
    end: int                # milliseconds
    text: List[str]         # List of original segment texts
    content_type: ContentType
    segment_count: int      # Number of original segments merged


class SegmentClassifier:
    """Classifies podcast transcript segments as content, ads, intros, outros, etc."""
    
    # Keyword patterns for classification
    ADVERTISEMENT_KEYWORDS = {
        r'\b(sponsor|sponsorship|brought to you by|presented by)\b',
        r'\b(ad|advertisement|commercial|promotion)\b',
        r'\b(discount|promo|code|offer|deal|limited time)\b',
        r'\b(try|sign up|visit|check out)\s*\w+\.(com|io|co)',  # URLs
        r'\b(this episode.*powered by)\b',
        r'\b(for \d+% off|use code|link in the?.*description)\b',
    }
    
    SPONSOR_KEYWORDS = {
        r'\b(sponsor|sponsorship)\b',
        r'\b(thank you to)\b',
        r'\b(made possible by)\b',
    }
    
    INTRO_KEYWORDS = {
        r'\bintro(duction)?\b',
        r'\bwelcome (back )?(to|on)\b',
        r'\bthis is\s+\w+\s*(podcast|show)',
        r'\btoday on\b',
        r'\bhello everyone\b',
        r'\byou.*listening to\b',
    }
    
    OUTRO_KEYWORDS = {
        r'\b(outro|thanks for listening|thanks for joining)\b',
        r'\buntil next (time|week|episode)\b',
        r'\b(goodbye|see you|catch you)\b',
        r'\bthank you for listening\b',
        r'\bfrom all of us\b',
    }
    
    def classify_segments(self, segments: List) -> List[ClassifiedSegment]:
        """
        Classify a list of transcript segments.
        
        Args:
            segments: List of Segment objects from transcription
            
        Returns:
            List of ClassifiedSegment objects with content types and confidence scores
        """
        classified = []
        
        for segment in segments:
            content_type, confidence = self._classify_single_segment(segment.text)
            classified_seg = ClassifiedSegment(
                start=segment.start,
                end=segment.end,
                text=segment.text,
                content_type=content_type,
                confidence=confidence
            )
            classified.append(classified_seg)
            logger.debug(f"Classified [{segment.start}ms-{segment.end}ms]: {content_type.value} (confidence: {confidence:.2f})")
        
        return classified
    
    def _classify_single_segment(self, text: str) -> tuple[ContentType, float]:
        """
        Classify a single segment text.
        """
        text_lower = text.lower()
        
        # Check for intro patterns
        intro_matches = self._count_pattern_matches(text_lower, self.INTRO_KEYWORDS)
        if intro_matches > 0:
            return ContentType.INTRO, 0.9
        
        # Check for outro patterns
        outro_matches = self._count_pattern_matches(text_lower, self.OUTRO_KEYWORDS)
        if outro_matches > 0:
            return ContentType.OUTRO, 0.9
        
        # Check for sponsor patterns (higher specificity than generic ads)
        sponsor_matches = self._count_pattern_matches(text_lower, self.SPONSOR_KEYWORDS)
        if sponsor_matches > 0:
            return ContentType.SPONSOR, 0.85
        
        # Check for advertisement patterns
        ad_matches = self._count_pattern_matches(text_lower, self.ADVERTISEMENT_KEYWORDS)
        if ad_matches > 0:
            # High confidence if multiple patterns match
            confidence = min(0.95, 0.75 + (ad_matches * 0.05))
            return ContentType.ADVERTISEMENT, confidence
        
        # Default to content
        return ContentType.CONTENT, 0.8
    
    def _count_pattern_matches(self, text: str, patterns: set) -> int:
        """Count how many patterns match in the given text."""
        count = 0
        for pattern in patterns:
            if re.search(pattern, text, re.IGNORECASE):
                count += 1
        return count
    
    def aggregate_segments(
        self,
        classified_segments: List[ClassifiedSegment],
        gap_threshold_ms: int = 1500,
    ) -> List[AggregatedSegment]:
        """
        Aggregate consecutive segments of the same type to reduce fragmentation.

        Args:
            classified_segments: List of ClassifiedSegment objects
            gap_threshold_ms: Max gap between same-type segments to merge (default: 1.5s)

        Returns:
            List of AggregatedSegment objects
        """
        if not classified_segments:
            return []

        aggregated = []
        current_group = None

        for seg in classified_segments:
            if (current_group is not None
                    and seg.content_type == current_group['type']
                    and seg.start - current_group['end'] <= gap_threshold_ms):
                current_group['end'] = seg.end
                current_group['text'].append(seg.text)
                current_group['count'] += 1
                continue

            # A different type, or too long a gap: close the group and open one
            # here. Intros and outros are preserved, never absorbed.
            if current_group is not None:
                aggregated.append(self._create_aggregated_segment(current_group))
            current_group = {
                'start': seg.start,
                'end': seg.end,
                'text': [seg.text],
                'type': seg.content_type,
                'count': 1,
            }

        if current_group is not None:
            aggregated.append(self._create_aggregated_segment(current_group))

        logger.info(f"Aggregated {len(classified_segments)} segments into {len(aggregated)} groups")
        return aggregated

    def _create_aggregated_segment(self, group: dict) -> AggregatedSegment:
        """Create an AggregatedSegment from a group dictionary."""
        return AggregatedSegment(
            start=group['start'],
            end=group['end'],
            text=group['text'],
            content_type=group['type'],
            segment_count=group['count'],
        )
