
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, List, Optional
import uuid
from skipcastify.models.content import AudioSegment, ContentType


@dataclass
class Episode:
    """Represents a podcast episode."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    title: str = ""
    description: str = ""
    published_date: Optional[datetime] = None
    duration: Optional[float] = None  # in seconds
    audio_url: Optional[str] = None
    image_url: Optional[str] = None
    show_notes: str = ""
    guid: Optional[str] = None  # RSS guid
    episode_number: Optional[int] = None
    season_number: Optional[int] = None
    
    # For tracking processing state
    local_audio_path: Optional[str] = None
    processed_audio_path: Optional[str] = None
    is_downloaded: bool = False
    is_processed: bool = False
    
    # Content segments
    audio_segments: List[AudioSegment] = field(default_factory=list)
    
    # Episode metadata
    download_count: int = 0
    likes: int = 0
    
    def download(self, directory: str) -> bool:
        """
        Download episode to specified directory.
        
        Args:
            directory: Path to download the episode to.
            
        Returns:
            bool: True if successful, False otherwise.
        """
        # Implementation would go here
        # Set is_downloaded to True if successful
        pass
    
    def process(self, ad_detection_func: Callable) -> bool:
        """
        Process the episode to detect and mark advertisements.
        
        Args:
            ad_detection_func: Function that performs ad detection
            
        Returns:
            bool: True if successful, False otherwise.
        """
        # Implementation would go here
        # Populates audio_segments
        # Sets is_processed to True if successful
        pass
    
    def create_ad_free_version(self, output_path: str) -> bool:
        """
        Create an ad-free version of the episode.
        
        Args:
            output_path: Path to save the ad-free version.
            
        Returns:
            bool: True if successful, False otherwise.
        """
        # Implementation would go here
        # Uses audio_segments to skip ad segments
        # Sets processed_audio_path if successful
        pass
    
    @property
    def ad_duration(self) -> float:
        """Return the total duration of advertisements in this episode."""
        return sum(segment.duration for segment in self.audio_segments 
                   if segment.segment_type in (ContentType.ADVERTISEMENT, ContentType.SPONSOR))
    
    @property
    def content_duration(self) -> float:
        """Return the duration of actual content (excluding ads)."""
        if self.duration is None:
            return 0
        return self.duration - self.ad_duration