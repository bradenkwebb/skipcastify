"""Per-feed cut policy. Labels say what a span is; the policy says which labels
get cut, so changing your mind about a show is a config edit, not a prompt edit.

policy.yaml at the repo root (or $CUT_POLICY), optional:

    default:
      cut: [ad, self_promo]
    feeds:
      mormon-stories-podcast:
        cut: [ad]          # keep the show's own asks
"""

import os
from pathlib import Path
from typing import Iterable

import yaml

LABELS = ("ad", "self_promo", "intro_outro")
DEFAULT_CUT = frozenset({"ad", "self_promo"})

# LLM output label -> span label. Today's prompt doesn't separate self-promo, so all of these are ads.
LLM_LABELS = {"ADVERTISEMENT": "ad", "SPONSOR": "ad", "PROMOTION": "ad", "OTHER_AD": "ad",
              "SELF_PROMO": "self_promo"}

_cache: dict = {}


def policy_path() -> Path:
    return Path(os.environ.get("CUT_POLICY") or Path(__file__).resolve().parents[2] / "policy.yaml")


def _labels(value, where: str) -> frozenset:
    if not isinstance(value, list) or not all(v in LABELS for v in value):
        raise ValueError(f"{where}: cut must be a list drawn from {LABELS}, got {value!r}")
    return frozenset(value)


def load(path: Path | None = None) -> dict:
    """{"default": frozenset, "feeds": {slug: frozenset}}; cached until the file changes."""
    path = path or policy_path()
    mtime = path.stat().st_mtime if path.is_file() else None
    if _cache.get("key") != (str(path), mtime):
        doc = (yaml.safe_load(path.read_text()) or {}) if mtime is not None else {}
        default = _labels(doc["default"]["cut"], "default") if "cut" in (doc.get("default") or {}) else DEFAULT_CUT
        feeds = {slug: _labels(cfg.get("cut"), f"feeds.{slug}")
                 for slug, cfg in (doc.get("feeds") or {}).items() if cfg and "cut" in cfg}
        _cache.update(key=(str(path), mtime), policy={"default": default, "feeds": feeds})
    return _cache["policy"]


def cut_labels(feed: str | None, path: Path | None = None) -> frozenset:
    p = load(path)
    return p["feeds"].get(feed or "", p["default"])


def span_label(span: dict) -> str:
    """Normalize a predicted span's label to LABELS; unknown LLM labels count as ads."""
    label = span.get("label", "")
    return label if label in LABELS else LLM_LABELS.get(label, "ad")


def split(spans: Iterable[dict], feed: str | None, path: Path | None = None) -> tuple[list[dict], list[dict]]:
    """(cut, kept) for this feed's policy."""
    labels = cut_labels(feed, path)
    cut, kept = [], []
    for s in spans:
        (cut if span_label(s) in labels else kept).append(s)
    return cut, kept
