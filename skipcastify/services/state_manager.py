import logging
from pathlib import Path
from typing import List


logger = logging.getLogger(__name__)

class StateManager:
    def __init__(self, data_dir: str) -> None:
        self.data_dir = Path(data_dir)
        self.raw_dir = self.data_dir / "podcasts" / "raw"
        self.processed_dir = self.data_dir / "podcasts" / "processed"

        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.processed_dir.mkdir(parents=True, exist_ok=True)

    def get_unprocessed_episodes(self) -> List[str]:
        """Returns list of episodes that need processing"""
        episodes = []
        for podcast_dir in self.raw_dir.iterdir():
            if podcast_dir.is_dir():
                for episode in podcast_dir.glob("*.mp3"):
                    if not (self.processed_dir / episode.relative_to(self.raw_dir)).exists():
                        episodes.append(episode)
        return episodes
    
    def get_processed_episodes(self) -> List[str]:
        """Returns list of episodes that have been processed"""
        episodes = []
        for podcast_dir in self.processed_dir.iterdir():
            if podcast_dir.is_dir():
                for episode in podcast_dir.glob("*.mp3"):
                    episodes.append(episode)
        return episodes