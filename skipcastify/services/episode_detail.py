"""Everything the dashboard knows about one episode: its cuts and why, its
processing runs, and how the latest cuts score against hand labels."""

import json
from pathlib import Path
from typing import Optional

from skipcastify.services import labeling
from skipcastify.services.ad_eval import score
from skipcastify.services.analytics import load_events
from skipcastify.services.llm_utils import parse_json_array_from_text


def _runs(events: list[dict]) -> list[dict]:
    """One row per processing attempt (episode_start onward), newest first."""
    runs: list[dict] = []
    for ev in events:
        kind = ev.get("event")
        if kind == "episode_start" or not runs:
            runs.append({"started": ev.get("ts"), "completed": None, "llm_calls": 0, "cost_usd": 0.0,
                         "prompt_tokens": 0, "transcribe_s": None, "whisper_model": None})
        run = runs[-1]
        if kind in ("llm_section", "llm_refinement"):
            run["llm_calls"] += 1
            run["cost_usd"] += ev.get("cost_usd") or 0
            run["prompt_tokens"] += ev.get("prompt_tokens") or 0
        elif kind == "transcription_end":
            run.update(transcribe_s=ev.get("duration_s"), whisper_model=ev.get("model"))
        elif kind == "episode_complete":
            run.update(completed=ev.get("ts"), ads_removed_s=ev.get("ads_removed_s"),
                       span_count=ev.get("span_count"), spans=ev.get("spans", []),
                       original_duration_s=ev.get("original_duration_s"),
                       processed_duration_s=ev.get("processed_duration_s"), ads_pct=ev.get("ads_pct"))
    for run in runs:
        run["cost_usd"] = round(run["cost_usd"], 6)
    return runs[::-1]


def _llm_items(data_dir, feed: Optional[str], name: str) -> list[dict]:
    """Spans with rationales from the latest run's saved section responses, if still on disk."""
    if not feed:
        return []
    items = []
    for path in sorted((Path(data_dir) / "podcasts" / "raw" / feed / "transcripts").glob(f"{name}_section_*_response.txt")):
        text = path.read_text(encoding="utf-8", errors="replace")
        body = text.split("=" * 100)
        try:
            parsed = parse_json_array_from_text(body[1] if len(body) > 2 else text)
        except Exception:
            continue
        items += [i for i in parsed if isinstance(i, dict) and "start_ms" in i and "end_ms" in i]
    return items


def _segments(data_dir, name: str) -> list[dict]:
    path = Path(data_dir) / "transcripts" / f"{name}_transcript.json"
    if not path.is_file():
        return []
    return [{"start": s["start"], "end": s["end"], "text": s["text"]} for s in json.loads(path.read_text())]


def episode_detail(data_dir, name: str) -> Optional[dict]:
    if not labeling.valid_name(name):
        return None
    events = [e for e in load_events(data_dir) if e.get("episode_name") == name]
    segments = _segments(data_dir, name)
    if not events and not segments:
        return None
    runs = _runs(events)
    latest = next((r for r in runs if r["completed"]), None)
    feed = next((e["feed_slug"] for e in reversed(events) if e.get("feed_slug")), None) \
        or labeling._feed_for(data_dir, name, {})
    items = _llm_items(data_dir, feed, name)

    cuts = []
    for sp in (latest or {}).get("spans", []):
        s, e = sp["start_ms"], sp["end_ms"]
        lines = [g for g in segments if g["start"] < e and g["end"] > s]
        cuts.append({
            "start_ms": s, "end_ms": e, "label": sp.get("label", ""),
            "rationales": [i.get("rationale", "") for i in items
                           if int(i["start_ms"]) < e and int(i["end_ms"]) > s and i.get("rationale")],
            "first_line": lines[0]["text"].strip() if lines else "",
            "last_line": lines[-1]["text"].strip() if len(lines) > 1 else "",
            "lines": [{"start_ms": g["start"], "text": g["text"].strip()} for g in lines],
        })

    labels = labeling._read_labels(data_dir, name)
    scored = None
    if labels and labels.get("reviewed") and segments and latest:
        scored = score(segments, labels.get("spans", []), latest.get("spans", []))

    return {
        "name": name,
        "feed": feed,
        "title": labeling.episode_titles(data_dir).get(name),
        "released": labeling.release_dates(data_dir).get(name),
        "latest": {k: v for k, v in latest.items() if k != "spans"} if latest else None,
        "runs": [{k: v for k, v in r.items() if k != "spans"} for r in runs],
        "cuts": cuts,
        "labels": {"reviewed": bool(labels.get("reviewed")), "spans": labels.get("spans", [])} if labels else None,
        "score": scored,
        "has_transcript": bool(segments),
        "has_audio": labeling.raw_audio_path(data_dir, name) is not None,
    }
