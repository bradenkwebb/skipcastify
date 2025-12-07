from pydub import AudioSegment
import sys
from skipcastify.services.rss_parser import RSSParser
import logging
import os
import subprocess
import datetime
import whisper
from pathlib import Path
from dataclasses import dataclass
from typing import List

from skipcastify.services.state_manager import StateManager

logger = logging.getLogger(__name__)


@dataclass
class Segment:
    """Represents a transcript segment with timing and text."""
    start: int  # milliseconds
    end: int    # milliseconds
    text: str

class AudioProcessor:
    def __init__(self, data_dir: str) -> None:
        self.data_dir = data_dir

    def load_and_validate_audio_file(self, file_path: str) -> AudioSegment:
        """
        Validates that the file exists and is a readable audio file.
        Loads and returns the audio object, or raises an exception if invalid.
        """
        if not os.path.isfile(file_path):
            logger.error(f"File does not exist: {file_path}")
            raise FileNotFoundError(f"Audio file not found: {file_path}")
        try:
            audio = AudioSegment.from_file(file_path)
            logger.info(f"Validated audio file: {file_path} (duration: {audio.duration_seconds:.2f}s)")
            return audio
        except Exception as e:
            logger.error(f"Invalid audio file: {file_path} ({e})")
            raise ValueError(f"Invalid audio file: {file_path}") from e

    def _get_episode_working_dir(self, episode_file_path: str) -> str:
        """Get or create the working directory for an episode."""
        episode_dir = os.path.dirname(episode_file_path)
        working_dir = os.path.join(episode_dir, "working")
        os.makedirs(working_dir, exist_ok=True)
        logger.debug(f"Working directory: {working_dir}")
        return working_dir

    def _get_episode_transcripts_dir(self, episode_file_path: str) -> str:
        """Get or create the transcripts directory for an episode."""
        episode_dir = os.path.dirname(episode_file_path)
        transcripts_dir = os.path.join(episode_dir, "transcripts")
        os.makedirs(transcripts_dir, exist_ok=True)
        logger.debug(f"Transcripts directory: {transcripts_dir}")
        return transcripts_dir

    def _export_audio_to_wav(self, audio: AudioSegment, output_path: str) -> None:
        """Export audio to WAV format for Buzz transcription."""
        try:
            audio.export(output_path, format="wav")
            logger.info(f"Exported audio to WAV: {output_path}")
        except Exception as e:
            logger.error(f"Failed to export audio to WAV: {e}")
            raise


    def transcribe_with_whisper(
        self, 
        audio_path: str,
        model_size: str = "base"
    ) -> List[Segment]:
        """
        Transcribe audio using OpenAI Whisper locally.
        
        Args:
            audio_path: Path to the audio file to transcribe
            model_size: Whisper model size - 'tiny', 'base', 'small', 'medium', 'large'
                       (default: 'base' - good balance of speed/accuracy for POC)
            
        Returns:
            List of Segment objects with transcription and timing
            
        Raises:
            FileNotFoundError: If audio file doesn't exist
            Exception: If transcription fails
        """
        if not os.path.isfile(audio_path):
            logger.error(f"Audio file not found for transcription: {audio_path}")
            raise FileNotFoundError(f"Audio file not found: {audio_path}")
        
        logger.info(f"Starting Whisper transcription: {audio_path}")
        logger.info(f"Model size: {model_size}")
        
        try:
            # Load Whisper model (downloads on first use)
            logger.info(f"Loading Whisper model ({model_size})...")
            model = whisper.load_model(model_size)
            
            # Transcribe audio
            logger.info("Transcribing audio...")
            result = model.transcribe(audio_path, verbose=False, language="en")
            
            # Convert Whisper segments to our Segment format
            segments = []
            for seg in result["segments"]:
                segment = Segment(
                    start=int(seg["start"] * 1000),  # Convert seconds to ms
                    end=int(seg["end"] * 1000),      # Convert seconds to ms
                    text=seg["text"].strip()
                )
                segments.append(segment)
            
            logger.info(f"Transcription complete: {len(segments)} segments")
            return segments
            
        except Exception as e:
            logger.error(f"Transcription failed: {e}")
            raise
    
    def process(self, episode_file_path: str, state_manager: StateManager):
        """
        Main entry point: validate, transcribe, and (future) classify/cut/stitch audio.
        """
        logger.info(f"Processing episode: {episode_file_path}")
        
        try:
            # Validate and load audio
            audio = self.load_and_validate_audio_file(episode_file_path)
            
            # Get working and transcript directories
            working_dir = self._get_episode_working_dir(episode_file_path)
            transcripts_dir = self._get_episode_transcripts_dir(episode_file_path)
            
            # Export audio to WAV for Whisper (handles various input formats reliably)
            wav_path = os.path.join(working_dir, "audio_for_transcription.wav")
            self._export_audio_to_wav(audio, wav_path)
            
            # Transcribe with Whisper
            logger.info("Starting transcription with Whisper...")
            segments = self.transcribe_with_whisper(wav_path, model_size="base")
            
            logger.info(f"Successfully transcribed {len(segments)} segments")
            
            # TODO: Classify segments with LLM (ads vs content)
            # TODO: Cut and stitch audio based on classification
            # TODO: Save processed audio to data/podcasts/processed/
            # TODO: Update state_manager with processing status
            
        except (FileNotFoundError, ValueError) as e:
            logger.error(f"Skipping invalid audio file: {episode_file_path} - {e}")
            return
        except Exception as e:
            logger.error(f"Error processing episode: {episode_file_path} - {e}", exc_info=True)
            raise
