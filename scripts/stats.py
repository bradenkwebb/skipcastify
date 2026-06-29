#!/usr/bin/env python3
"""Pipeline metrics reporting.

Usage:
  uv run python scripts/stats.py                          # everything
  uv run python scripts/stats.py feeds                    # ad density + savings per feed
  uv run python scripts/stats.py tokens                   # LLM token/cost breakdown
  uv run python scripts/stats.py transcription            # Whisper time per feed
  uv run python scripts/stats.py utilization              # machine busy % + CPU temp
  uv run python scripts/stats.py breakdown --date 2026-06-29  # per-day per-feed breakdown
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_events(data_dir: str) -> list[dict]:
    path = Path(data_dir) / "metrics" / "events.jsonl"
    if not path.exists():
        return []
    events = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    ev = json.loads(line)
                    ev["_ts"] = datetime.fromisoformat(ev["ts"])
                    events.append(ev)
                except Exception:
                    pass
    return events


def load_monitor_log(log_path: str) -> list[tuple[datetime, float]]:
    """Return [(datetime, cpu_temp), ...] with day-rollover applied."""
    pattern = re.compile(r"\[(\d{2}:\d{2}:\d{2})\] CPU: ([\d.]+)°C")
    entries: list[tuple[datetime, float]] = []
    base = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    with open(log_path) as f:
        for line in f:
            m = pattern.search(line)
            if m:
                t = datetime.strptime(m.group(1), "%H:%M:%S")
                entries.append((t, float(m.group(2))))

    # Anchor to a real date by applying day rollovers
    if entries:
        anchored: list[tuple[datetime, float]] = []
        prev = entries[0][0]
        day_offset = 0
        for t, cpu in entries:
            if t < prev:
                day_offset += 1
            anchored.append((t + timedelta(days=day_offset), cpu))
            prev = t
        # Shift all entries so the last one lands on today
        if anchored:
            last_t = anchored[-1][0]
            today_approx = datetime.now().replace(second=0, microsecond=0)
            shift = timedelta(
                hours=today_approx.hour - last_t.hour,
                minutes=today_approx.minute - last_t.minute,
            )
            anchored = [(t + shift, cpu) for t, cpu in anchored]
        return anchored
    return []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_duration(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60:02d}s"
    return f"{s // 3600}h {(s % 3600) // 60:02d}m"


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _events_by_type(events: list[dict], event_type: str) -> list[dict]:
    return [e for e in events if e.get("event") == event_type]


def _filter_date(events: list[dict], date_str: str) -> list[dict]:
    return [e for e in events if e["ts"].startswith(date_str)]


# ---------------------------------------------------------------------------
# Report: Feeds (ad density + listening time saved)
# ---------------------------------------------------------------------------

def report_feeds(events: list[dict]) -> None:
    complete = _events_by_type(events, "episode_complete")
    if not complete:
        print("No episode_complete events found. Process some episodes first.")
        return

    by_feed: dict[str, list[dict]] = defaultdict(list)
    for ev in complete:
        by_feed[ev.get("feed_slug", "unknown")].append(ev)

    print("\nAd Density & Listening Time Saved")
    print("─" * 72)
    print(f"{'Feed':<30} {'Episodes':>8} {'Avg ad%':>8} {'Total saved':>12} {'Avg/ep':>10}")
    print("─" * 72)

    total_saved = 0.0
    for slug in sorted(by_feed):
        eps = by_feed[slug]
        ads_pcts = [e.get("ads_pct", 0) for e in eps]
        saved = [e.get("ads_removed_s", 0) for e in eps]
        total_slug_saved = sum(saved)
        total_saved += total_slug_saved
        avg_pct = _mean(ads_pcts)
        avg_saved = _mean(saved)
        print(f"{slug:<30} {len(eps):>8} {avg_pct:>7.1f}% {_fmt_duration(total_slug_saved):>12} {_fmt_duration(avg_saved):>10}")

    print("─" * 72)
    total_eps = sum(len(v) for v in by_feed.values())
    print(f"{'TOTAL':<30} {total_eps:>8} {'':>8} {_fmt_duration(total_saved):>12}")


# ---------------------------------------------------------------------------
# Report: Tokens / cost
# ---------------------------------------------------------------------------

def report_tokens(events: list[dict]) -> None:
    sections = _events_by_type(events, "llm_section")
    if not sections:
        print("No llm_section events found. Process some episodes first.")
        return

    by_feed: dict[str, list[dict]] = defaultdict(list)
    for ev in sections:
        by_feed[ev.get("feed_slug", "unknown")].append(ev)

    print("\nLLM Token & Cost Breakdown (per feed)")
    print("─" * 78)
    print(f"{'Feed':<30} {'Sections':>8} {'Prompt tok':>11} {'Compl tok':>10} {'Cost USD':>10}")
    print("─" * 78)

    total_cost = 0.0
    total_prompt = 0
    total_compl = 0
    for slug in sorted(by_feed):
        secs = by_feed[slug]
        prompt = sum(s.get("prompt_tokens", 0) for s in secs)
        compl = sum(s.get("completion_tokens", 0) for s in secs)
        cost = sum(s.get("cost_usd", 0.0) for s in secs)
        total_prompt += prompt
        total_compl += compl
        total_cost += cost
        print(f"{slug:<30} {len(secs):>8} {prompt:>11,} {compl:>10,} {cost:>10.4f}")

    print("─" * 78)
    print(f"{'TOTAL':<30} {len(sections):>8} {total_prompt:>11,} {total_compl:>10,} {total_cost:>10.4f}")

    # Per-day breakdown
    by_day: dict[str, list[dict]] = defaultdict(list)
    for ev in sections:
        by_day[ev["ts"][:10]].append(ev)

    if len(by_day) > 1:
        print("\nDaily Token Spend")
        print("─" * 50)
        print(f"{'Date':<12} {'Sections':>8} {'Tokens':>10} {'Cost USD':>10}")
        print("─" * 50)
        for day in sorted(by_day):
            secs = by_day[day]
            tok = sum(s.get("prompt_tokens", 0) + s.get("completion_tokens", 0) for s in secs)
            cost = sum(s.get("cost_usd", 0.0) for s in secs)
            print(f"{day:<12} {len(secs):>8} {tok:>10,} {cost:>10.4f}")


# ---------------------------------------------------------------------------
# Report: Transcription time
# ---------------------------------------------------------------------------

def report_transcription(events: list[dict]) -> None:
    ends = _events_by_type(events, "transcription_end")
    if not ends:
        print("No transcription_end events found. Process some episodes first.")
        return

    by_feed: dict[str, list[dict]] = defaultdict(list)
    for ev in ends:
        by_feed[ev.get("feed_slug", "unknown")].append(ev)

    print("\nWhisper Transcription Time")
    print("─" * 62)
    print(f"{'Feed':<30} {'Episodes':>8} {'Total time':>12} {'Avg/ep':>10}")
    print("─" * 62)

    total_time = 0.0
    for slug in sorted(by_feed):
        eps = by_feed[slug]
        durations = [e.get("duration_s", 0) for e in eps]
        total = sum(durations)
        total_time += total
        print(f"{slug:<30} {len(eps):>8} {_fmt_duration(total):>12} {_fmt_duration(_mean(durations)):>10}")

    print("─" * 62)
    print(f"{'TOTAL':<30} {len(ends):>8} {_fmt_duration(total_time):>12}")

    # Show model breakdown if mixed
    by_model: dict[str, float] = defaultdict(float)
    for ev in ends:
        by_model[ev.get("model", "unknown")] += ev.get("duration_s", 0)
    if len(by_model) > 1:
        print("\nBy model:", ", ".join(f"{m}: {_fmt_duration(t)}" for m, t in sorted(by_model.items())))


# ---------------------------------------------------------------------------
# Report: Machine utilization + CPU temp correlation
# ---------------------------------------------------------------------------

def report_utilization(events: list[dict], monitor_log: str) -> None:
    starts = {e["episode_name"]: e["_ts"] for e in _events_by_type(events, "episode_start")}
    completes = {e["episode_name"]: e["_ts"] for e in _events_by_type(events, "episode_complete")}
    xscr_starts = {e["episode_name"]: e["_ts"] for e in _events_by_type(events, "transcription_start")}
    xscr_ends = {e["episode_name"]: e["_ts"] for e in _events_by_type(events, "transcription_end")}

    # Build list of pipeline windows: (start_ts, end_ts, kind)
    pipeline_windows: list[tuple[datetime, datetime, str]] = []
    for ep, t0 in starts.items():
        t1 = completes.get(ep)
        if t1:
            pipeline_windows.append((t0, t1, "episode"))
    xscr_windows: list[tuple[datetime, datetime]] = []
    for ep, t0 in xscr_starts.items():
        t1 = xscr_ends.get(ep)
        if t1:
            xscr_windows.append((t0, t1))

    if not pipeline_windows:
        print("No complete episode windows found yet.")
        return

    first_event = min(e["_ts"] for e in events)
    last_event = max(e["_ts"] for e in events)
    total_span_s = (last_event - first_event).total_seconds()
    pipeline_active_s = sum((t1 - t0).total_seconds() for t0, t1, _ in pipeline_windows)
    xscr_active_s = sum((t1 - t0).total_seconds() for t0, t1 in xscr_windows)

    print("\nMachine Utilization")
    print("─" * 50)
    print(f"  Tracking period : {_fmt_duration(total_span_s)} ({first_event.strftime('%Y-%m-%d')} → {last_event.strftime('%Y-%m-%d')})")
    print(f"  Episodes processed : {len(pipeline_windows)}")
    pct = pipeline_active_s / total_span_s * 100 if total_span_s else 0
    print(f"  Pipeline active : {_fmt_duration(pipeline_active_s)} ({pct:.1f}% of tracked period)")
    xpct = xscr_active_s / total_span_s * 100 if total_span_s else 0
    print(f"  Whisper active  : {_fmt_duration(xscr_active_s)} ({xpct:.1f}% of tracked period)")

    # CPU temp correlation
    if not Path(monitor_log).exists():
        print(f"\n  (CPU temp correlation skipped — {monitor_log} not found)")
        return

    monitor_entries = load_monitor_log(monitor_log)
    if not monitor_entries:
        print("\n  (CPU temp correlation skipped — monitor log is empty)")
        return

    transcription_temps: list[float] = []
    idle_temps: list[float] = []

    for ts, cpu in monitor_entries:
        in_xscr = any(t0 <= ts <= t1 for t0, t1 in xscr_windows)
        if in_xscr:
            transcription_temps.append(cpu)
        else:
            idle_temps.append(cpu)

    print("\nCPU Temperature")
    print("─" * 50)
    if transcription_temps:
        print(f"  During Whisper transcription : {_mean(transcription_temps):.1f}°C avg  (n={len(transcription_temps)})")
    if idle_temps:
        print(f"  During idle / other          : {_mean(idle_temps):.1f}°C avg  (n={len(idle_temps)})")


# ---------------------------------------------------------------------------
# Report: Per-day per-feed breakdown
# ---------------------------------------------------------------------------

def report_breakdown(events: list[dict], date_str: str) -> None:
    day_events = _filter_date(events, date_str)
    if not day_events:
        print(f"No events found for {date_str}.")
        return

    xscr_ends = {e["episode_name"]: e for e in _events_by_type(day_events, "transcription_end")}
    llm_secs = _events_by_type(day_events, "llm_section")
    completes = _events_by_type(day_events, "episode_complete")

    feeds = sorted({e.get("feed_slug", "unknown") for e in day_events})

    print(f"\nBreakdown for {date_str}")
    print("─" * 85)
    print(f"{'Feed':<30} {'Whisper':>9} {'LLM time':>9} {'Tokens':>9} {'Cost':>8} {'Ads removed':>12}")
    print("─" * 85)

    for slug in feeds:
        xscr_time = sum(e.get("duration_s", 0) for e in xscr_ends.values() if e.get("feed_slug") == slug)
        llm_time = sum(e.get("duration_s", 0) for e in llm_secs if e.get("feed_slug") == slug)
        tokens = sum(e.get("prompt_tokens", 0) + e.get("completion_tokens", 0)
                     for e in llm_secs if e.get("feed_slug") == slug)
        cost = sum(e.get("cost_usd", 0) for e in llm_secs if e.get("feed_slug") == slug)
        ads_removed = sum(e.get("ads_removed_s", 0) for e in completes if e.get("feed_slug") == slug)
        print(f"{slug:<30} {_fmt_duration(xscr_time):>9} {_fmt_duration(llm_time):>9} {tokens:>9,} {cost:>8.4f} {_fmt_duration(ads_removed):>12}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    from dotenv import load_dotenv
    load_dotenv()

    parser = argparse.ArgumentParser(description="Skipcastify pipeline metrics")
    parser.add_argument("command", nargs="?", default="all",
                        choices=["all", "feeds", "tokens", "transcription", "utilization", "breakdown"])
    parser.add_argument("--date", help="Date for 'breakdown' command (YYYY-MM-DD)")
    parser.add_argument("--monitor-log", default="/home/bwebb/monitor.log",
                        help="Path to monitor.log (default: /home/bwebb/monitor.log)")
    args = parser.parse_args()

    data_dir = os.environ.get("DATA_DIR", "data")
    events = load_events(data_dir)

    if not events and args.command != "utilization":
        print(f"No events found in {data_dir}/metrics/events.jsonl")
        print("Run the pipeline (with ENABLE_PROCESSING=true) to start collecting metrics.")
        sys.exit(0)

    cmd = args.command
    if cmd in ("all", "feeds"):
        report_feeds(events)
    if cmd in ("all", "tokens"):
        report_tokens(events)
    if cmd in ("all", "transcription"):
        report_transcription(events)
    if cmd in ("all", "utilization"):
        report_utilization(events, args.monitor_log)
    if cmd == "breakdown":
        date_str = args.date or datetime.now().strftime("%Y-%m-%d")
        report_breakdown(events, date_str)


if __name__ == "__main__":
    main()
