# Plan: Time-Based Feed Tracking

Replace the per-GUID seen-list **and** the processing baseline with a single
per-feed publish-date watermark.

## Motivation

Today two mechanisms decide what to download and process:

- `data/seen_episodes.json` — a set of episode GUIDs per feed, to avoid
  re-downloading. It grows to thousands of entries per feed and broke when a
  podcast changed hosting platforms (every `<guid>` was rewritten, so the whole
  backlog looked new — see the GUID-migration guard added as a workaround).
- `data/process_baseline.json` — a skip-list of pre-existing backlog episodes so
  that enabling processing on all feeds doesn't grind through history.

Both can be replaced by one timestamp per feed. Empirically all 38 subscribed
feeds have publish dates on 100% of episodes (0 missing across 12,602), so a
time-based decision is viable.

## New mechanism: per-feed watermark

```
data/feed_watermarks.json
{ "<feed_url>": "2026-06-29T13:00:00Z", ... }   # newest pubDate handled
```

The watermark is the publish date of the newest episode we have downloaded for
that feed.

### Download (`download_episode.py`)

1. Parse feed. If the feed URL is **absent** from watermarks → set
   watermark = newest entry's `pubDate`, download nothing. (This reproduces the
   baseline "future episodes only" behavior for free.)
2. Else → `new = [e for e in entries if pubDate(e) > watermark]`, download up to
   `episode_limit`, then advance the watermark to the max `pubDate` downloaded.
3. Keep the existing `target_path.exists()` / `processed_path.exists()` check as
   a belt-and-suspenders dedupe while files are still on disk.

### Processing (`pipeline.py` / `state_manager.py`)

Since only future episodes are ever downloaded, drop the baseline entirely and
process every unprocessed raw file. No skip-list, no `_load_baseline()`.

## What gets removed

- `seen_episodes.json` and all GUID logic (`_entry_id`, the seen set load/save).
- `process_baseline.json` and `StateManager._load_baseline()` / its filter.
- The GUID-migration re-baseline guard in `download_latest()` — keying on
  `pubDate` is immune to GUID churn, so that bug class disappears.

## Cutover steps (one-time)

1. Add watermark read/write helpers.
2. Seed `feed_watermarks.json`: for each feed, watermark = newest current
   episode's `pubDate`.
3. Decide the fate of the 33 "buffer" episodes currently kept-and-skipped by the
   baseline (delete the raws, or let them process once) — the baseline that was
   skipping them is going away.
4. Remove seen-list + baseline + migration guard.
5. Update tests (see below).

## Edge cases & safety nets

- **Missing `pubDate`** — currently 0/12,602, but add a fallback: skip-with-
  warning, or fall back to the filename-exists check.
- **Back-filled old episode** (published with an old date after newer ones) —
  would be missed (the GUID set would have caught it). Acceptable for forward-
  publishing podcasts; documented tradeoff.
- **Identical timestamps** — use strict `>`; the filesystem check covers any
  same-second duplicate.
- **Timezones** — store and compare watermarks in UTC.

## Testing (all deterministic, mocked feeds — no network)

- New feed → seeds watermark, downloads nothing.
- `pubDate > watermark` boundary is strict (equal pubDate not re-downloaded).
- Watermark advances to the max downloaded pubDate; does not advance when
  nothing new.
- Missing-`pubDate` fallback path.

## Status

Approved 2026-06-30. Implement when ready to retire the baseline; hold off until
then.
