#!/usr/bin/env python3
"""Eval ad detection on every reviewed /label episode. Makes paid LLM calls.

Runs the pipeline's detection (section LLM, then span post-processing) on each
episode's cached transcript and scores the cuts against your labels, in
seconds missed and seconds wrongly cut. Repeats each episode --runs times,
because the model is not deterministic. Pass --prompt more than once to
compare prompts; the arms are interleaved round-robin so drift hits all equally.

    uv run python scripts/eval_ads.py --runs 3
    uv run python scripts/eval_ads.py --prompt old.txt --prompt new.txt --runs 4
    uv run python scripts/eval_ads.py --episode odd-lots --runs 1

Results go to data/evals/<timestamp>/: results.jsonl (one row per call, with
spans), summary.md, and metrics/events.jsonl (LLM cost; kept off the dashboard).
"""

import argparse
import json
import os
import statistics
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

from skipcastify.services import labeling, span_postprocess  # noqa: E402
from skipcastify.services.ad_eval import score  # noqa: E402
from skipcastify.services.audio_processor import DEFAULT_PROMPT_PATH, AudioProcessor, Segment  # noqa: E402


def reviewed_episodes(data_dir: Path, filters: list[str]) -> list[str]:
    names = []
    for path in sorted((data_dir / "labels" / "ground_truth").glob("*_labels.json")):
        name = path.name[: -len("_labels.json")]
        doc = json.loads(path.read_text())
        if doc.get("version") == labeling.FORMAT_VERSION and doc.get("reviewed") \
                and labeling.valid_episode(data_dir, name) \
                and (not filters or any(f in name for f in filters)):
            names.append(name)
    return names


def load(data_dir: Path, name: str):
    segs = json.loads((data_dir / "transcripts" / f"{name}_transcript.json").read_text())
    labels = json.loads(labeling.label_path(data_dir, name).read_text())["spans"]
    return [Segment(start=s["start"], end=s["end"], text=s["text"]) for s in segs], labels


def detect(segments, name: str, prompt: str) -> list[dict]:
    with tempfile.TemporaryDirectory() as tmp:
        spans = AudioProcessor(tmp)._run_section_llm(segments, tmp, episode_name=f"eval-{name}", prompt_path=prompt)
    return span_postprocess.postprocess_spans(spans, segments) if spans else []


def summarize(rows: list[dict], arms: list[str], episodes: list[str], cost: float) -> str:
    sec = lambda ms: f"{ms / 1000:.1f}"  # noqa: E731
    out = [f"# Ad-detection eval, {len(episodes)} reviewed episodes, LLM cost ${cost:.2f}", ""]
    out += ["| prompt | runs | missed s/run | wrongly cut s/run | recall | precision | errors |", "|---|---|---|---|---|---|---|"]
    for arm in arms:
        ok = [r for r in rows if r["arm"] == arm and "error" not in r]
        runs = sorted({r["round"] for r in ok})
        per = lambda k: statistics.mean(sum(r[k] for r in ok if r["round"] == n) for n in runs) if runs else 0  # noqa: E731
        truth, cut, missed, wrong = per("truth_ms"), per("cut_ms"), per("missed_ms"), per("wrong_ms")
        recall = f"{(truth - missed) / truth:.1%}" if truth else "-"
        precision = f"{(cut - wrong) / cut:.1%}" if cut else "-"
        errors = sum(1 for r in rows if r["arm"] == arm and "error" in r)
        out.append(f"| {Path(arm).name} | {len(runs)} | {sec(missed)} | {sec(wrong)} | {recall} | {precision} | {errors} |")
    out += ["", "Per episode, mean (min–max) seconds over runs:", ""]
    out += ["| episode | prompt | labeled to cut | missed | wrongly cut |", "|---|---|---|---|---|"]
    for ep in episodes:
        for arm in arms:
            ok = [r for r in rows if r["arm"] == arm and r["episode"] == ep and "error" not in r]
            if not ok:
                continue
            col = lambda k: f"{sec(statistics.mean(r[k] for r in ok))} ({sec(min(r[k] for r in ok))}–{sec(max(r[k] for r in ok))})"  # noqa: E731
            out.append(f"| {ep} | {Path(arm).name} | {sec(ok[0]['truth_ms'])} | {col('missed_ms')} | {col('wrong_ms')} |")
    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    load_dotenv()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data-dir", default=os.environ.get("DATA_DIR", "data"))
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--prompt", action="append", help="prompt file; repeat to compare (default: production prompt)")
    ap.add_argument("--episode", action="append", default=[], help="substring filter; repeatable")
    ap.add_argument("--out", help="output dir (default: <data-dir>/evals/<timestamp>)")
    args = ap.parse_args(argv)

    data_dir = Path(args.data_dir).resolve()
    arms = [str(Path(p).resolve()) for p in (args.prompt or [os.path.normpath(DEFAULT_PROMPT_PATH)])]
    episodes = reviewed_episodes(data_dir, args.episode)
    if not episodes:
        print("No reviewed episodes match.", file=sys.stderr)
        return 1
    out = Path(args.out) if args.out else data_dir / "evals" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    os.environ["DATA_DIR"] = str(out)  # LLM cost events land here, not on the dashboard
    data = {ep: load(data_dir, ep) for ep in episodes}

    rows = []
    with open(out / "results.jsonl", "w") as f:
        for n in range(args.runs):
            # Round-robin, rotating which arm goes first each round.
            for arm in arms[n % len(arms):] + arms[: n % len(arms)]:
                for ep in episodes:
                    segments, labels = data[ep]
                    row = {"round": n, "arm": arm, "episode": ep, "ts": datetime.now().isoformat(timespec="seconds")}
                    try:
                        spans = detect(segments, ep, arm)
                        row.update(score(segments, labels, spans), spans=spans)
                    except Exception as e:  # one failed call shouldn't sink the run
                        row["error"] = repr(e)
                    rows.append(row)
                    f.write(json.dumps(row) + "\n")
                    f.flush()
                    print(f"[{n + 1}/{args.runs}] {Path(arm).name} {ep}: "
                          + (row["error"] if "error" in row else
                             f"missed {row['missed_ms'] / 1000:.1f}s, wrongly cut {row['wrong_ms'] / 1000:.1f}s"))

    events = out / "metrics" / "events.jsonl"
    cost = sum(json.loads(line).get("cost_usd", 0) for line in events.open()) if events.exists() else 0.0
    summary = summarize(rows, arms, episodes, cost)
    (out / "summary.md").write_text(summary)
    print("\n" + summary + f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
