"""
Transcript caching and management.

Allows reusing transcripts across multiple preview runs without re-transcribing,
significantly speeding up iteration during development.
"""

import json
import logging
import os
from pathlib import Path
from typing import List, Optional
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)


@dataclass
class CachedSegment:
    """A cached transcript segment."""
    start: int      # milliseconds
    end: int        # milliseconds
    text: str


class TranscriptCache:
    """Manages caching of transcriptions."""
    
    def __init__(self, cache_dir: str = "data/transcripts"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
    
    def get_cache_path(self, episode_name: str) -> Path:
        """Get the cache file path for an episode."""
        return self.cache_dir / f"{episode_name}_transcript.json"
    
    def load_cached_transcript(self, episode_name: str) -> Optional[List[CachedSegment]]:
        """Load a cached transcript if it exists.
        
        Returns:
            List of CachedSegment objects, or None if not cached
        """
        cache_path = self.get_cache_path(episode_name)
        if not cache_path.exists():
            return None
        
        try:
            with open(cache_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            segments = [CachedSegment(**item) for item in data]
            logger.info(f"Loaded cached transcript for {episode_name}: {len(segments)} segments")
            return segments
        except Exception as e:
            logger.warning(f"Failed to load cached transcript: {e}")
            return None
    
    def save_transcript(self, episode_name: str, segments: List) -> None:
        """Save a transcript to cache.
        
        Args:
            episode_name: Name of the episode (for cache file naming)
            segments: List of Segment objects with start, end, text attributes
        """
        cache_path = self.get_cache_path(episode_name)
        try:
            # Convert segments to dicts
            data = []
            for seg in segments:
                data.append({
                    'start': seg.start,
                    'end': seg.end,
                    'text': seg.text
                })
            
            with open(cache_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            
            logger.info(f"Saved cached transcript for {episode_name}: {len(segments)} segments")
        except Exception as e:
            logger.warning(f"Failed to save cached transcript: {e}")
    
    def list_cached(self) -> List[str]:
        """List all cached episode names."""
        cached = []
        for f in self.cache_dir.glob("*_transcript.json"):
            episode_name = f.name.replace("_transcript.json", "")
            cached.append(episode_name)
        return sorted(cached)
