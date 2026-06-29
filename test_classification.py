#!/usr/bin/env python3
"""
Comprehensive test script for the complete audio processing pipeline.

Tests transcription, classification, aggregation, and audio cutting on real episodes.
"""

import os
import sys
from pathlib import Path
from datetime import datetime

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from skipcastify.services.audio_processor import AudioProcessor, Segment
from skipcastify.services.segment_classifier import SegmentClassifier
from skipcastify.models.content import ContentType


def find_test_episodes():
    """Find test audio files to process."""
    test_files = []
    
    # Look for short test file
    short_test = Path("data/temp/short.mp3")
    if short_test.exists():
        test_files.append((str(short_test), "Short test (20s)"))
    
    # Look for real episodes
    raw_dir = Path("data/podcasts/raw")
    if raw_dir.exists():
        for podcast_dir in sorted(raw_dir.iterdir()):
            if podcast_dir.is_dir():
                episodes = list(podcast_dir.glob("*.mp3"))
                if episodes:
                    # Get smallest episode for faster testing
                    smallest = min(episodes, key=lambda p: p.stat().st_size)
                    size_mb = smallest.stat().st_size / (1024 * 1024)
                    duration_approx = size_mb * 1.4  # Very rough estimate (1 min ≈ 0.7 MB MP3)
                    test_files.append((
                        str(smallest), 
                        f"Episode: {smallest.stem[:40]} (~{size_mb:.1f}MB, ~{int(duration_approx)}min)"
                    ))
                    break  # Just use one for now
    
    return test_files


def print_segment_table(segments, title, max_rows=20):
    """Pretty-print segments in a table format."""
    print(f"\n{title}")
    print("=" * 120)
    
    if not segments:
        print("(no segments)")
        return
    
    # Print header
    print(f"{'#':<4} {'Start':<12} {'End':<12} {'Duration':<10} {'Type':<15} {'Conf':<6} {'Merged':<7} {'Preview':<50}")
    print("-" * 120)
    
    for i, seg in enumerate(segments[:max_rows], 1):
        start_s = seg.start / 1000
        end_s = seg.end / 1000
        duration_s = (seg.end - seg.start) / 1000
        
        # Check segment type to determine which format this is
        if hasattr(seg, 'segment_count'):
            # AggregatedSegment from classifier
            seg_type = seg.content_type.value
            merged = seg.segment_count
            text = " ".join(seg.text)[:50]
            conf = "-"
        elif hasattr(seg, 'confidence'):
            # ClassifiedSegment
            seg_type = seg.content_type.value
            merged = "-"
            text = seg.text[:50]
            conf = f"{seg.confidence:.2f}"
        else:
            # Raw Segment from transcription
            seg_type = "TRANS"
            merged = "-"
            text = seg.text[:50]
            conf = "-"
        
        preview = text.replace("\n", " ")
        
        print(
            f"{i:<4} {start_s:>8.1f}s   {end_s:>8.1f}s   {duration_s:>7.1f}s   {seg_type:<15} "
            f"{conf:<6} {str(merged):<7} {preview:<50}"
        )
    
    if len(segments) > max_rows:
        print(f"... and {len(segments) - max_rows} more segments")
    
    print()


def print_statistics(segments, title):
    """Print statistics about segments."""
    print(f"\n{title}")
    print("-" * 60)
    
    if not segments:
        print("(no segments)")
        return
    
    # Count by type
    type_counts = {}
    type_duration = {}
    
    for seg in segments:
        # Get content type appropriately
        if hasattr(seg, 'content_type'):
            seg_type = seg.content_type
        else:
            # Raw segment, skip statistics for raw transcription
            continue
            
        duration_s = (seg.end - seg.start) / 1000
        
        if seg_type not in type_counts:
            type_counts[seg_type] = 0
            type_duration[seg_type] = 0
        
        type_counts[seg_type] += 1
        type_duration[seg_type] += duration_s
    
    if not type_counts:
        print("(no classified segments)")
        return
    
    total_duration_s = sum(type_duration.values())
    
    print(f"Total segments: {len([s for s in segments if hasattr(s, 'content_type')])}")
    print(f"Total duration: {total_duration_s:.1f}s")
    print("\nBreakdown by type:")
    
    for seg_type in sorted(type_counts.keys(), key=lambda x: type_counts[x], reverse=True):
        count = type_counts[seg_type]
        duration = type_duration[seg_type]
        percentage = (duration / total_duration_s * 100) if total_duration_s > 0 else 0
        
        type_name = seg_type.value if hasattr(seg_type, 'value') else str(seg_type)
        print(f"  {type_name:<15} : {count:>3} segments, {duration:>8.1f}s ({percentage:>5.1f}%)")


def test_episode(file_path, description):
    """Test the complete pipeline on a single episode."""
    print(f"\n\n{'=' * 120}")
    print(f"Testing: {description}")
    print(f"File: {file_path}")
    print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print('=' * 120)
    
    processor = AudioProcessor("data")
    
    # Step 1: Validate and load
    print("\n[1/4] Validating audio file...")
    try:
        audio = processor.load_and_validate_audio_file(file_path)
        print(f"[OK] Audio loaded: {audio.duration_seconds:.1f}s, {audio.channels} channel(s), {audio.frame_rate} Hz")
    except Exception as e:
        print(f"[ERROR] Failed to load audio: {e}")
        return
    
    # Step 2: Transcribe
    print(f"\n[2/4] Transcribing with Whisper...")
    try:
        segments = processor.transcribe_with_whisper(file_path, model_size="base")
        print(f"[OK] Transcribed {len(segments)} segments")
        print_segment_table(segments, "Raw transcription segments (first 10):", max_rows=10)
        print_statistics(segments, "Transcription Statistics")
    except Exception as e:
        print(f"[ERROR] Transcription failed: {e}")
        return
    
    # Step 3: Classify
    print(f"\n[3/4] Classifying segments...")
    try:
        classifier = SegmentClassifier(use_ollama=True)
        classified = classifier.classify_segments(segments)
        print(f"[OK] Classified {len(classified)} segments")
        print_segment_table(classified, "Classified segments (first 10):", max_rows=10)
        print_statistics(classified, "Classification Statistics")
    except Exception as e:
        print(f"[ERROR] Classification failed: {e}")
        return
    
    # Step 4: Aggregate
    print(f"\n[4/4] Aggregating segments...")
    try:
        aggregated = classifier.aggregate_segments(classified)
        print(f"[OK] Aggregated into {len(aggregated)} groups")
        print_segment_table(aggregated, "Aggregated segments (first 15):", max_rows=15)
        print_statistics(aggregated, "Aggregation Statistics")
    except Exception as e:
        print(f"[ERROR] Aggregation failed: {e}")
        return
    
    # Bonus: Show audio cutting simulation
    print(f"\n[BONUS] Simulating audio cutting...")
    try:
        content_duration = 0
        for agg_seg in aggregated:
            if agg_seg.content_type == ContentType.CONTENT:
                content_duration += (agg_seg.end - agg_seg.start) / 1000
        
        original_duration = audio.duration_seconds
        removed_duration = original_duration - content_duration
        removal_percentage = (removed_duration / original_duration * 100) if original_duration > 0 else 0
        
        print(f"[OK] Would cut and stitch:")
        print(f"  Original duration: {original_duration:.1f}s")
        print(f"  Content duration:  {content_duration:.1f}s")
        print(f"  Would remove:      {removed_duration:.1f}s ({removal_percentage:.1f}%)")
    except Exception as e:
        print(f"[ERROR] Simulation failed: {e}")
    
    print("\n" + "=" * 120)


def main():
    """Run tests on available episodes."""
    print("\n" + "=" * 120)
    print("AUDIO PROCESSING PIPELINE - COMPREHENSIVE TEST")
    print("=" * 120)
    
    test_episodes = find_test_episodes()
    
    if not test_episodes:
        print("No test episodes found!")
        print("Expected to find audio files in:")
        print("  - data/temp/short.mp3")
        print("  - data/podcasts/raw/*/episode.mp3")
        return
    
    print(f"\nFound {len(test_episodes)} test episode(s):")
    for i, (path, desc) in enumerate(test_episodes, 1):
        print(f"  {i}. {desc}")
        print(f"     {path}")
    
    # Run tests
    for file_path, description in test_episodes:
        try:
            test_episode(file_path, description)
        except KeyboardInterrupt:
            print("\n\nTest interrupted by user.")
            break
        except Exception as e:
            print(f"\n\n[ERROR] Unexpected error during test: {e}")
            import traceback
            traceback.print_exc()
    
    print("\n" + "=" * 120)
    print("TEST COMPLETE")
    print("=" * 120)


if __name__ == "__main__":
    main()
