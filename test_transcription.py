#!/usr/bin/env python3
"""
Test script for AudioProcessor transcription.
Tests with real episodes and outputs transcription details.
"""

import os
import sys
import logging
from pathlib import Path

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Add project to path
sys.path.insert(0, str(Path(__file__).parent))

from skipcastify.services.audio_processor import AudioProcessor
from skipcastify.services.state_manager import StateManager


def find_test_episodes():
    """Find test episodes: prefer short.mp3 in temp, then raw podcasts."""
    episodes = []
    
    # First, check for short.mp3 in temp
    short_mp3 = Path("data/temp/short.mp3")
    if short_mp3.exists():
        episodes.append((str(short_mp3), "short test (20s)"))
    
    # Then find the smallest episode in raw podcasts
    raw_dir = Path("data/podcasts/raw")
    if raw_dir.exists():
        mp3_files = list(raw_dir.glob("**/*.mp3"))
        if mp3_files:
            smallest = min(mp3_files, key=lambda p: p.stat().st_size)
            episodes.append((str(smallest), f"small episode ({smallest.stat().st_size / 1e6:.1f} MB)"))
    
    return episodes


def print_transcription(segments, max_segments=20):
    """Pretty print transcription segments."""
    print("\n" + "="*80)
    print(f"TRANSCRIPTION ({len(segments)} total segments)")
    print("="*80 + "\n")
    
    # Show first N segments
    for i, seg in enumerate(segments[:max_segments], 1):
        time_range = f"{seg.start//1000:3d}s - {seg.end//1000:3d}s"
        print(f"[{i:3d}] {time_range} | {seg.text}")
    
    if len(segments) > max_segments:
        print(f"\n... and {len(segments) - max_segments} more segments")
    
    print("\n" + "="*80 + "\n")


def test_episode(episode_path, description):
    """Test transcription on a single episode."""
    logger.info(f"\n{'='*80}")
    logger.info(f"Testing: {description}")
    logger.info(f"File: {episode_path}")
    logger.info(f"Size: {Path(episode_path).stat().st_size / 1e6:.1f} MB")
    logger.info(f"{'='*80}\n")
    
    try:
        processor = AudioProcessor("data")
        state_manager = StateManager("data")
        
        # Get audio info
        audio = processor.load_and_validate_audio_file(episode_path)
        logger.info(f"Duration: {audio.duration_seconds:.1f}s")
        
        # Get working directory
        working_dir = processor._get_episode_working_dir(episode_path)
        
        # Export to WAV
        wav_path = os.path.join(working_dir, "audio_for_transcription.wav")
        processor._export_audio_to_wav(audio, wav_path)
        
        # Transcribe
        logger.info("Transcribing...")
        segments = processor.transcribe_with_whisper(wav_path, model_size="base")
        
        logger.info(f"✅ Successfully transcribed {len(segments)} segments")
        print_transcription(segments, max_segments=15)
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Test failed: {e}", exc_info=True)
        return False


def main():
    logger.info("Starting transcription tests...\n")
    
    episodes = find_test_episodes()
    if not episodes:
        logger.error("Could not find test episodes")
        return 1
    
    results = []
    for episode_path, description in episodes:
        success = test_episode(episode_path, description)
        results.append((description, success))
    
    # Summary
    logger.info("\n" + "="*80)
    logger.info("TEST SUMMARY")
    logger.info("="*80)
    for description, success in results:
        status = "✅ PASS" if success else "❌ FAIL"
        logger.info(f"{status}: {description}")
    
    return 0 if all(success for _, success in results) else 1


if __name__ == "__main__":
    sys.exit(main())
