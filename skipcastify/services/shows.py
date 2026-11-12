"""One show, as every client presents it: identity, status, stats, cut policy, where its
ads fall and its recent episodes. Built from the same functions as the dashboard and
settings, so the numbers match. Show notes stay at /api/feed/<slug>/meta.json: they come
from the show's own feed, which can be slow, and shouldn't hold this up.
"""

from datetime import datetime

from skipcastify.services import (
    analytics,
    cut_policy,
    episode_detail,
    labeling,
    settings,
    subscriptions,
)
from skipcastify.services.published_feeds import published_feeds
from skipcastify.services.run_log import recent_runs


def show_detail(data_dir, slug: str, public_base_url: str) -> dict | None:
    """None when the server knows nothing about the slug."""
    subs = subscriptions.read(subscriptions.opml_path())
    sub = next((s for s in subs if settings.feed_slug(data_dir, s) == slug), None)
    events = analytics.load_events(data_dir)
    summary = next((f for f in analytics.feed_summary(events) if f["feed_slug"] == slug), None)
    published = next((f for f in published_feeds(data_dir, public_base_url) if f.slug == slug), None)
    if sub is None and summary is None and published is None:
        return None

    status = next((r for r in analytics.feed_status(recent_runs(data_dir, 500)) if r["feed_slug"] == slug), {})
    signature = next((g["episodes"] for g in analytics.ad_signatures(events) if g["feed_slug"] == slug), [])
    episodes = [e for e in analytics.recent_episodes(events, n=10_000) if e["feed_slug"] == slug][:25]
    try:
        policy = cut_policy.load()
        policy_view = {"cut": sorted(cut_policy.cut_labels(slug)), "uses_default": slug not in policy["feeds"]}
    except ValueError as e:  # a hand edit broke policy.yaml; /settings shows the same error
        policy_view = {"cut": None, "uses_default": None, "error": str(e)}
    return {
        "slug": slug,
        "title": (sub or {}).get("title") or (published.title if published else None),
        "subscribed": sub is not None,
        "status": {
            "checked": status.get("checked"),
            "last_download": status.get("last_download"),
            "published": published is not None,
            "published_episodes": published.episodes if published else 0,
            "published_bytes": published.audio_bytes if published else 0,
        },
        "stats": {k: v for k, v in summary.items() if k != "feed_slug"} if summary else None,
        "policy": policy_view,
        # The dashboard's signature rows, review overlay included, so both pages match.
        "signature": analytics._with_review(
            data_dir, [{"feed_slug": slug, "episodes": analytics._with_titles(data_dir, signature)}], events)[0]["episodes"],
        "history": _history(data_dir, events, slug),
        "recent_episodes": analytics._with_titles(data_dir, episodes),
    }


def _when(ts: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(analytics._aware(ts)) if ts else None
    except ValueError:
        return None


def _history(data_dir, events: list[dict], slug: str) -> list[dict]:
    """Per processed episode, oldest first: when it was released, when processing started
    and finished, and how long it waited and took. Feeds the show page's charts."""
    by_ep: dict[str, list[dict]] = {}
    for ev in events:
        if ev.get("feed_slug") == slug and ev.get("episode_name"):
            by_ep.setdefault(ev["episode_name"], []).append(ev)
    released = labeling.release_dates(data_dir)
    rows = []
    for name, evs in by_ep.items():
        run = next((r for r in episode_detail._runs(evs) if r["completed"]), None)
        if not run:
            continue
        start, done, out = _when(run["started"]), _when(run["completed"]), _when(released.get(name))
        rows.append({
            "episode": name,
            "released": released.get(name),
            "started": analytics._aware(run["started"]) if run["started"] else None,
            "completed": analytics._aware(run["completed"]),
            "processing_s": round((done - start).total_seconds(), 1) if start and done else None,
            # Time from release to the start of processing: how long the pipeline took to notice.
            "wait_s": round((start - out).total_seconds(), 1) if start and out and start >= out else None,
            "transcribe_s": run.get("transcribe_s"),
            "duration_min": round((run.get("original_duration_s") or 0) / 60, 1),
        })
    return sorted(rows, key=lambda r: r["completed"] or "")
