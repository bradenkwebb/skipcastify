#!/usr/bin/env python3
"""Ad-hoc transcription of one or more episodes, without waiting for the cron.

Transcribes with Whisper (thermal-gated) and caches the result to
data/transcripts/<episode>_transcript.json — the same location the LLM
regression tests and the pipeline read/write. Useful for building golden-dataset
test fixtures or warming the transcript cache for specific episodes.

Usage:
  # one or more explicit audio files
  python scripts/transcribe_episode.py data/podcasts/raw/<feed>/<ep>.mp3 [more.mp3 ...]

  # every raw .mp3 for a feed (skips ones already cached)
  python scripts/transcribe_episode.py --feed wisdom-of-the-masters

Options:
  --feed <slug>   Transcribe all raw episodes under data/podcasts/raw/<slug>/
  --force         Re-transcribe even if a cached transcript already exists
  --cooldown <s>  Seconds to wait between episodes (default 90; 0 to disable)
"""
import argparse
import logging
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skipcastify.services.audio_processor import AudioProcessor
from skipcastify.services.transcript_cache import TranscriptCache

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("transcribe_episode")

DATA_DIR = "data"
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
    parser.add_argument("paths", nargs="*", help="Audio file(s) to transcribe")
    parser.add_argument("--feed", help="Transcribe all raw episodes for a feed slug")
    parser.add_argument("--force", action="store_true",
                        help="Re-transcribe even if a cached transcript exists")
    parser.add_argument("--cooldown", type=int, default=90,
                        help="Seconds between episodes (default 90)")
    args = parser.parse_args()

    if not args.paths and not args.feed:
        parser.print_help()
        sys.exit(1)

    paths = _collect_paths(args)
    processor = AudioProcessor(DATA_DIR)
    cache = TranscriptCache(str(Path(DATA_DIR) / "transcripts"))

    transcribed = 0
    for i, path in enumerate(paths, 1):
        ep_name = path.stem
        if not args.force and cache.load_cached_transcript(ep_name) is not None:
            logger.info("(%d/%d) cached, skipping %s (use --force to redo)", i, len(paths), ep_name)
            continue

        if transcribed > 0 and args.cooldown > 0:
            logger.info("cooldown %ds before next episode", args.cooldown)
            time.sleep(args.cooldown)

        logger.info("(%d/%d) transcribing %s", i, len(paths), ep_name)
        segments = processor.transcribe_with_whisper(str(path))
        cache.save_transcript(ep_name, segments)
        logger.info("(%d/%d) saved %d segments -> data/transcripts/%s_transcript.json",
                    i, len(paths), len(segments), ep_name)
        transcribed += 1

    logger.info("Done. Transcribed %d episode(s).", transcribed)


if __name__ == "__main__":
    main()
