"""One show, as every client presents it: identity, status, stats, cut policy, where its
ads fall and its recent episodes. Built from the same functions as the dashboard and
settings, so the numbers match. Show notes stay at /api/feed/<slug>/meta.json: they come
from the show's own feed, which can be slow, and shouldn't hold this up.
"""

from skipcastify.services import analytics, cut_policy, settings, subscriptions
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
        "signature": analytics._with_titles(data_dir, signature),
        "recent_episodes": analytics._with_titles(data_dir, episodes),
    }
