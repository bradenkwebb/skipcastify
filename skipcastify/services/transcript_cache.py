"""
Transcript caching and management.

Allows reusing transcripts across multiple preview runs without re-transcribing,
significantly speeding up iteration during development.
"""

import hashlib
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

    def _source_hash_path(self, episode_name: str) -> Path:
        """Sidecar file storing the hash of the audio a transcript was built from."""
        return self.cache_dir / f"{episode_name}_transcript.sha256"

    @staticmethod
    def _hash_file(path) -> Optional[str]:
        """SHA-256 of a file's bytes, or None if it can't be read."""
        try:
            h = hashlib.sha256()
            with open(path, 'rb') as f:
                for chunk in iter(lambda: f.read(1 << 20), b''):
                    h.update(chunk)
            return h.hexdigest()
        except Exception as e:
            logger.warning(f"Failed to hash {path}: {e}")
            return None

    def _load_source_hash(self, episode_name: str) -> Optional[str]:
        path = self._source_hash_path(episode_name)
        if not path.exists():
            return None
        try:
            return path.read_text().strip()
        except Exception:
            return None

    def _save_source_hash(self, episode_name: str, source_path) -> None:
        digest = self._hash_file(source_path)
        if digest is not None:
            self._source_hash_path(episode_name).write_text(digest)
    
    def load_cached_transcript(self, episode_name: str, source_path=None) -> Optional[List[CachedSegment]]:
        """Load a cached transcript if it exists.

        Args:
            episode_name: Name of the episode (cache key).
            source_path: If given, only return the cache when it was built from
                this exact audio. Guards against dynamically-inserted ads: a
                re-download under the same name carries different ad content, so
                a transcript keyed only by name would be stale.

        Returns:
            List of CachedSegment objects, or None if not cached / stale.
        """
        cache_path = self.get_cache_path(episode_name)
        if not cache_path.exists():
            return None

        if source_path is not None:
            stored = self._load_source_hash(episode_name)
            current = self._hash_file(source_path)
            if stored is None or current is None or stored != current:
                logger.info(
                    f"Cached transcript for {episode_name} does not match current audio; ignoring cache"
                )
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
    
    def save_transcript(self, episode_name: str, segments: List, source_path=None) -> None:
        """Save a transcript to cache.

        Args:
            episode_name: Name of the episode (for cache file naming)
            segments: List of Segment objects with start, end, text attributes
            source_path: If given, also record a hash of this audio file so the
                cache can be invalidated when the audio changes (see
                load_cached_transcript).
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

            if source_path is not None:
                self._save_source_hash(episode_name, source_path)
            
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
