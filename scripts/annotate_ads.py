#!/usr/bin/env python3
"""
Interactive terminal tool to annotate ads based on cached transcript.

Usage:
    python scripts/annotate_ads.py <episode_name>

This tool:
1. Loads the cached transcript
2. Displays segments with segment numbers
3. Lets you interactively mark which ones are ads
4. Saves ground-truth labels to JSON

Example:
    python scripts/annotate_ads.py philosophize-this-episode_237_the_stoics_are_wrong_-_nietzsche_schopenhauer
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skipcastify.services.transcript_cache import TranscriptCache
from skipcastify.services.ground_truth import GroundTruthStore, GroundTruthLabel
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def parse_segment_input(input_str: str, max_segment: int) -> set:
    """Parse user input for segment numbers.
    
    Accepts:
    - Single number: 15
    - Comma-separated: 15,16,17
    - Range: 15-20
    - Mixed: 15-20,25,30-35
    
    Returns:
        Set of valid segment indices
    """
    segments = set()
    
    try:
        for part in input_str.split(','):
            part = part.strip()
            if not part:
                continue
            
            if '-' in part:
                # Range
                start, end = part.split('-')
                start_idx = int(start.strip())
                end_idx = int(end.strip())
                for i in range(start_idx, end_idx + 1):
                    if 0 <= i <= max_segment:
                        segments.add(i)
            else:
                # Single
                idx = int(part.strip())
                if 0 <= idx <= max_segment:
                    segments.add(idx)
    except ValueError:
        logger.error(f"Invalid input format. Use: 15 or 15,16,17 or 15-20 or 15-20,25")
        return set()
    
    return segments


def display_transcript(segments, ad_indices=None, page=None, page_size=None):
    """Display transcript with segment numbers and marking ads.

    Accepts optional `page` and `page_size` parameters to show a single
    page of segments. If those are not provided, the function falls back
    to showing the full transcript.
    """
    if ad_indices is None:
        ad_indices = set()

    total = len(segments)
    if page is None or page_size is None:
        start_idx = 0
        end_idx = total
        page = 1
        page_size = total
        total_pages = 1
    else:
        total_pages = max(1, (total + page_size - 1) // page_size)
        page = max(1, min(page, total_pages))
        start_idx = (page - 1) * page_size
        end_idx = min(total, start_idx + page_size)

    print("\n" + "="*120)
    print(f"TRANSCRIPT (segments {start_idx}..{end_idx-1} of {total})  Page {page}/{total_pages}")
    print("="*120 + "\n")

    for i in range(start_idx, end_idx):
        seg = segments[i]
        marker = ">> AD" if i in ad_indices else "   "
        time_range = f"[{seg.start}ms-{seg.end}ms]"
        print(f"{marker} {i:4d}. {time_range:20s} {seg.text}")

    print()


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/annotate_ads.py <episode_name>")
        sys.exit(1)
    
    episode_name = sys.argv[1]
    cache = TranscriptCache()
    store = GroundTruthStore()
    
    # Load cached transcript
    segments = cache.load_cached_transcript(episode_name)
    if not segments:
        logger.error(f"No cached transcript found for {episode_name}")
        logger.info(f"First run: python scripts/preview_episode.py <episode>")
        sys.exit(1)
    
    logger.info(f"Loaded {len(segments)} segments from cache")
    
    # Load existing labels if they exist
    existing_labels = store.load_labels(episode_name)
    ad_indices = set()
    if existing_labels:
        # Convert time ranges back to segment indices (approximate)
        for label in existing_labels:
            for i, seg in enumerate(segments):
                if seg.start >= label.start_ms and seg.end <= label.end_ms:
                    ad_indices.add(i)
        logger.info(f"Loaded {len(ad_indices)} segments already marked as ads")
    
    print(f"\nAnnotating episode: {episode_name}")
    print(f"Total segments: {len(segments)}")
    
    # Display first page by default
    page = 1
    page_size = 30
    display_transcript(segments, ad_indices, page=page, page_size=page_size)
    
    # Interactive loop with paging controls
    print("\nINSTRUCTIONS:")
    print("  Enter segment numbers that are ads (use global indices shown on left).")
    print("  Use ranges/comma format, e.g. '15' or '15,16,17' or '15-20'")
    print("  Paging: 'next', 'prev', 'page N', 'perpage N' (change page size)")
    print("  Commands: 'done', 'show', 'clear', 'undo', 'help'\n")

    while True:
        try:
            user_input = input(">> Enter segments or command: ").strip()
        except EOFError:
            # Handle end of input (Ctrl+D)
            break

        if not user_input:
            continue

        cmd = user_input.lower()
        if cmd == 'done':
            break
        if cmd == 'show':
            display_transcript(segments, ad_indices, page=page, page_size=page_size)
            continue
        if cmd == 'clear':
            ad_indices.clear()
            logger.info("Cleared all ad selections")
            display_transcript(segments, ad_indices, page=page, page_size=page_size)
            continue
        if cmd == 'help':
            print("\nCOMMANDS:")
            print("  done   - Finish annotating and save")
            print("  show   - Display transcript page again")
            print("  clear  - Clear all ad selections")
            print("  undo   - Remove last selection")
            print("  help   - Show this help")
            print("\nPAGING:")
            print("  next        - Next page")
            print("  prev        - Previous page")
            print("  page N      - Jump to page N")
            print("  perpage N   - Set page size to N and show page 1")
            print("\nNUMBER FORMATS:")
            print("  15          - Single segment")
            print("  15,16,17    - Multiple segments")
            print("  15-20       - Range of segments")
            print("  15-20,25    - Mix of ranges and singles\n")
            continue
        if cmd == 'undo':
            if ad_indices:
                removed = ad_indices.pop()
                logger.info(f"Removed segment {removed} from ads")
                display_transcript(segments, ad_indices, page=page, page_size=page_size)
            else:
                logger.info("Nothing to undo")
            continue

        # Paging commands
        if cmd == 'next':
            page = page + 1
            display_transcript(segments, ad_indices, page=page, page_size=page_size)
            continue
        if cmd == 'prev':
            page = max(1, page - 1)
            display_transcript(segments, ad_indices, page=page, page_size=page_size)
            continue
        if cmd.startswith('page '):
            try:
                n = int(cmd.split()[1])
                page = n
                display_transcript(segments, ad_indices, page=page, page_size=page_size)
            except Exception:
                logger.error('Invalid page number')
            continue
        if cmd.startswith('perpage '):
            try:
                n = int(cmd.split()[1])
                n = max(1, n)
                page_size = n
                page = 1
                display_transcript(segments, ad_indices, page=page, page_size=page_size)
            except Exception:
                logger.error('Invalid perpage value')
            continue

        # Otherwise parse as segment selection (global indices)
        new_ads = parse_segment_input(user_input, len(segments) - 1)
        if new_ads:
            ad_indices.update(new_ads)
            logger.info(f"Added {len(new_ads)} segment(s). Total ads: {len(ad_indices)}")
            display_transcript(segments, ad_indices, page=page, page_size=page_size)
        else:
            logger.error("Could not parse input")
    
    # Convert segment indices back to time ranges and save
    if not ad_indices:
        logger.warning("No ads marked. Exiting without saving.")
        sys.exit(0)
    
    logger.info(f"\nSaving {len(ad_indices)} ad segments...")
    
    # Group consecutive segments into ranges
    sorted_indices = sorted(ad_indices)
    ad_ranges = []
    
    i = 0
    while i < len(sorted_indices):
        start_idx = sorted_indices[i]
        end_idx = start_idx
        
        # Find consecutive segments
        while i + 1 < len(sorted_indices) and sorted_indices[i + 1] == sorted_indices[i] + 1:
            i += 1
            end_idx = sorted_indices[i]
        
        # Create label from segment time range
        start_ms = segments[start_idx].start
        end_ms = segments[end_idx].end
        
        label = GroundTruthLabel(
            start_ms=start_ms,
            end_ms=end_ms,
            label="ADVERTISEMENT"
        )
        ad_ranges.append(label)
        
        i += 1
    
    # Save labels
    store.save_labels(episode_name, ad_ranges)
    
    logger.info(f"Saved {len(ad_ranges)} ad spans to ground-truth labels")
    logger.info(f"  File: {store.get_label_path(episode_name)}")
    
    print(f"\n✓ Annotations saved!")
    print(f"\nNow evaluate with:")
    print(f"  python scripts/evaluate_ad_detection.py {episode_name}")


if __name__ == '__main__':
    main()
