"""
Segment classification service for identifying ad segments in podcast transcriptions.

Uses keyword-based heuristics with optional Ollama LLM fallback for ambiguous cases.
"""

import logging
import re
from dataclasses import dataclass
from typing import List, Optional
from enum import Enum
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
    
    def __init__(self, use_ollama: bool = False, ollama_model: str = "neural-chat"):
        """
        Initialize the classifier.
        
        Args:
            use_ollama: Whether to use Ollama for LLM-based classification
            ollama_model: Ollama model to use (default: neural-chat)
        """
        self.use_ollama = use_ollama
        self.ollama_model = ollama_model
        
        if self.use_ollama:
            self._test_ollama_connection()
    
    def _test_ollama_connection(self) -> bool:
        """Test if Ollama is available and responding."""
        try:
            import requests
            response = requests.get("http://localhost:11434/api/tags", timeout=2)
            if response.status_code == 200:
                logger.info("Ollama connection successful")
                return True
        except Exception as e:
            logger.warning(f"Ollama not available: {e}. Falling back to keyword-based classification.")
            self.use_ollama = False
        return False
    
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
        
        Returns:
            Tuple of (ContentType, confidence_score)
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
        
        # If Ollama is available and we're uncertain, use LLM for borderline cases
        if self.use_ollama and len(text) > 50:  # Only for non-trivial segments
            try:
                return self._classify_with_ollama(text)
            except Exception as e:
                logger.debug(f"Ollama classification failed, falling back to CONTENT: {e}")
        
        # Default to content
        return ContentType.CONTENT, 0.8
    
    def _count_pattern_matches(self, text: str, patterns: set) -> int:
        """Count how many patterns match in the given text."""
        count = 0
        for pattern in patterns:
            if re.search(pattern, text, re.IGNORECASE):
                count += 1
        return count
    
    def _classify_with_ollama(self, text: str) -> tuple[ContentType, float]:
        """
        Use Ollama LLM to classify ambiguous segments.
        
        Returns:
            Tuple of (ContentType, confidence_score)
        """
        try:
            import requests
            
            prompt = f"""Classify the following podcast transcript segment as one of: INTRO, CONTENT, ADVERTISEMENT, SPONSOR, OUTRO.
            
Segment: "{text}"

Respond with ONLY the classification (one word) and a confidence score (0.0-1.0) separated by a colon.
Example: CONTENT:0.95

Classification:"""
            
            response = requests.post(
                "http://localhost:11434/api/generate",
                json={
                    "model": self.ollama_model,
                    "prompt": prompt,
                    "stream": False,
                    "temperature": 0.3,  # Lower temperature for more deterministic classification
                },
                timeout=10
            )
            
            if response.status_code == 200:
                result = response.json()["response"].strip().upper()
                
                # Parse response (e.g., "CONTENT:0.95")
                if ":" in result:
                    parts = result.split(":")
                    classification = parts[0].strip()
                    try:
                        confidence = float(parts[1].strip())
                    except (ValueError, IndexError):
                        confidence = 0.7
                else:
                    # Try to match just the word
                    classification = result.split()[0] if result else "CONTENT"
                    confidence = 0.7
                
                # Map string to ContentType
                content_type_map = {
                    "INTRO": ContentType.INTRO,
                    "CONTENT": ContentType.CONTENT,
                    "ADVERTISEMENT": ContentType.ADVERTISEMENT,
                    "SPONSOR": ContentType.SPONSOR,
                    "OUTRO": ContentType.OUTRO,
                }
                
                content_type = content_type_map.get(classification, ContentType.CONTENT)
                logger.debug(f"Ollama classified: {classification} (confidence: {confidence:.2f})")
                return content_type, confidence
        
        except Exception as e:
            logger.warning(f"Ollama classification error: {e}")
        
        return ContentType.CONTENT, 0.5
    
    def aggregate_segments(
        self, 
        classified_segments: List[ClassifiedSegment],
        gap_threshold_ms: int = 1500,
        merge_window_ms: int = 2000
    ) -> List[AggregatedSegment]:
        """
        Aggregate consecutive segments of the same type to reduce fragmentation.
        
        Uses a time-window based approach:
        1. Merge consecutive segments of same type if gap is <= gap_threshold_ms
        2. Merge very close segments (within merge_window_ms) to avoid short silences
        
        Args:
            classified_segments: List of ClassifiedSegment objects
            gap_threshold_ms: Max gap between segments to merge (default: 1.5s)
            merge_window_ms: If two adjacent different-type segments are < this distance, 
                           keep smaller one for continuity (default: 2.0s)
            
        Returns:
            List of AggregatedSegment objects
        """
        if not classified_segments:
            return []
        
        aggregated = []
        current_group = None
        
        for i, seg in enumerate(classified_segments):
            if current_group is None:
                # Start new group
                current_group = {
                    'start': seg.start,
                    'end': seg.end,
                    'text': [seg.text],
                    'type': seg.content_type,
                    'count': 1,
                }
            else:
                # Check if we should continue current group or start new one
                gap = seg.start - current_group['end']
                
                if seg.content_type == current_group['type']:
                    # Same type: merge if gap is small enough
                    if gap <= gap_threshold_ms:
                        current_group['end'] = seg.end
                        current_group['text'].append(seg.text)
                        current_group['count'] += 1
                    else:
                        # Gap too large, finalize group and start new one
                        aggregated.append(self._create_aggregated_segment(current_group))
                        current_group = {
                            'start': seg.start,
                            'end': seg.end,
                            'text': [seg.text],
                            'type': seg.content_type,
                            'count': 1,
                        }
                else:
                    # Different type
                    # Check if we should keep this small segment or skip it for continuity
                    segment_duration = seg.end - seg.start
                    
                    # If this segment is very short and different from group, consider skipping
                    # For now, always finalize current group and start new one
                    # (User requested to keep intros/outros, so preserve them)
                    aggregated.append(self._create_aggregated_segment(current_group))
                    current_group = {
                        'start': seg.start,
                        'end': seg.end,
                        'text': [seg.text],
                        'type': seg.content_type,
                        'count': 1,
                    }
        
        # Don't forget the last group
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
