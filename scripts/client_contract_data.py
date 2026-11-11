"""Write a small, made-up data directory that exercises every JSON endpoint a client
reads, so CI can run the iOS app's decoders against the real server code.

    python scripts/client_contract_data.py OUT_DIR
    # then serve with DATA_DIR=OUT_DIR/data SUBSCRIPTIONS=OUT_DIR/subscriptions.opml
    #   UPSTREAM_OPML=OUT_DIR/upstream.opml CUT_POLICY=OUT_DIR/policy.yaml
    #   PUBLIC_BASE_URL=https://pods.example.invalid PUBLISH_TO_R2=true

Everything is fictional: example.invalid hosts, no secrets. Files are written with the
server's own modules where they exist, so format changes flow through.
"""

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from skipcastify.services import labeling, subscriptions
from skipcastify.services.publish_manifest import PublishManifest
from skipcastify.services.run_log import append_run

SLUG = "demo-show"
SECRET = "contract-secret"
BASE = "https://pods.example.invalid"
EPISODES = [("Sponsors and Signal", True), ("Nothing to Cut Here", False)]


def ep_name(title: str) -> str:
    return f"{SLUG}-" + title.lower().replace(" ", "_")


def main(out: Path) -> None:
    data = out / "data"
    now = datetime.now(UTC)
    lines = [
        {"start": 0, "end": 4000, "text": "This episode is brought to you by Example Mattress."},
        {"start": 4500, "end": 9000, "text": "Use code DEMO for ten percent off."},
        {"start": 9500, "end": 15000, "text": "Welcome back to the show."},
        {"start": 15500, "end": 21000, "text": "Support us on our membership page."},
    ]

    events = []
    for i, (title, has_ads) in enumerate(EPISODES):
        name = ep_name(title)
        ts = (now - timedelta(hours=6 + i)).isoformat()
        spans = [{"start_ms": 0, "end_ms": 9000, "label": "ADVERTISEMENT"}] if has_ads else []
        events += [
            {"event": "episode_start", "ts": ts, "feed_slug": SLUG, "episode_name": name, "raw_duration_s": 21.0},
            {"event": "llm_section", "ts": ts, "feed_slug": SLUG, "episode_name": name,
             "cost_usd": 0.001, "prompt_tokens": 120},
            {"event": "episode_complete", "ts": ts, "feed_slug": SLUG, "episode_name": name,
             "original_duration_s": 21.0, "processed_duration_s": 12.0 if has_ads else 21.0,
             "ads_removed_s": 9.0 if has_ads else 0.0, "ads_pct": 42.9 if has_ads else 0.0,
             "span_count": len(spans), "spans": spans, "kept_spans": []},
        ]
        (data / "transcripts").mkdir(parents=True, exist_ok=True)
        (data / "transcripts" / f"{name}_transcript.json").write_text(json.dumps(lines))
        sections = data / "podcasts" / "raw" / SLUG / "transcripts"
        sections.mkdir(parents=True, exist_ok=True)
        (sections / f"{name}_section_0_response.txt").write_text(
            "Raw LLM Response for section 0 [0ms-600000ms]:\n" + "=" * 100 + "\n"
            + json.dumps([{**s, "rationale": "A sponsor read with a code."} for s in spans])
            + "\n" + "=" * 100 + "\n")
    (data / "metrics").mkdir(parents=True, exist_ok=True)
    (data / "metrics" / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))

    append_run(data, {"started": (now - timedelta(hours=7)).isoformat(), "duration_s": 61.2, "outcome": "ok",
                      "feeds": [{"feed": SLUG, "status": "downloaded", "downloaded": 2}],
                      "processed": [{"feed": SLUG, "episode": ep_name(t)} for t, _ in EPISODES], "published": 1})
    append_run(data, {"started": (now - timedelta(hours=1)).isoformat(), "duration_s": 3.0, "outcome": "failed",
                      "feeds": [], "processed": [], "published": None, "stage": "download", "error": "ConnectionError"})

    labeling.save_labels(data, ep_name(EPISODES[0][0]),
                         [{"start_ms": 0, "end_ms": 9000, "label": "ad"}], reviewed=True)

    # The local feed's <channel><link> is where show notes come from; unreachable on purpose.
    feed_items = "".join(
        f"<item><title>{t}</title><pubDate>{(now - timedelta(days=1)).strftime('%a, %d %b %Y %H:%M:%S +0000')}</pubDate>"
        f'<enclosure url="{BASE}/audio/{SECRET}/{SLUG}/{ep_name(t)}.mp3" length="1000" type="audio/mpeg"/></item>'
        for t, _ in EPISODES)
    for sub in ("feeds", "feeds_r2"):
        (data / sub).mkdir(parents=True, exist_ok=True)
        (data / sub / f"{SLUG}.xml").write_text(
            f'<?xml version="1.0"?><rss version="2.0"><channel><title>Demo Show</title>'
            f"<link>https://feeds.example.invalid/demo.xml</link>{feed_items}</channel></rss>")
    manifest = PublishManifest(str(data))
    manifest.record_put(f"feeds/{SECRET}/{SLUG}.xml", 2048, "feed")
    manifest.record_put(f"audio/{SECRET}/{SLUG}/{ep_name(EPISODES[0][0])}.mp3", 1_000_000, "audio")
    manifest.save()

    subscriptions.write(out / "subscriptions.opml", [{"title": "Demo Show", "url": "https://feeds.example.invalid/demo.xml"}])
    subscriptions.write(out / "upstream.opml", [{"title": "Another Show", "url": "https://feeds.example.invalid/other.xml"}])
    (out / "policy.yaml").write_text("default:\n  cut: [ad, self_promo]\nfeeds:\n  demo-show:\n    cut: [ad]\n")
    print(f"wrote contract data to {out}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
