#!/usr/bin/env python3
"""Exact ad labels for feeds that insert ads server-side. Makes no LLM calls.

A host that inserts ads server-side stitches pre-encoded MP3 frames into the
episode, and commonly serves a different rendering to clients that do not look
like a podcast player. Fetching a second rendering that way gives a reference
copy of the same encode, and aligning the two recovers every inserted span to
about 26ms -- ground truth for the episodes already on disk, with no hand
labelling and no transcription.

Some feeds leave the reference unmonetized and some leave a house spot in each
slot. Either way the ads are what the episode on disk carries and the
reference does not.

    uv run python scripts/dai_ground_truth.py --feed odd-lots
    uv run python scripts/dai_ground_truth.py --feed odd-lots --limit 3 --dry-run

What it writes are proposals, not finished labels, so they are written
unreviewed and the eval ignores them until reviewed. A diff sees only the spots
the host inserted, and locates them without identifying them, so reviewing a
file in /label means two things: confirm "ad" against self_promo on each
proposed span, and add any ad the host reads aloud, which belongs to the
episode master and so appears in both renderings and in neither the diff.

Reviewing therefore completes the episode. The file becomes an ordinary label
set that happens to have started with exact boundaries, scored like any other.
The source stays on it as provenance; nothing branches on it.

Only an unreviewed file this script wrote is ever replaced. Hand labels and
confirmed labels alike are left alone, so re-running a feed cannot undo a
review. To re-derive a confirmed episode, delete its label file first.

Nothing is written unless every check passes: the reference has to be the same
encode, run to the length the feed declares, and differ by a plausible amount.
A feed whose renderings are identical has no insertion, so it gets no labels
and still needs hand labelling.

Expect this to work on recent episodes. An episode downloaded months ago can
fail the same-encode check because the show re-encoded its master since, and
some hosts re-encode on insertion rather than stitching frames, which rules
their feeds out altogether.
"""

import argparse
import json
import logging
import os
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urljoin

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import feedparser  # noqa: E402
import requests  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

from skipcastify.services import dai_diff, labeling  # noqa: E402
from skipcastify.services.subscriptions import check_url  # noqa: E402
from skipcastify.utils.utils import safe_filename, slugify  # noqa: E402

logger = logging.getLogger("dai_ground_truth")

# Hosts serve the unmonetized rendering to clients that are plainly not players.
REFERENCE_UA = "python-requests/2.32.3"

MAX_BYTES = 600 * 1024 * 1024
TIMEOUT_S = 60
# Tracking prefixes chain several hops before the CDN: six is routine.
MAX_REDIRECTS = 12

# A truncated reference aligns as a perfect prefix and reads the missing tail
# as one ad, so it has to run about as long as the feed declares. Running over
# is normal: a declared duration describes the master, and a reference may
# carry house spots of its own.
DECLARED_SHORTFALL_S = 120.0
# One break longer than this, or this share of the episode, means the alignment
# found something other than ad insertion.
MAX_SPAN_S = 600.0
MAX_SHARE = 0.40


def declared_duration_s(entry) -> float | None:
    raw = (entry.get("itunes_duration") or "").strip()
    if not raw:
        return None
    try:
        parts = [float(p) for p in raw.split(":")]
    except ValueError:
        return None
    return sum(p * 60 ** i for i, p in enumerate(reversed(parts)))


def feed_urls(paths: list[Path]) -> dict[str, str]:
    """slug -> feed url, for every rss outline in the given OPML files."""
    out = {}
    for path in paths:
        if not path.is_file():
            continue
        for outline in ET.parse(path).getroot().iter("outline"):
            url = (outline.get("xmlUrl") or "").strip()
            title = outline.get("title") or outline.get("text")
            if url and title:
                out.setdefault(slugify(title), url)
    return out


def fetch_reference(url: str, dest: Path, user_agent: str) -> int:
    """Download with each redirect hop re-checked. Returns bytes written."""
    headers = {"User-Agent": user_agent}
    for _ in range(MAX_REDIRECTS):
        url = check_url(url)
        with requests.get(url, headers=headers, timeout=TIMEOUT_S, stream=True,
                          allow_redirects=False) as r:
            if r.is_redirect or r.is_permanent_redirect:
                url = urljoin(url, r.headers.get("Location", ""))
                continue
            r.raise_for_status()
            written = 0
            with open(dest, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    written += len(chunk)
                    if written > MAX_BYTES:
                        raise ValueError("reference rendering is implausibly large")
                    f.write(chunk)
            return written
    raise ValueError("too many redirects")


def check(spans: list[dict], reference_ms: float, episode_ms: float,
          declared_s: float | None) -> str | None:
    """Why these spans must not be trusted, or None when they hold up."""
    if declared_s is not None and reference_ms / 1000 < declared_s - DECLARED_SHORTFALL_S:
        return (f"reference runs {reference_ms / 1000:.0f}s against the "
                f"{declared_s:.0f}s the feed declares, so it is truncated")
    if not spans:
        return "renderings are identical: this feed inserts nothing, so it needs hand labels"
    inserted = sum(s["end_ms"] - s["start_ms"] for s in spans)
    if inserted / episode_ms > MAX_SHARE:
        return f"{inserted / episode_ms:.0%} of the episode reads as inserted"
    longest = max(s["end_ms"] - s["start_ms"] for s in spans) / 1000
    if longest > MAX_SPAN_S:
        return f"longest break is {longest:.0f}s"
    return None


def protected_because(data_dir: Path, name: str) -> str | None:
    """Why this episode's labels must not be replaced, or None when they may be.

    Only an unreviewed file this script wrote itself is safe to overwrite.
    Anything touched by hand carries work a fresh diff would throw away: the
    spans it proposes are unreviewed and every label is "ad", so replacing a
    confirmed file would undo both the confirmation and any relabelling.
    """
    path = labeling.label_path(data_dir, name)
    if not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text())
    except (OSError, ValueError):
        return "label file is unreadable, left alone"
    if labeling.label_source(doc) == labeling.MANUAL:
        return "has hand labels, left alone"
    if doc.get("reviewed"):
        return "labels already reviewed, left alone"
    return None


def episodes_for(data_dir: Path, slug: str, filters: list[str], limit: int) -> list[Path]:
    """Raw episodes of one feed that have a cached transcript, newest first."""
    raw = data_dir / "podcasts" / "raw" / slug
    if not raw.is_dir():
        return []
    found = [p for p in raw.glob("*.mp3")
             if labeling.valid_episode(data_dir, p.stem)
             and (not filters or any(f in p.stem for f in filters))]
    found.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return found[:limit]


def run_feed(data_dir: Path, slug: str, url: str, args) -> list[dict]:
    chosen = episodes_for(data_dir, slug, args.episode, args.limit)
    if not chosen:
        logger.warning("%s: no episodes on disk with a cached transcript", slug)
        return []

    entries = {}
    for entry in feedparser.parse(url).entries:
        title = entry.get("title")
        if title:
            entries.setdefault(safe_filename(title, slug), entry)

    rows = []
    for mp3 in chosen:
        row = {"feed": slug, "episode": mp3.stem}
        protected = protected_because(data_dir, mp3.stem)
        if protected:
            row["skipped"] = protected
            rows.append(row)
            continue
        entry = entries.get(mp3.name)
        if entry is None:
            row["skipped"] = "no feed entry matches this file"
            rows.append(row)
            continue
        audio_url = next((link.href for link in entry.get("links", [])
                          if link.get("rel") == "enclosure"), None) or \
            (entry.enclosures[0].get("href") if entry.get("enclosures") else None)
        if not audio_url:
            row["skipped"] = "feed entry has no enclosure"
            rows.append(row)
            continue

        with tempfile.TemporaryDirectory(dir=args.tmp_dir) as tmp:
            dest = Path(tmp) / "reference.mp3"
            try:
                size = fetch_reference(audio_url, dest, args.user_agent)
            except Exception as e:
                row["skipped"] = f"reference download failed: {type(e).__name__}: {e}"
                rows.append(row)
                continue
            logger.info("%s: reference %.1f MB against %.1f MB on disk",
                        mp3.stem[:60], size / 1e6, mp3.stat().st_size / 1e6)

            episode = dai_diff.read_frames(mp3.read_bytes())
            reference = dai_diff.read_frames(dest.read_bytes())
            try:
                result = dai_diff.align(episode, reference)
            except dai_diff.NotComparable as e:
                row["skipped"] = str(e)
                rows.append(row)
                continue

        spans = result.inserted
        episode_ms, reference_ms = dai_diff.duration_ms(episode), dai_diff.duration_ms(reference)
        row.update(episode_s=episode_ms / 1000, reference_s=reference_ms / 1000,
                   spans=len(spans), inserted_s=result.inserted_ms / 1000,
                   replaced=len(result.replaced), match=result.match_ratio)
        reason = check(spans, reference_ms, episode_ms, declared_duration_s(entry))
        if reason:
            row["skipped"] = reason
        elif args.dry_run:
            row["written"] = False
        else:
            labeling.save_labels(data_dir, mp3.stem, spans, reviewed=False,
                                 source=labeling.DAI_DIFF)
            row["written"] = True
        rows.append(row)
    return rows


def main(argv=None) -> int:
    load_dotenv()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--feed", action="append", default=[],
                    help="feed slug; repeat for several (default: every feed on disk)")
    ap.add_argument("--episode", action="append", default=[],
                    help="substring filter on the episode name; repeatable")
    ap.add_argument("--limit", type=int, default=5, help="episodes per feed (default 5)")
    ap.add_argument("--data-dir", default=os.environ.get("DATA_DIR", "data"))
    ap.add_argument("--opml", action="append", default=[],
                    help="OPML to resolve feed URLs from; repeatable")
    ap.add_argument("--user-agent", default=REFERENCE_UA)
    ap.add_argument("--tmp-dir", help="where to stage reference downloads")
    ap.add_argument("--dry-run", action="store_true", help="report without writing labels")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    data_dir = Path(args.data_dir).resolve()

    opml = [Path(p) for p in args.opml]
    if not opml:
        opml = [Path(p) for p in (os.environ.get("SUBSCRIPTIONS"),
                                  os.environ.get("UPSTREAM_OPML"),
                                  Path(__file__).resolve().parents[1] / "upstream.opml")
                if p]
    known = feed_urls(opml)
    if not known:
        print(f"No feed URLs found in {[str(p) for p in opml]}", file=sys.stderr)
        return 1

    slugs = args.feed or sorted(
        d.name for d in (data_dir / "podcasts" / "raw").iterdir() if d.is_dir())
    rows = []
    for slug in slugs:
        if slug not in known:
            logger.warning("%s: not in the subscription list, skipping", slug)
            continue
        rows += run_feed(data_dir, slug, known[slug], args)

    print()
    print(f"{'episode':52} {'on disk':>9} {'reference':>10} {'inserted':>9} "
          f"{'breaks':>7} {'match':>7}  result")
    for r in rows:
        if "episode_s" in r:
            head = (f"{r['episode'][:51]:52} {r['episode_s']:8.1f}s {r['reference_s']:9.1f}s "
                    f"{r['inserted_s']:8.1f}s {r['spans']:7} {r['match']:6.2%}")
        else:
            head = f"{r['episode'][:51]:52} {'':>9} {'':>10} {'':>9} {'':>7} {'':>7}"
        outcome = r.get("skipped") or ("labels written" if r.get("written") else "ok, not written")
        print(f"{head}  {outcome}")

    written = sum(1 for r in rows if r.get("written"))
    skipped = sum(1 for r in rows if "skipped" in r)
    print(f"\n{written} label file(s) written, {skipped} episode(s) skipped")
    if written:
        print("These are proposals and the eval ignores them until reviewed. "
              "In /label, for each episode:")
        print("  - confirm ad against self_promo on each span; the boundaries "
              "are already exact")
        print("  - add any ad the host reads aloud, which no rendering diff "
              "can see")
    return 0


if __name__ == "__main__":
    sys.exit(main())
