from pydub import AudioSegment
import sys
from skipcastify.services.rss_parser import RSSParser
import logging
import os
import subprocess
import datetime
import json
import whisper
import time
from pathlib import Path
from dataclasses import dataclass
from typing import List

from skipcastify.services.state_manager import StateManager
from skipcastify.services.segment_classifier import SegmentClassifier
from skipcastify.models.content import ContentType
from skipcastify.services import llm_utils
from skipcastify.services import section_processor
from skipcastify.services.segment_classifier import ClassifiedSegment
from skipcastify.services.llm_monitor import get_monitor

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
        """
        if not os.path.isfile(audio_path):
            logger.error(f"Audio file not found for transcription: {audio_path}")
            raise FileNotFoundError(f"Audio file not found: {audio_path}")
        
        logger.info(f"Starting Whisper transcription: {audio_path}")
        logger.info(f"Model size: {model_size}")
        
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
            audio = self.load_and_validate_audio_file(episode_file_path)
            
            working_dir = self._get_episode_working_dir(episode_file_path)
            transcripts_dir = self._get_episode_transcripts_dir(episode_file_path)
            
            wav_path = os.path.join(working_dir, "audio_for_transcription.wav")
            self._export_audio_to_wav(audio, wav_path)
            
            logger.info("Starting transcription with Whisper...")
            segments = self.transcribe_with_whisper(wav_path, model_size="base")
            logger.info(f"Transcribed {len(segments)} segments")
            episode_name = os.path.splitext(os.path.basename(episode_file_path))[0]
            
            # Run section-level LLM refinement (10-minute windows) to detect ad spans
            logger.info("Running section-level LLM refinement (if available)...")
            try:
                llm_ad_spans = self._run_section_llm(segments, transcripts_dir, episode_name=episode_name)
            except Exception as e:
                logger.warning(f"Section LLM refinement failed: {e}. Falling back to heuristic classification.")
                llm_ad_spans = []

            # Mark segments based on LLM results or heuristic fallback
            classifier = SegmentClassifier(use_ollama=False)
            if llm_ad_spans:
                logger.info(f"LLM identified {len(llm_ad_spans)} ad spans. Marking overlapping segments...")
                classified_segments = []
                for seg in segments:
                    # Check overlap with any LLM ad span
                    is_ad = False
                    ad_label = None
                    for span in llm_ad_spans:
                        span_start = int(span.get("start_ms", 0))
                        span_end = int(span.get("end_ms", 0))
                        if seg.start < span_end and seg.end > span_start:
                            is_ad = True
                            ad_label = span.get("label")
                            break
                    if is_ad:
                        ct = ContentType.ADVERTISEMENT if ad_label in ("ADVERTISEMENT", "OTHER_AD") else ContentType.SPONSOR
                        classified_segments.append(ClassifiedSegment(start=seg.start, end=seg.end, text=seg.text, content_type=ct, confidence=0.9))
                    else:
                        classified_segments.append(ClassifiedSegment(start=seg.start, end=seg.end, text=seg.text, content_type=ContentType.CONTENT, confidence=0.9))
            else:
                logger.info("Using heuristic classifier as fallback...")
                classified_segments = classifier.classify_segments(segments)

            logger.info("Aggregating segments...")
            aggregated_segments = classifier.aggregate_segments(classified_segments)
            
            segment_counts = {}
            for agg_seg in aggregated_segments:
                content_type = agg_seg.content_type.value
                segment_counts[content_type] = segment_counts.get(content_type, 0) + 1
            
            logger.info(f"Segment breakdown: {segment_counts}")
            
            logger.info("Cutting and stitching audio...")
            processed_audio = self.cut_and_stitch_audio(audio, aggregated_segments)
            
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

    def _save_preview_transcript(self, transcript_path: str, aggregated_segments: List, classified_segments: List) -> None:
        """Save a side-by-side transcript showing original text and what was removed."""
        try:
            with open(transcript_path, 'w', encoding='utf-8') as f:
                f.write("Preview Transcript: Original vs. Removed Content\n")
                f.write("=" * 100 + "\n\n")
                
                # Write full transcript with markers for removed content
                f.write("FULL ORIGINAL TRANSCRIPT:\n")
                f.write("-" * 100 + "\n")
                for seg in classified_segments:
                    time_display = f"[{seg.start}ms-{seg.end}ms]"
                    content_type_display = seg.content_type.value
                    if seg.content_type != ContentType.CONTENT:
                        f.write(f"❌ REMOVED ({content_type_display}) {time_display}: {seg.text}\n")
                    else:
                        f.write(f"✓  KEPT                {time_display}: {seg.text}\n")
                
                f.write("\n" + "=" * 100 + "\n\n")
                f.write("REMOVED CONTENT SUMMARY:\n")
                f.write("-" * 100 + "\n")
                
                removed_count = 0
                total_removed_ms = 0
                for agg_seg in aggregated_segments:
                    if agg_seg.content_type != ContentType.CONTENT:
                        duration_s = (agg_seg.end - agg_seg.start) / 1000
                        label = agg_seg.content_type.value
                        combined_text = " ".join(agg_seg.text)
                        f.write(f"\n[{agg_seg.start}ms-{agg_seg.end}ms] ({duration_s:.1f}s) - {label}\n")
                        f.write(f"  Text: {combined_text}\n")
                        removed_count += 1
                        total_removed_ms += (agg_seg.end - agg_seg.start)
                
                f.write(f"\n\nTOTAL: {removed_count} ad/promo segments removed ({total_removed_ms/1000:.1f}s)\n")
                
                logger.debug(f"Saved preview transcript to: {transcript_path}")
        except Exception as e:
            logger.warning(f"Failed to save preview transcript: {e}")


    def _run_section_llm(self, segments: List[Segment], transcripts_dir: str, episode_name: str) -> List[dict]:
        """Run the section-level LLM on suspicious sections and return ad spans.

        Returns a list of dicts with keys: start_ms, end_ms, label, rationale
        """
        prompt_path = os.path.join(os.path.dirname(__file__), '..', 'llm_prompts', 'identify_ads_prompt.txt')
        # Normalize path
        prompt_path = os.path.normpath(prompt_path)

        try:
            with open(prompt_path, 'r', encoding='utf-8') as f:
                prompt_template = f.read()
        except Exception as e:
            logger.error(f"Failed to read prompt template: {e}")
            raise

        sections = section_processor.make_sections_from_segments(segments)
        all_spans = []
        
        # Start monitoring for this episode
        monitor = get_monitor()
        monitor.start_episode(episode_name)

        for section_idx, sec in enumerate(sections):
            if not section_processor.is_section_suspicious(sec):
                continue

            section_text = section_processor.section_to_prompt_text(sec)
            full_prompt = prompt_template + "\n\nTRANSCRIPT:\n" + section_text

            # Time the LLM call
            call_start = time.time()
            parsed = []
            success = False
            error_msg = None
            raw = None
            
            try:
                raw = llm_utils.call_ollama_generate(full_prompt)
                call_duration = time.time() - call_start
                
                # Save raw response for debugging
                try:
                    debug_response_path = os.path.join(transcripts_dir, f"{episode_name}_section_{section_idx}_response.txt")
                    with open(debug_response_path, 'w', encoding='utf-8') as f:
                        f.write(f"Raw LLM Response for section {section_idx} [{sec.start_ms}ms-{sec.end_ms}ms]:\n")
                        f.write("="*100 + "\n")
                        f.write(raw)
                        f.write("\n" + "="*100 + "\n")
                    logger.debug(f"Saved raw LLM response to: {debug_response_path}")
                except Exception as debug_e:
                    logger.debug(f"Failed to save debug response: {debug_e}")
                
                try:
                    parsed = llm_utils.parse_json_array_from_text(raw)
                    success = True
                except Exception as e:
                    error_msg = str(e)
                    logger.warning(f"Failed to parse LLM response for section {sec.start_ms}-{sec.end_ms}: {e}")
                    logger.debug(f"Raw response was: {raw[:500]}...")
            except Exception as e:
                call_duration = time.time() - call_start
                error_msg = str(e)
                logger.warning(f"Ollama call failed for section {sec.start_ms}-{sec.end_ms}: {e}")
            
            # Record metrics
            monitor.record_call(
                section_index=section_idx,
                section_start_ms=sec.start_ms,
                section_end_ms=sec.end_ms,
                duration_sec=call_duration,
                success=success,
                spans_returned=len(parsed) if success else 0,
                error_msg=error_msg
            )

            # Validate and normalize spans
            for item in parsed:
                try:
                    s = int(item.get('start_ms', 0))
                    e = int(item.get('end_ms', 0))
                    label = item.get('label', 'ADVERTISEMENT')
                    rationale = item.get('rationale', '')
                    # Ensure spans are within section bounds broadly
                    if s < sec.start_ms:
                        s = sec.start_ms
                    if e > sec.end_ms:
                        e = sec.end_ms
                    if s >= e:
                        logger.debug(f"Ignoring invalid span {s}-{e} in section {sec.start_ms}-{sec.end_ms}")
                        continue
                    all_spans.append({"start_ms": s, "end_ms": e, "label": label, "rationale": rationale})
                except Exception as ex:
                    logger.debug(f"Skipping malformed LLM item: {item} ({ex})")
                    continue

        logger.info(f"LLM identified {len(all_spans)} ad spans across sections")
        
        # Save and print monitoring summary
        monitor.save_episode_profile()
        monitor.print_episode_summary()
        
        return all_spans

    def _aggregated_segments_to_ad_spans(self, aggregated_segments: List) -> List[dict]:
        """Convert aggregated segments classified as ads into ad span dicts.
        
        Returns list of dicts with keys: start_ms, end_ms, label, rationale
        """
        ad_spans = []
        for agg_seg in aggregated_segments:
            if agg_seg.content_type != ContentType.CONTENT:
                label = "ADVERTISEMENT" if agg_seg.content_type == ContentType.ADVERTISEMENT else "SPONSOR"
                ad_spans.append({
                    "start_ms": agg_seg.start,
                    "end_ms": agg_seg.end,
                    "label": label,
                    "rationale": "(heuristic classifier)"
                })
        return ad_spans

    def process_preview(self, episode_file_path: str, preview_output_path: str) -> dict:
        """Process a single episode and write a preview MP3 with suggested removals.

        This does not update state_manager or overwrite original processed outputs.
        
        Returns dict with keys:
            - audio_path: path to preview MP3
            - transcript_path: path to side-by-side transcript showing what was removed
        """
        logger.info(f"Preview processing episode: {episode_file_path}")

        audio = self.load_and_validate_audio_file(episode_file_path)
        working_dir = self._get_episode_working_dir(episode_file_path)
        transcripts_dir = self._get_episode_transcripts_dir(episode_file_path)

        wav_path = os.path.join(working_dir, "audio_for_transcription.wav")
        self._export_audio_to_wav(audio, wav_path)

        logger.info("Transcribing for preview...")
        segments = self.transcribe_with_whisper(wav_path, model_size="base")

        # LLM refinement
        episode_name = os.path.splitext(os.path.basename(episode_file_path))[0]
        try:
            llm_ad_spans = self._run_section_llm(segments, transcripts_dir, episode_name=episode_name)
        except Exception as e:
            logger.warning(f"Preview LLM refinement failed: {e}")
            llm_ad_spans = []

        if llm_ad_spans:
            logger.info(f"LLM identified {len(llm_ad_spans)} ad spans for preview.")
            classified_segments = []
            for seg in segments:
                is_ad = False
                ad_label = None
                for span in llm_ad_spans:
                    span_start = int(span.get('start_ms', 0))
                    span_end = int(span.get('end_ms', 0))
                    if seg.start < span_end and seg.end > span_start:
                        is_ad = True
                        ad_label = span.get('label')
                        break
                if is_ad:
                    ct = ContentType.ADVERTISEMENT if ad_label in ("ADVERTISEMENT", "OTHER_AD") else ContentType.SPONSOR
                    classified_segments.append(ClassifiedSegment(start=seg.start, end=seg.end, text=seg.text, content_type=ct, confidence=0.9))
                else:
                    classified_segments.append(ClassifiedSegment(start=seg.start, end=seg.end, text=seg.text, content_type=ContentType.CONTENT, confidence=0.9))
        else:
            # Fallback: use heuristic classifier
            logger.info("Using heuristic classifier as fallback for preview...")
            classifier = SegmentClassifier(use_ollama=False)
            classified_segments = classifier.classify_segments(segments)

        classifier = SegmentClassifier(use_ollama=False)
        aggregated_segments = classifier.aggregate_segments(classified_segments)
        preview_audio = self.cut_and_stitch_audio(audio, aggregated_segments)

        os.makedirs(os.path.dirname(preview_output_path), exist_ok=True)
        preview_audio.export(preview_output_path, format="mp3", bitrate="192k")
        logger.info(f"Saved preview audio to: {preview_output_path}")

        # Save side-by-side transcript showing original vs. removed
        transcript_path = preview_output_path.replace('.mp3', '_transcript.txt')
        self._save_preview_transcript(transcript_path, aggregated_segments, classified_segments)
        logger.info(f"Saved preview transcript to: {transcript_path}")

        # Save predictions to JSON for evaluation
        # Use episode name directly (not preview_output_path) to get consistent naming
        preview_dir = os.path.dirname(preview_output_path)
        predictions_path = os.path.join(preview_dir, f"{episode_name}_predictions.json")
        
        # If LLM returned nothing, use heuristic results as predictions
        predictions_to_save = llm_ad_spans if llm_ad_spans else self._aggregated_segments_to_ad_spans(aggregated_segments)
        
        predictions_data = {
            'predictions': predictions_to_save
        }
        try:
            with open(predictions_path, 'w', encoding='utf-8') as f:
                json.dump(predictions_data, f, indent=2, ensure_ascii=False)
            logger.info(f"Saved predictions to: {predictions_path}")
        except Exception as e:
            logger.warning(f"Failed to save predictions: {e}")
            predictions_path = None

        return {
            "audio_path": preview_output_path,
            "transcript_path": transcript_path,
            "predictions_path": predictions_path,
            "segments": segments,
            "ad_spans": predictions_to_save
        }

    def process_preview_with_segments(self, episode_file_path: str, preview_output_path: str, segments: List[Segment]) -> dict:
        """Process a preview using pre-loaded (cached) segments.
        
        Skips transcription entirely, useful for fast iteration during development.
        
        Args:
            episode_file_path: Path to the original episode MP3
            preview_output_path: Path where preview MP3 will be saved
            segments: Pre-transcribed segments (from cache)
        
        Returns:
            Same dict as process_preview()
        """
        logger.info(f"Preview processing episode (using cached segments): {episode_file_path}")

        audio = self.load_and_validate_audio_file(episode_file_path)
        episode_name = os.path.splitext(os.path.basename(episode_file_path))[0]
        working_dir = self._get_episode_working_dir(episode_file_path)
        transcripts_dir = self._get_episode_transcripts_dir(episode_file_path)

        logger.info(f"Using {len(segments)} cached segments (skipping transcription)")

        # LLM refinement
        try:
            llm_ad_spans = self._run_section_llm(segments, transcripts_dir, episode_name=episode_name)
        except Exception as e:
            logger.warning(f"Preview LLM refinement failed: {e}")
            llm_ad_spans = []

        if llm_ad_spans:
            logger.info(f"LLM identified {len(llm_ad_spans)} ad spans for preview.")
            classified_segments = []
            for seg in segments:
                is_ad = False
                ad_label = None
                for span in llm_ad_spans:
                    span_start = int(span.get('start_ms', 0))
                    span_end = int(span.get('end_ms', 0))
                    if seg.start < span_end and seg.end > span_start:
                        is_ad = True
                        ad_label = span.get('label')
                        break
                if is_ad:
                    ct = ContentType.ADVERTISEMENT if ad_label in ("ADVERTISEMENT", "OTHER_AD") else ContentType.SPONSOR
                    classified_segments.append(ClassifiedSegment(start=seg.start, end=seg.end, text=seg.text, content_type=ct, confidence=0.9))
                else:
                    classified_segments.append(ClassifiedSegment(start=seg.start, end=seg.end, text=seg.text, content_type=ContentType.CONTENT, confidence=0.9))
        else:
            # Fallback: use heuristic classifier
            logger.info("Using heuristic classifier as fallback for preview...")
            classifier = SegmentClassifier(use_ollama=False)
            classified_segments = classifier.classify_segments(segments)

        classifier = SegmentClassifier(use_ollama=False)
        aggregated_segments = classifier.aggregate_segments(classified_segments)
        preview_audio = self.cut_and_stitch_audio(audio, aggregated_segments)

        os.makedirs(os.path.dirname(preview_output_path), exist_ok=True)
        preview_audio.export(preview_output_path, format="mp3", bitrate="192k")
        logger.info(f"Saved preview audio to: {preview_output_path}")

        # Save side-by-side transcript showing original vs. removed
        transcript_path = preview_output_path.replace('.mp3', '_transcript.txt')
        self._save_preview_transcript(transcript_path, aggregated_segments, classified_segments)
        logger.info(f"Saved preview transcript to: {transcript_path}")

        # Save predictions to JSON for evaluation
        # Use episode name directly (not preview_output_path) to get consistent naming
        preview_dir = os.path.dirname(preview_output_path)
        predictions_path = os.path.join(preview_dir, f"{episode_name}_predictions.json")
        
        # If LLM returned nothing, use heuristic results as predictions
        predictions_to_save = llm_ad_spans if llm_ad_spans else self._aggregated_segments_to_ad_spans(aggregated_segments)
        
        predictions_data = {
            'predictions': predictions_to_save
        }
        try:
            with open(predictions_path, 'w', encoding='utf-8') as f:
                json.dump(predictions_data, f, indent=2, ensure_ascii=False)
            logger.info(f"Saved predictions to: {predictions_path}")
        except Exception as e:
            logger.warning(f"Failed to save predictions: {e}")
            predictions_path = None

        return {
            "audio_path": preview_output_path,
            "transcript_path": transcript_path,
            "predictions_path": predictions_path,
            "segments": segments,
            "ad_spans": predictions_to_save
        }

