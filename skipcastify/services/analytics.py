"""Aggregations over the pipeline event log for the stats CLI and dashboard.

Single source of truth so the CLI (`scripts/stats.py`) and the web dashboard
never drift. Reads `data/metrics/events.jsonl` (see metrics.log_event).

Design note — ad volume is computed from the *merged, ad-labeled spans*, NOT
from `ads_removed_s`. Before the "cut only ad spans" fix, the stitcher also
dropped silence/music, so `ads_removed_s` (and `ads_pct`) over-reported ads for
feeds with quiet passages (e.g. Wisdom of the Masters' phantom ~25%). Spans are
the detector's actual ad calls, so deriving minutes from them is independent of
that stitching behavior and consistent across all history.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

AD_LABELS = {"ADVERTISEMENT", "SPONSOR"}


def load_events(data_dir: str) -> list[dict]:
    """Parse events.jsonl into dicts, skipping malformed lines."""
    path = Path(data_dir) / "metrics" / "events.jsonl"
    if not path.exists():
        return []
    events: list[dict] = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except Exception:
                pass
    return events


def _merge_ad_spans(spans: list[dict]) -> list[tuple[int, int]]:
    """Return non-overlapping (start_ms, end_ms) for ad/sponsor spans only.

    The raw detector output can overlap (adjacent windows re-flag the same
    break), so summing durations naively double-counts. Merge first.
    """
    ranges = sorted(
        (s["start_ms"], s["end_ms"])
        for s in spans
        if s.get("label") in AD_LABELS and s["end_ms"] > s["start_ms"]
    )
    if not ranges:
        return []
    merged = [list(ranges[0])]
    for start, end in ranges[1:]:
        if start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(a, b) for a, b in merged]


def _ad_ms(ev: dict) -> int:
    """Milliseconds of genuine ad content in an episode_complete event."""
    return sum(b - a for a, b in _merge_ad_spans(ev.get("spans", [])))


def _ad_pct(ev: dict) -> float:
    dur = ev.get("original_duration_s") or 0
    if dur <= 0:
        return 0.0
    return (_ad_ms(ev) / 1000.0) / dur * 100.0


def _completes(events: list[dict]) -> list[dict]:
    return [e for e in events if e.get("event") == "episode_complete"]


def _day(ev: dict) -> str:
    return ev.get("ts", "")[:10]


def feed_summary(events: list[dict]) -> list[dict]:
    """Per-feed rollup: episode count, ad density, total/avg ad minutes."""
    by_feed: dict[str, list[dict]] = defaultdict(list)
    for ev in _completes(events):
        by_feed[ev.get("feed_slug", "unknown")].append(ev)

    rows = []
    for slug, eps in by_feed.items():
        eps_sorted = sorted(eps, key=lambda e: e.get("ts", ""))
        ad_minutes = [_ad_ms(e) / 60000.0 for e in eps_sorted]
        pcts = [_ad_pct(e) for e in eps_sorted]
        n = len(eps_sorted)
        # Trailing run of ad-free episodes -> drop-candidate signal.
        ad_free_streak = 0
        for e in reversed(eps_sorted):
            if _ad_ms(e) == 0:
                ad_free_streak += 1
            else:
                break
        rows.append({
            "feed_slug": slug,
            "episodes": n,
            "avg_ad_pct": round(sum(pcts) / n, 2) if n else 0.0,
            "total_ad_minutes": round(sum(ad_minutes), 1),
            "avg_ad_minutes": round(sum(ad_minutes) / n, 2) if n else 0.0,
            "last_processed": eps_sorted[-1].get("ts", "")[:19],
            "ad_free_streak": ad_free_streak,
        })
    return sorted(rows, key=lambda r: r["total_ad_minutes"], reverse=True)


def daily_series(events: list[dict]) -> list[dict]:
    """Per-day throughput: episodes processed, distinct feeds, ad minutes."""
    by_day_eps: dict[str, int] = defaultdict(int)
    by_day_feeds: dict[str, set] = defaultdict(set)
    by_day_admin: dict[str, float] = defaultdict(float)
    for ev in _completes(events):
        d = _day(ev)
        by_day_eps[d] += 1
        by_day_feeds[d].add(ev.get("feed_slug"))
        by_day_admin[d] += _ad_ms(ev) / 60000.0
    return [
        {
            "date": d,
            "episodes": by_day_eps[d],
            "feeds": len(by_day_feeds[d]),
            "ad_minutes": round(by_day_admin[d], 1),
        }
        for d in sorted(by_day_eps)
    ]


def ad_signatures(events: list[dict]) -> list[dict]:
    """Per-feed ad positions, normalized to episode length (0..1).

    Each episode becomes a track of normalized [start, end] ad spans — the raw
    material for the "where do ads live / does this feed have a recognizable
    signature" view.
    """
    by_feed: dict[str, list[dict]] = defaultdict(list)
    for ev in _completes(events):
        dur_ms = (ev.get("original_duration_s") or 0) * 1000
        if dur_ms <= 0:
            continue
        spans = [
            {"start": round(a / dur_ms, 4), "end": round(b / dur_ms, 4)}
            for a, b in _merge_ad_spans(ev.get("spans", []))
        ]
        by_feed[ev.get("feed_slug", "unknown")].append({
            "episode": ev.get("episode_name", ""),
            "duration_min": round((ev.get("original_duration_s") or 0) / 60.0, 1),
            "spans": spans,
        })
    return [
        {"feed_slug": slug, "episodes": tracks}
        for slug, tracks in sorted(by_feed.items())
    ]


def drop_candidates(events: list[dict], min_episodes: int = 2) -> list[dict]:
    """Feeds whose entire recent run is ad-free — candidates to drop from the
    pipeline (the Wisdom / Politics workflow, data-driven)."""
    out = []
    for row in feed_summary(events):
        if row["episodes"] >= min_episodes and row["ad_free_streak"] == row["episodes"]:
            out.append({
                "feed_slug": row["feed_slug"],
                "episodes": row["episodes"],
                "last_processed": row["last_processed"],
            })
    return out


def overview(events: list[dict]) -> dict:
    completes = _completes(events)
    total_ad_min = sum(_ad_ms(e) for e in completes) / 60000.0
    llm = [e for e in events if e.get("event") == "llm_section"]
    return {
        "episodes_processed": len(completes),
        "feeds": len({e.get("feed_slug") for e in completes}),
        "total_ad_minutes": round(total_ad_min, 1),
        "total_llm_cost_usd": round(sum(e.get("cost_usd", 0) for e in llm), 4),
        "first_event": min((e.get("ts", "") for e in events), default=""),
        "last_event": max((e.get("ts", "") for e in events), default=""),
    }


def build_payload(data_dir: str) -> dict:
    """One JSON blob with everything the dashboard needs."""
    events = load_events(data_dir)
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "overview": overview(events),
        "feeds": feed_summary(events),
        "daily": daily_series(events),
        "signatures": ad_signatures(events),
        "drop_candidates": drop_candidates(events),
    }
