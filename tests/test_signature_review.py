"""Ad signatures mark hand-reviewed episodes and compare cuts with their labels."""

import json

from skipcastify.services import analytics

DUR_MS = 100_000


def test_overlay_splits_hits_misses_and_extras():
    cut = [(10_000, 20_000), (50_000, 60_000)]
    truth = [(10_000, 20_000), (70_000, 80_000), (55_000, 60_000)]
    r = analytics.review_overlay(cut, truth, DUR_MS)
    assert r["hit"] == [{"start": 0.1, "end": 0.2}, {"start": 0.55, "end": 0.6}]
    assert r["missed"] == [{"start": 0.7, "end": 0.8}]
    assert r["extra"] == [{"start": 0.5, "end": 0.55}]


def test_boundary_slivers_are_not_shown_as_misses_or_extras():
    r = analytics.review_overlay([(10_000, 21_000)], [(10_500, 20_000)], DUR_MS)
    assert r["missed"] == [] and r["extra"] == []
    assert r["hit"] == [{"start": 0.105, "end": 0.2}]


def write_label(data_dir, name, spans, reviewed):
    d = data_dir / "labels" / "ground_truth"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}_labels.json").write_text(json.dumps(
        {"episode_name": name, "version": 2, "reviewed": reviewed, "spans": spans}))


def complete(name, spans):
    return {"event": "episode_complete", "ts": "2026-11-09T10:00:00", "feed_slug": "show",
            "episode_name": name, "original_duration_s": DUR_MS / 1000,
            "spans": [{"start_ms": a, "end_ms": b, "label": "ADVERTISEMENT"} for a, b in spans]}


def payload_episodes(tmp_path, monkeypatch, events):
    monkeypatch.setenv("CUT_POLICY", str(tmp_path / "policy.yaml"))
    (tmp_path / "metrics").mkdir()
    (tmp_path / "metrics" / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n")
    (feed,) = analytics.build_payload(str(tmp_path))["signatures"]
    return {ep["episode"]: ep for ep in feed["episodes"]}


def test_only_reviewed_episodes_get_an_overlay(tmp_path, monkeypatch):
    write_label(tmp_path, "show-a", [{"start_ms": 10_000, "end_ms": 20_000, "label": "ad"}], reviewed=True)
    write_label(tmp_path, "show-b", [{"start_ms": 10_000, "end_ms": 20_000, "label": "ad"}], reviewed=False)
    eps = payload_episodes(tmp_path, monkeypatch, [complete("show-a", [(10_000, 30_000)]),
                                                  complete("show-b", [(10_000, 30_000)])])
    assert eps["show-a"]["reviewed"] and eps["show-a"]["review"]["extra"] == [{"start": 0.2, "end": 0.3}]
    assert "reviewed" not in eps["show-b"]


def test_truth_follows_the_feeds_cut_policy(tmp_path, monkeypatch):
    """Intro/outro isn't cut by default, so a labeled intro the pipeline kept is no miss."""
    write_label(tmp_path, "show-a", [{"start_ms": 0, "end_ms": 5_000, "label": "intro_outro"},
                                     {"start_ms": 40_000, "end_ms": 50_000, "label": "self_promo"}], reviewed=True)
    eps = payload_episodes(tmp_path, monkeypatch, [complete("show-a", [])])
    assert eps["show-a"]["review"]["missed"] == [{"start": 0.4, "end": 0.5}]


def test_removed_mirrors_the_cutter_runs_including_pauses():
    segs = [{"start": 0, "end": 4_000}, {"start": 5_000, "end": 9_000},
            {"start": 12_000, "end": 15_000}, {"start": 16_000, "end": 20_000}]
    spans = [{"start_ms": 6_000, "end_ms": 13_000}]
    assert analytics._removed(segs, spans) == [(5_000, 15_000)]   # the 9–12s pause is inside the run


def test_signatures_list_newest_first():
    events = [complete("show-old", []), {**complete("show-new", []), "ts": "2026-11-10T10:00:00"}]
    (feed,) = analytics.ad_signatures(events)
    assert [ep["episode"] for ep in feed["episodes"]] == ["show-new", "show-old"]
