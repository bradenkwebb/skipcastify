import json

from scripts import eval_ads
from skipcastify.services import labeling
from skipcastify.services.ad_eval import score

SEGS = [{"start": 0, "end": 4000, "text": "Brought to you by Acme."},
        {"start": 4000, "end": 9000, "text": "Use code SKIP."},
        {"start": 12000, "end": 20000, "text": "Welcome back."}]  # 9-12s is silence


def test_score_counts_spoken_time_and_whole_lines():
    labels = [{"start_ms": 0, "end_ms": 12000, "label": "ad"},
              {"start_ms": 12000, "end_ms": 13000, "label": "intro_outro"}]  # not a cut label
    # Touching 4.5-5s cuts the whole 4-9s line; the 9-12s gap is not spoken, so not missed.
    r = score(SEGS, labels, [{"start_ms": 4500, "end_ms": 5000}])
    assert r == {"spoken_ms": 17000, "truth_ms": 9000, "cut_ms": 5000, "missed_ms": 4000, "wrong_ms": 0}


def test_score_wrong_cut_and_self_promo():
    labels = [{"start_ms": 0, "end_ms": 4000, "label": "self_promo"}]
    r = score(SEGS, labels, [{"start_ms": 0, "end_ms": 100}, {"start_ms": 15000, "end_ms": 16000}])
    assert (r["missed_ms"], r["wrong_ms"]) == (0, 8000)


def test_eval_runs_reviewed_episodes_and_interleaves_arms(tmp_path, monkeypatch):
    (tmp_path / "transcripts").mkdir()
    for ep in ("show-a", "show-b"):
        (tmp_path / "transcripts" / f"{ep}_transcript.json").write_text(json.dumps(SEGS))
    labeling.save_labels(tmp_path, "show-a", [{"start_ms": 0, "end_ms": 9000, "label": "ad"}], reviewed=True)
    labeling.save_labels(tmp_path, "show-b", [], reviewed=False)  # unreviewed: skipped
    p1, p2 = tmp_path / "p1.txt", tmp_path / "p2.txt"
    p1.write_text("x"), p2.write_text("y")

    calls = []

    def fake(self, segments, transcripts_dir, episode_name, feed_slug="", prompt_path=None):
        calls.append(prompt_path)
        return [{"start_ms": 0, "end_ms": 4000, "label": "ADVERTISEMENT"}] if prompt_path == str(p1) else []

    monkeypatch.setattr(eval_ads.AudioProcessor, "_run_section_llm", fake)
    out = tmp_path / "out"
    assert eval_ads.main(["--data-dir", str(tmp_path), "--runs", "2", "--prompt", str(p1),
                          "--prompt", str(p2), "--out", str(out)]) == 0
    assert calls == [str(p1), str(p2), str(p2), str(p1)]  # rotates who goes first
    rows = [json.loads(line) for line in (out / "results.jsonl").open()]
    assert {r["episode"] for r in rows} == {"show-a"}
    assert sorted((r["arm"] == str(p1), r["missed_ms"]) for r in rows) == [(False, 9000), (False, 9000), (True, 5000), (True, 5000)]
    assert "| p1.txt | 2 | 5.0 | 0.0 | 44.4% | 100.0% | 0 |" in (out / "summary.md").read_text()


def test_dated_snapshot_uses_family_price():
    from skipcastify.services.llm_utils import price_for
    assert price_for("gpt-4o-mini-2024-07-18") == price_for("gpt-4o-mini") != price_for("gpt-4o")
    assert price_for("gpt-4o-mini")["input"] > 0 and price_for("unknown")["input"] == 0


def test_summary_splits_tuned_from_held_out(tmp_path):
    rows = [{"round": 0, "arm": "/p/a.txt", "episode": ep, "truth_ms": 10000, "cut_ms": 10000,
             "missed_ms": m, "wrong_ms": 0} for ep, m in (("tuned-ep", 0), ("fresh-ep", 4000))]
    tuned_file = tmp_path / "tuned.txt"
    tuned_file.write_text("# comment\ntuned-ep\n")
    text = eval_ads.summarize(rows, ["/p/a.txt"], ["fresh-ep", "tuned-ep"], 0.0, eval_ads.read_tuned(tuned_file))
    held, tuned = text.split("## Tuned")[0], text.split("## Tuned")[1].split("## Per episode")[0]
    assert "## Held-out: 1 episode" in held and "| a.txt | 1 | 4.0 | 0.0 | 60.0% |" in held
    assert "| a.txt | 1 | 0.0 | 0.0 | 100.0% |" in tuned
    assert "| fresh-ep | held-out |" in text and "| tuned-ep | tuned |" in text


def test_tuned_list_names_are_episode_ids():
    for name in eval_ads.read_tuned(eval_ads.TUNED_FILE):
        assert labeling.valid_name(name) and " " not in name, name
