#!/usr/bin/env python3
"""Run preview processing on a single episode and write preview MP3 + transcript.

Usage:
  python scripts/preview_episode.py <path-to-episode.mp3>
  python scripts/preview_episode.py <path-to-episode.mp3> --use-cached
  python scripts/preview_episode.py <path-to-episode.mp3> --force-transcribe

Options:
  --use-cached: Use cached transcript if available (fast iteration)
  --force-transcribe: Force re-transcription even if cached

Output:
  - Preview MP3: data/podcasts/processed_preview/<episode>_preview.mp3
  - Transcript: data/podcasts/processed_preview/<episode>_preview_transcript.txt
  - Predictions: data/podcasts/processed_preview/<episode>_predictions.json
"""
import sys
import json
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skipcastify.services.audio_processor import AudioProcessor, Segment
from skipcastify.services.transcript_cache import TranscriptCache
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/preview_episode.py <episode.mp3> [--use-cached|--force-transcribe]")
        sys.exit(1)
    
    ep = sys.argv[1]
    use_cached = "--use-cached" in sys.argv
    force_transcribe = "--force-transcribe" in sys.argv
    
    out_dir = Path("data/podcasts/processed_preview")
    out_dir.mkdir(parents=True, exist_ok=True)
    ep_name = Path(ep).stem
    out_path = out_dir / f"{ep_name}_preview.mp3"

    processor = AudioProcessor("data")
    cache = TranscriptCache()
    
    try:
        # Check cache first if requested
        if use_cached and not force_transcribe:
            cached_segments = cache.load_cached_transcript(ep_name)
            if cached_segments:
                logger.info(f"Using cached transcript (--use-cached flag)")
                # Convert cached segments to Segment objects
                segments = [Segment(start=cs.start, end=cs.end, text=cs.text) for cs in cached_segments]
                result = processor.process_preview_with_segments(ep, str(out_path), segments)
            else:
                logger.info(f"No cached transcript found, transcribing...")
                result = processor.process_preview(ep, str(out_path))
                cache.save_transcript(ep_name, result.get('segments', []))
        else:
            result = processor.process_preview(ep, str(out_path))
            cache.save_transcript(ep_name, result.get('segments', []))
        
        logger.info(f"\n✓ Preview complete!")
        logger.info(f"  Audio:        {result['audio_path']}")
        logger.info(f"  Transcript:   {result['transcript_path']}")
        logger.info(f"  Predictions:  {result.get('predictions_path', 'N/A')}")
        
        # Print the transcript to stdout for immediate review
        print("\n" + "="*100)
        print("PREVIEW TRANSCRIPT (showing what will be removed):")
        print("="*100 + "\n")
        
        with open(result['transcript_path'], 'r', encoding='utf-8') as f:
            print(f.read())
        
        # Show span comparison across all stages
        def _print_spans(title: str, spans: list) -> None:
            print("\n" + "="*100)
            print(f"{title}: {len(spans)} ad span(s) detected")
            print("="*100 + "\n")
            for i, span in enumerate(spans, 1):
                start = int(span.get('start_ms', 0))
                end = int(span.get('end_ms', 0))
                duration_s = (end - start) / 1000
                label = span.get('label', 'ADVERTISEMENT')
                rationale = span.get('rationale', '')
                print(f"{i}. [{start}ms-{end}ms] ({duration_s:.1f}s) - {label}")
                if rationale:
                    print(f"   {rationale}")

        stage1 = result.get('stage1_spans') or []
        postprocessed = result.get('postprocessed_spans') or []
        refined = result.get('refined_spans')

        if stage1:
            _print_spans("STAGE 1 — raw LLM output", stage1)
        if postprocessed and postprocessed != stage1:
            _print_spans("POST-PROCESSING — merged + CTA extended", postprocessed)
        elif postprocessed:
            print(f"\n(Post-processing: no changes from stage 1)")
        if refined is not None:
            _print_spans("STAGE 2 — LLM boundary refinement", refined)

        if not stage1:
            _print_spans("PREDICTIONS", result.get('ad_spans') or [])
        
        print(f"\nNext steps:")
        print(f"  1. Annotate ads:  python scripts/annotate_ads.py {ep_name}")
        print(f"  2. Evaluate:      python scripts/evaluate_ad_detection.py {ep_name}")
        print(f"  3. Re-run (fast): python scripts/preview_episode.py <episode> --use-cached")
        
    except Exception as e:
        logger.error(f"Preview failed: {e}")
        raise


if __name__ == '__main__':
    main()
