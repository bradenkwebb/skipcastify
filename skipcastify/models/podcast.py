from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Dict, Optional, Set, Callable
import uuid

from skipcastify.models.episode import Episode
from skipcastify.models.feed import RSSFeed
from skipcastify.models.people import Person

@dataclass
class Podcast:
    """Represents a podcast."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    title: str = ""
    description: str = ""
    author: str = ""
    website: Optional[str] = None
    language: str = "en"
    explicit: bool = False
    image_url: Optional[str] = None
    categories: List[str] = field(default_factory=list)
    
    # RSS feed information
    rss_feed: Optional[RSSFeed] = None
    
    # People involved
    people: List[Person] = field(default_factory=list)
    
    # Episodes
    episodes: Dict[str, Episode] = field(default_factory=dict)  # id -> Episode
    
    # For tracking user preferences
    is_subscribed: bool = False
    is_favorite: bool = False
    auto_download: bool = False
    
    def add_episode(self, episode: Episode) -> None:
        """Add an episode to this podcast."""
        self.episodes[episode.id] = episode
    
    def update_from_feed(self) -> int:
        """
        Update podcast info and episodes from RSS feed.
        
        Returns:
            int: Number of new episodes found.
        """
        if self.rss_feed is None:
            return 0
        # Implementation would go here
        # Return count of new episodes
        pass
    
    def get_latest_episode(self) -> Optional[Episode]:
        """Return the most recently published episode."""
        if not self.episodes:
            return None
        return max(self.episodes.values(), 
                   key=lambda e: e.published_date if e.published_date else datetime.min)
    
    def get_unprocessed_episodes(self) -> List[Episode]:
        """Return all episodes that haven't been processed yet."""
        return [episode for episode in self.episodes.values() 
                if episode.is_downloaded and not episode.is_processed]