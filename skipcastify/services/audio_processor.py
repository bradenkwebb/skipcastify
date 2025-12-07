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
from skipcastify.services.segment_classifier import SegmentClassifier
from skipcastify.models.content import ContentType

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
        """Export audio to WAV format for transcription."""
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
    
    def cut_and_stitch_audio(
        self, 
        audio: AudioSegment, 
        aggregated_segments: List
    ) -> AudioSegment:
        """
        Remove non-CONTENT segments from audio and concatenate remaining pieces.
        
        Args:
            audio: The full AudioSegment to process
            aggregated_segments: List of AggregatedSegment objects with classifications
            
        Returns:
            New AudioSegment containing only CONTENT segments, with transitions preserved
        """
        if not aggregated_segments:
            logger.warning("No aggregated segments provided, returning original audio")
            return audio
        
        # Collect all CONTENT segments
        content_pieces = []
        
        for agg_seg in aggregated_segments:
            if agg_seg.content_type == ContentType.CONTENT:
                try:
                    # Extract audio from this segment (times are in milliseconds)
                    piece = audio[agg_seg.start:agg_seg.end]
                    content_pieces.append(piece)
                    logger.debug(
                        f"Extracted content piece: {agg_seg.start}ms-{agg_seg.end}ms "
                        f"({len(piece)//1000}s, {agg_seg.segment_count} segments)"
                    )
                except Exception as e:
                    logger.warning(
                        f"Failed to extract segment [{agg_seg.start}ms-{agg_seg.end}ms]: {e}"
                    )
        
        if not content_pieces:
            logger.warning("No content segments found, returning original audio")
            return audio
        
        # Concatenate all content pieces
        logger.info(f"Stitching {len(content_pieces)} content pieces together...")
        stitched_audio = content_pieces[0]
        
        for piece in content_pieces[1:]:
            stitched_audio += piece
        
        original_duration = audio.duration_seconds
        processed_duration = stitched_audio.duration_seconds
        ads_duration = original_duration - processed_duration
        ads_percentage = (ads_duration / original_duration * 100) if original_duration > 0 else 0
        
        logger.info(
            f"Audio stitching complete: "
            f"{original_duration:.1f}s → {processed_duration:.1f}s "
            f"({ads_duration:.1f}s ads removed, {ads_percentage:.1f}%)"
        )
        
        return stitched_audio
    
    def process(self, episode_file_path: str, state_manager: StateManager):
        """
        Main entry point: validate, transcribe, classify, and cut/stitch audio.
        
        Pipeline:
        1. Validate and load audio
        2. Export to WAV for transcription
        3. Transcribe with Whisper
        4. Classify segments (ads vs content vs intros/outros)
        5. Aggregate consecutive segments of same type
        6. Cut and stitch audio (remove non-CONTENT segments)
        7. Save processed audio to data/podcasts/processed/
        8. Update state_manager with processing status
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
            logger.info(f"Transcribed {len(segments)} segments")
            
            # Classify segments
            logger.info("Classifying segments...")
            classifier = SegmentClassifier(use_ollama=True)
            classified_segments = classifier.classify_segments(segments)
            
            # Aggregate segments
            logger.info("Aggregating segments...")
            aggregated_segments = classifier.aggregate_segments(classified_segments)
            
            # Log segment breakdown
            segment_counts = {}
            for agg_seg in aggregated_segments:
                content_type = agg_seg.content_type.value
                segment_counts[content_type] = segment_counts.get(content_type, 0) + 1
            
            logger.info(f"Segment breakdown: {segment_counts}")
            
            # Cut and stitch audio
            logger.info("Cutting and stitching audio...")
            processed_audio = self.cut_and_stitch_audio(audio, aggregated_segments)
            
            # Save processed audio
            episode_name = os.path.splitext(os.path.basename(episode_file_path))[0]
            processed_audio_path = os.path.join(
                self.data_dir, 
                "podcasts", 
                "processed", 
                f"{episode_name}_processed.mp3"
            )
            os.makedirs(os.path.dirname(processed_audio_path), exist_ok=True)
            
            logger.info(f"Saving processed audio to: {processed_audio_path}")
            processed_audio.export(processed_audio_path, format="mp3", bitrate="192k")
            logger.info(f"Successfully saved processed audio")
            
            # Save classification results for debugging
            classification_log_path = os.path.join(
                transcripts_dir,
                f"{episode_name}_classification.txt"
            )
            self._save_classification_log(
                classification_log_path, 
                aggregated_segments
            )
            
            # TODO: Update state_manager with processing status
            logger.info(f"Episode processing complete: {episode_file_path}")
            
        except (FileNotFoundError, ValueError) as e:
            logger.error(f"Skipping invalid audio file: {episode_file_path} - {e}")
            return
        except Exception as e:
            logger.error(f"Error processing episode: {episode_file_path} - {e}", exc_info=True)
            raise
    
    def _save_classification_log(self, log_path: str, aggregated_segments: List) -> None:
        """Save segment classification results to a text file for debugging."""
        try:
            with open(log_path, 'w', encoding='utf-8') as f:
                f.write("Aggregated Segment Classification Log\n")
                f.write("=" * 80 + "\n\n")
                
                for i, agg_seg in enumerate(aggregated_segments, 1):
                    duration_s = (agg_seg.end - agg_seg.start) / 1000
                    time_range = f"{agg_seg.start}ms-{agg_seg.end}ms"
                    
                    f.write(f"Segment {i}: [{time_range}] ({duration_s:.1f}s)\n")
                    f.write(f"  Type: {agg_seg.content_type.value}\n")
                    f.write(f"  Merged segments: {agg_seg.segment_count}\n")
                    
                    # Show first 100 chars of combined text
                    combined_text = " ".join(agg_seg.text)[:100]
                    f.write(f"  Text: {combined_text}...\n\n")
                
                logger.debug(f"Saved classification log to: {log_path}")
        except Exception as e:
            logger.warning(f"Failed to save classification log: {e}")
