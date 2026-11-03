"""Backend for the /label GUI: list cached transcripts, load one with its
pipeline cuts, and save hand-labeled spans to data/labels/ground_truth/."""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from skipcastify.services.analytics import load_events

LABELS = ("ad", "self_promo", "uncertain", "intro_outro")
FORMAT_VERSION = 2
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _transcripts_dir(data_dir) -> Path:
    return Path(data_dir) / "transcripts"


def label_path(data_dir, name: str) -> Path:
    return Path(data_dir) / "labels" / "ground_truth" / f"{name}_labels.json"


def valid_episode(data_dir, name: str) -> bool:
    """Only names with a cached transcript; also rules out path traversal."""
    return bool(_NAME_RE.match(name)) and \
        (_transcripts_dir(data_dir) / f"{name}_transcript.json").is_file()


def _latest_completes(data_dir) -> dict:
    out = {}
    for ev in load_events(data_dir):
        if ev.get("event") == "episode_complete" and ev.get("episode_name"):
            out[ev["episode_name"]] = ev
    return out


def _feed_for(data_dir, name: str, completes: dict) -> Optional[str]:
    ev = completes.get(name)
    if ev and ev.get("feed_slug"):
        return ev["feed_slug"]
    raw = Path(data_dir) / "podcasts" / "raw"
    feeds = [d.name for d in raw.iterdir() if d.is_dir()] if raw.is_dir() else []
    matches = [f for f in feeds if name.startswith(f + "-")]
    return max(matches, key=len) if matches else None


def raw_audio_path(data_dir, name: str, completes: Optional[dict] = None) -> Optional[Path]:
    """The untouched download; the processed file's timeline has ads cut out."""
    feed = _feed_for(data_dir, name, completes if completes is not None else _latest_completes(data_dir))
    if not feed:
        return None
    path = Path(data_dir) / "podcasts" / "raw" / feed / f"{name}.mp3"
    return path if path.is_file() else None


def _read_labels(data_dir, name: str) -> Optional[dict]:
    path = label_path(data_dir, name)
    if not path.is_file():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def list_episodes(data_dir) -> list[dict]:
    completes = _latest_completes(data_dir)
    out = []
    for path in _transcripts_dir(data_dir).glob("*_transcript.json"):
        name = path.name[: -len("_transcript.json")]
        ev = completes.get(name)
        labels = _read_labels(data_dir, name)
        out.append({
            "name": name,
            "feed": _feed_for(data_dir, name, completes),
            "transcribed": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
            "has_predictions": ev is not None,
            "labeled_spans": len(labels.get("spans", [])) if labels else None,
            "reviewed": bool(labels and labels.get("reviewed")),
        })
    out.sort(key=lambda e: e["transcribed"], reverse=True)
    return out


def load_episode(data_dir, name: str) -> dict:
    with open(_transcripts_dir(data_dir) / f"{name}_transcript.json", encoding="utf-8") as f:
        segments = [{"start": s["start"], "end": s["end"], "text": s["text"]} for s in json.load(f)]
    completes = _latest_completes(data_dir)
    ev = completes.get(name)
    labels = _read_labels(data_dir, name) or {}
    return {
        "name": name,
        "segments": segments,
        "predictions": [{"start_ms": s["start_ms"], "end_ms": s["end_ms"], "label": s.get("label", "")}
                        for s in (ev or {}).get("spans", [])],
        "predicted_at": (ev or {}).get("ts"),
        "spans": labels.get("spans", []),
        "reviewed": bool(labels.get("reviewed")),
        "has_audio": raw_audio_path(data_dir, name, completes) is not None,
    }


def clean_spans(spans) -> list[dict]:
    """Validate client spans; sorted, integer ms. Raises ValueError."""
    if not isinstance(spans, list):
        raise ValueError("spans must be a list")
    out = []
    for s in spans:
        try:
            start, end, label = int(s["start_ms"]), int(s["end_ms"]), s["label"]
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"bad span: {s!r}") from None
        if label not in LABELS or start < 0 or end <= start:
            raise ValueError(f"bad span: {s!r}")
        out.append({"start_ms": start, "end_ms": end, "label": label})
    return sorted(out, key=lambda s: s["start_ms"])


def save_labels(data_dir, name: str, spans, reviewed: bool) -> dict:
    doc = {
        "episode_name": name,
        "version": FORMAT_VERSION,
        "reviewed": bool(reviewed),
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "spans": clean_spans(spans),
    }
    path = label_path(data_dir, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)
    return doc
