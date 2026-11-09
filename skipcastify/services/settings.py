"""Dashboard editing of policy.yaml and the subscription list, with an
append-only change log for undo.

Each log entry holds the full policy before and after, so undo is just saving
an entry's "before" as a new change. Nothing is ever rewritten or erased.
"""

import fcntl
import hashlib
import json
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import yaml

from skipcastify.services import cut_policy, subscriptions
from skipcastify.services.labeling import valid_name
from skipcastify.services.published_feeds import _channel_title
from skipcastify.utils.utils import slugify

HEADER = ("# Edited from the dashboard (/settings); change log in data/settings/changes.jsonl.\n"
          "# Labels: ad, self_promo, intro_outro. Hand edits still work.\n")


class Conflict(Exception):
    """policy.yaml changed since the editor loaded it."""


def _dir(data_dir) -> Path:
    d = Path(data_dir) / "settings"
    d.mkdir(parents=True, exist_ok=True)
    return d


@contextmanager
def _locked(data_dir):
    with open(_dir(data_dir) / ".lock", "w") as f:  # serializes gunicorn workers
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def version(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16] if path.is_file() else "none"


def _ordered(labels) -> list[str]:
    return [label for label in cut_policy.LABELS if label in labels]


def snapshot(path: Path) -> dict:
    p = cut_policy.load(path)
    return {"default": _ordered(p["default"]),
            "feeds": {slug: _ordered(v) for slug, v in sorted(p["feeds"].items())}}


def validate(new: dict) -> dict:
    """Normalized policy, or ValueError. A feed set to None uses the default."""
    if not isinstance(new, dict):
        raise ValueError("policy must be an object")
    default = cut_policy._labels(new.get("default"), "default")
    feeds = {}
    for slug, labels in sorted((new.get("feeds") or {}).items()):
        if not valid_name(slug):
            raise ValueError(f"bad feed slug {slug!r}")
        if labels is not None:
            feeds[slug] = _ordered(cut_policy._labels(labels, f"feeds.{slug}"))
    return {"default": _ordered(default), "feeds": feeds}


def summarize(before: dict, after: dict) -> list[str]:
    def show(v):
        return "default" if v is None else (", ".join(v) or "nothing")
    lines = []
    if before["default"] != after["default"]:
        lines.append(f"default: {show(before['default'])} → {show(after['default'])}")
    for slug in sorted(set(before["feeds"]) | set(after["feeds"])):
        b, a = before["feeds"].get(slug), after["feeds"].get(slug)
        if b != a:
            lines.append(f"{slug}: {show(b)} → {show(a)}")
    return lines


def _write(path: Path, policy: dict) -> None:
    doc = {"default": {"cut": policy["default"]},
           "feeds": {slug: {"cut": v} for slug, v in policy["feeds"].items()}}
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(HEADER + yaml.safe_dump(doc, sort_keys=False, default_flow_style=None))
    tmp.replace(path)


def changes(data_dir, n: int = 50) -> list[dict]:
    """Newest first."""
    path = _dir(data_dir) / "changes.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out[::-1][:n]


def save(data_dir, new: dict, expected: str, user: str | None,
         undo_of: int | None = None, path: Path | None = None) -> dict | None:
    """Write the policy and log the change; None if nothing changed."""
    path = path or cut_policy.policy_path()
    with _locked(data_dir):
        if version(path) != expected:
            raise Conflict("policy.yaml changed since this page loaded; reload to see it")
        before, after = snapshot(path), validate(new)
        if before == after:
            return None
        _write(path, after)
        return _log(data_dir, "policy", user, before, after, summarize(before, after), undo_of)


def _log(data_dir, kind, user, before, after, summary, undo_of) -> dict:
    entry = {"id": time.time_ns() // 1000, "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
             "kind": kind, "user": user, "before": before, "after": after,
             "summary": summary, "undo_of": undo_of}
    with open(_dir(data_dir) / "changes.jsonl", "a") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def save_subscriptions(data_dir, change, expected: str, user: str | None,
                       undo_of: int | None = None, path: Path | None = None) -> dict | None:
    """change(list) -> (new list, summary); applied under the lock, then logged."""
    path = path or subscriptions.opml_path()
    with _locked(data_dir):
        if version(path) != expected:
            raise Conflict("the subscription list changed since this page loaded; reload to see it")
        before = subscriptions.read(path)
        after, summary = change(before)
        if after == before:
            return None
        subscriptions.write(path, after)
        return _log(data_dir, "subscriptions", user, before, after, summary, undo_of)


def add_subscription(data_dir, url: str, title: str, expected: str, user: str | None, **kw) -> dict | None:
    def change(subs):
        if any(s["url"] == url for s in subs):
            raise ValueError("already subscribed")
        return subs + [{"title": title, "url": url}], [f"subscribed: {title}"]
    return save_subscriptions(data_dir, change, expected, user, **kw)


def remove_subscription(data_dir, url: str, expected: str, user: str | None, **kw) -> dict | None:
    def change(subs):
        gone = [s for s in subs if s["url"] == url]
        if not gone:
            raise KeyError(url)
        return [s for s in subs if s["url"] != url], [f"unsubscribed: {gone[0]['title']}"]
    return save_subscriptions(data_dir, change, expected, user, **kw)


def undo(data_dir, change_id: int, versions: dict, user: str | None,
         policy_path: Path | None = None, subs_path: Path | None = None) -> dict | None:
    """Re-apply an entry's "before" as a new change. versions: {kind: version}."""
    entry = next((c for c in changes(data_dir, n=10**6) if c.get("id") == change_id), None)
    if entry is None:
        raise KeyError(change_id)
    kind = entry.get("kind", "policy")
    if kind == "subscriptions":
        before = entry["before"]
        return save_subscriptions(
            data_dir, lambda _subs: (before, [f"undo: {line}" for line in entry["summary"]]),
            versions.get("subscriptions"), user, undo_of=change_id, path=subs_path)
    return save(data_dir, entry["before"], versions.get("policy"), user, undo_of=change_id, path=policy_path)


def feed_slug(data_dir, sub: dict) -> str:
    """The slug the downloader uses: cached from its last fetch, else from our title."""
    try:
        cache = json.loads((Path(data_dir) / "feed_cache.json").read_text())
    except (OSError, ValueError):
        cache = {}
    return (cache.get(sub["url"]) or {}).get("slug") or slugify(sub["title"])


def feeds(data_dir, policy: dict, subs: list[dict] = ()) -> list[dict]:
    """Feeds to show: subscribed, published to R2, or named by the policy."""
    r2 = Path(data_dir) / "feeds_r2"
    titles = {feed_slug(data_dir, s): s["title"] for s in subs}
    slugs = {p.stem for p in r2.glob("*.xml")} | set(policy["feeds"]) | set(titles)
    rows = [{"slug": s, "title": _channel_title(str(r2 / f"{s}.xml")) or titles.get(s) or s} for s in slugs]
    return sorted(rows, key=lambda r: r["title"].casefold())
