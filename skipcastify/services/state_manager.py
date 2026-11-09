import json
import logging
from pathlib import Path
from typing import List

from skipcastify.models.content import AudioSegment


logger = logging.getLogger(__name__)

class StateManager:
    def __init__(self, data_dir: str) -> None:
        self.data_dir = Path(data_dir)
        self.raw_dir = self.data_dir / "podcasts" / "raw"
        self.processed_dir = self.data_dir / "podcasts" / "processed"
        self.baseline_path = self.data_dir / "process_baseline.json"
        self.attempts_path = self.data_dir / "process_attempts.json"

        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.processed_dir.mkdir(parents=True, exist_ok=True)

    def _load_baseline(self) -> set[str]:
        """Episodes (as 'slug/filename') that predate processing and must be skipped.

        Lets us enable processing on all feeds while ignoring the historical
        backlog already on disk — only episodes downloaded afterward get processed.
        """
        if self.baseline_path.exists():
            try:
                return set(json.loads(self.baseline_path.read_text()))
            except Exception as e:
                logger.warning(f"Could not read process baseline: {e}")
        return set()

    def get_unprocessed_episodes(self) -> List[str]:
        """Returns episodes that need processing, excluding the backlog baseline."""
        baseline = self._load_baseline()
        episodes = []
        for podcast_dir in self.raw_dir.iterdir():
            if podcast_dir.is_dir():
                for episode in podcast_dir.glob("*.mp3"):
                    rel = episode.relative_to(self.raw_dir)
                    if str(rel) in baseline:
                        continue
                    if not (self.processed_dir / rel).exists():
                        episodes.append(episode)
        return episodes
    
    def _load_attempts(self) -> dict[str, int]:
        try:
            return json.loads(self.attempts_path.read_text())
        except FileNotFoundError:
            return {}
        except Exception as e:
            logger.warning(f"Could not read process attempts: {e}")
            return {}

    def attempts(self, episode) -> int:
        """Runs that started this episode and never finished it (crash or error)."""
        return self._load_attempts().get(self._key(episode), 0)

    def start_attempt(self, episode) -> int:
        """Counted before work starts, so a SIGKILL (OOM) still counts."""
        attempts = self._load_attempts()
        key = self._key(episode)
        attempts[key] = attempts.get(key, 0) + 1
        self.attempts_path.write_text(json.dumps(attempts, indent=1))
        return attempts[key]

    def finish_attempt(self, episode) -> None:
        attempts = self._load_attempts()
        if attempts.pop(self._key(episode), None) is not None:
            self.attempts_path.write_text(json.dumps(attempts, indent=1))

    def _key(self, episode) -> str:
        return str(Path(episode).relative_to(self.raw_dir))

    def get_processed_episodes(self) -> List[str]:
        """Returns list of episodes that have been processed"""
        episodes = []
        for podcast_dir in self.processed_dir.iterdir():
            if podcast_dir.is_dir():
                for episode in podcast_dir.glob("*.mp3"):
                    episodes.append(episode)
        return episodes

    def save_processed_audio(self, audio: AudioSegment, raw_episode: str) -> None:
        processed_episode = self.processed_dir / Path(raw_episode).name
        audio.export(processed_episode, format="mp3")
        logger.info(f"Saved processed audio to {processed_episode}")
        return