#!/usr/bin/env python3
"""Run the real processing pipeline on specific episode(s), without waiting for cron.

Transcribes (reusing a cached transcript if present), detects ads, and writes the
cut episode to data/podcasts/processed/ — the same output the hourly pipeline
produces, just for episodes you choose. Useful for reprocessing a single episode
after a prompt change instead of waiting for / running the whole pipeline.

Usage:
  python scripts/process_episode.py data/podcasts/raw/<feed>/<ep>.mp3 [more.mp3 ...]
  python scripts/process_episode.py --feed the-ezra-klein-show   # all raw episodes for a feed

Options:
  --feed <slug>   Process every raw episode under data/podcasts/raw/<slug>/
  --cooldown <s>  Seconds to wait between episodes (default 120; 0 to disable)
"""
import argparse
import logging
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skipcastify.services.audio_processor import AudioProcessor
from skipcastify.services.state_manager import StateManager
from skipcastify.utils.logger import setup_logging

logger = logging.getLogger("process_episode")

DATA_DIR = os.environ.get("DATA_DIR") or "data"
RAW_BASE = Path(DATA_DIR) / "podcasts" / "raw"


def _collect_paths(args) -> list[Path]:
    paths: list[Path] = []
    if args.feed:
        feed_dir = RAW_BASE / args.feed
        if not feed_dir.is_dir():
            logger.error("No raw directory for feed '%s' (%s)", args.feed, feed_dir)
            sys.exit(1)
        paths.extend(sorted(feed_dir.glob("*.mp3")))
        if not paths:
            logger.error("No .mp3 files found in %s", feed_dir)
            sys.exit(1)
    for p in args.paths:
        path = Path(p)
        if not path.is_file():
            logger.error("File not found: %s", path)
            sys.exit(1)
        paths.append(path)
    return paths


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="*", help="Raw episode .mp3 file(s) to process")
    parser.add_argument("--feed", help="Process all raw episodes for a feed slug")
    parser.add_argument("--cooldown", type=int, default=120,
                        help="Seconds between episodes (default 120)")
    args = parser.parse_args()

    if not args.paths and not args.feed:
        parser.print_help()
        sys.exit(1)

    setup_logging(DATA_DIR)
    paths = _collect_paths(args)
    processor = AudioProcessor(DATA_DIR)
    state = StateManager(DATA_DIR)

    for i, path in enumerate(paths, 1):
        if i > 1 and args.cooldown > 0:
            logger.info("cooldown %ds before next episode", args.cooldown)
            time.sleep(args.cooldown)
        logger.info("(%d/%d) processing %s", i, len(paths), path.name)
        processor.process(str(path), state)

    logger.info("Done. Processed %d episode(s).", len(paths))


if __name__ == "__main__":
    main()
