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

Every reviewed episode is either tuned or held-out, and nobody chooses which:
those listed in tests/llm/tuned_episodes.txt already shaped tuning, and every
other episode is assigned by a hash of its name (a third tuned). Comparing arms
(--prompt twice or more) scores only the tuned set, so a decision can't be made
on held-out episodes by accident; pass --held-out for the final, one-time check.

Results go to data/evals/<timestamp>/: results.jsonl (one row per call, with
spans), summary.md, metrics/events.jsonl (LLM cost) and llm_profiles/, all kept
off the dashboard.
"""

import argparse
import hashlib
import json
import os
import statistics
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

from skipcastify.services import cut_policy, labeling, span_postprocess  # noqa: E402
from skipcastify.services.ad_eval import score  # noqa: E402
from skipcastify.services.audio_processor import (  # noqa: E402
    DEFAULT_PROMPT_PATH,
    AudioProcessor,
    Segment,
)
from skipcastify.services.llm_monitor import get_monitor  # noqa: E402


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


def detect(segments, name: str, prompt: str, feed: str | None = None) -> list[dict]:
    """The spans the pipeline would cut: LLM, then the feed's policy, then post-processing."""
    with tempfile.TemporaryDirectory() as tmp:
        spans = AudioProcessor(tmp)._run_section_llm(segments, tmp, episode_name=f"eval-{name}", prompt_path=prompt)
    spans, _ = cut_policy.split(spans, feed)
    return span_postprocess.postprocess_spans(spans, segments) if spans else []


TUNED_FILE = Path(__file__).resolve().parents[1] / "tests" / "llm" / "tuned_episodes.txt"
# Share of not-yet-listed episodes the hash puts in the tuned set.
TUNED_SHARE = 1 / 3


def hashed_tuned(name: str) -> bool:
    """Fixed per episode name, so its set can never be picked or changed."""
    return int(hashlib.sha256(name.encode()).hexdigest()[:8], 16) < TUNED_SHARE * 16 ** 8


def tuned_set(episodes, listed: set[str]) -> set[str]:
    return {ep for ep in episodes if ep in listed or hashed_tuned(ep)}


def to_score(episodes: list[str], tuned: set[str], n_arms: int, held_out: bool) -> list[str]:
    """A comparison (two or more arms) tunes something, so it sees only the tuned set."""
    return episodes if n_arms < 2 or held_out else [e for e in episodes if e in tuned]


def read_tuned(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    return {line.strip() for line in path.read_text().splitlines() if line.strip() and not line.startswith("#")}


def summarize(rows: list[dict], arms: list[str], episodes: list[str], cost: float, tuned: set[str] = frozenset()) -> str:
    out = [f"# Ad-detection eval, {len(episodes)} reviewed episodes, LLM cost ${cost:.3f}", ""]
    groups = [("Held-out", "the honest number: the prompt never saw these", [e for e in episodes if e not in tuned]),
              ("Tuned", "regression check: the prompt was tuned on these", [e for e in episodes if e in tuned])]
    for name, note, eps in groups:
        if eps:
            out += [f"## {name}: {len(eps)} episode{'s' * (len(eps) != 1)} ({note})", ""]
            out += _arm_table([r for r in rows if r["episode"] in eps], arms) + [""]
    out += ["## Per episode, mean (min–max) seconds over runs", ""]
    out += ["| episode | set | prompt | labeled to cut | missed | wrongly cut |", "|---|---|---|---|---|---|"]
    sec = lambda ms: f"{ms / 1000:.1f}"  # noqa: E731
    for ep in episodes:
        for arm in arms:
            ok = [r for r in rows if r["arm"] == arm and r["episode"] == ep and "error" not in r]
            if not ok:
                continue
            col = lambda k: f"{sec(statistics.mean(r[k] for r in ok))} ({sec(min(r[k] for r in ok))}–{sec(max(r[k] for r in ok))})"  # noqa: E731
            out.append(f"| {ep} | {'tuned' if ep in tuned else 'held-out'} | {Path(arm).name} | {sec(ok[0]['truth_ms'])} | {col('missed_ms')} | {col('wrong_ms')} |")
    return "\n".join(out) + "\n"


def _arm_table(rows: list[dict], arms: list[str]) -> list[str]:
    sec = lambda ms: f"{ms / 1000:.1f}"  # noqa: E731
    out = ["| prompt | runs | missed s/run | wrongly cut s/run | recall | precision | errors |", "|---|---|---|---|---|---|---|"]
    for arm in arms:
        ok = [r for r in rows if r["arm"] == arm and "error" not in r]
        runs = sorted({r["round"] for r in ok})
        per = lambda k: statistics.mean(sum(r[k] for r in ok if r["round"] == n) for n in runs) if runs else 0  # noqa: E731
        truth, cut, missed, wrong = per("truth_ms"), per("cut_ms"), per("missed_ms"), per("wrong_ms")
        recall = f"{(truth - missed) / truth:.1%}" if truth else "-"
        precision = f"{(cut - wrong) / cut:.1%}" if cut else "-"
        errors = sum(1 for r in rows if r["arm"] == arm and "error" in r)
        out.append(f"| {Path(arm).name} | {len(runs)} | {sec(missed)} | {sec(wrong)} | {recall} | {precision} | {errors} |")
    return out


def main(argv=None) -> int:
    load_dotenv()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data-dir", default=os.environ.get("DATA_DIR", "data"))
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--prompt", action="append", help="prompt file; repeat to compare (default: production prompt)")
    ap.add_argument("--episode", action="append", default=[], help="substring filter; repeatable")
    ap.add_argument("--out", help="output dir (default: <data-dir>/evals/<timestamp>)")
    ap.add_argument("--tuned", default=str(TUNED_FILE), help="file listing tuned episodes, one per line")
    ap.add_argument("--held-out", action="store_true",
                    help="when comparing arms, also score held-out episodes (the final check, once)")
    args = ap.parse_args(argv)

    data_dir = Path(args.data_dir).resolve()
    arms = [str(Path(p).resolve()) for p in (args.prompt or [os.path.normpath(DEFAULT_PROMPT_PATH)])]
    episodes = reviewed_episodes(data_dir, args.episode)
    tuned = tuned_set(episodes, read_tuned(Path(args.tuned)))
    episodes = to_score(episodes, tuned, len(arms), args.held_out)
    if len(arms) > 1 and not args.held_out:
        print(f"Comparing {len(arms)} arms on the {len(episodes)} tuned episodes only; "
              "pass --held-out for the final check.", file=sys.stderr)
    if not episodes:
        print("No reviewed episodes match.", file=sys.stderr)
        return 1
    out = Path(args.out) if args.out else data_dir / "evals" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    os.environ["DATA_DIR"] = str(out)  # LLM cost events land here, not on the dashboard
    get_monitor().profile_dir = out / "llm_profiles"
    get_monitor().profile_dir.mkdir(exist_ok=True)
    data = {ep: load(data_dir, ep) for ep in episodes}
    feeds = {ep: labeling.feed_for(data_dir, ep, {}) for ep in episodes}

    rows = []
    with open(out / "results.jsonl", "w") as f:
        for n in range(args.runs):
            # Round-robin, rotating which arm goes first each round.
            for arm in arms[n % len(arms):] + arms[: n % len(arms)]:
                for ep in episodes:
                    segments, labels = data[ep]
                    row = {"round": n, "arm": arm, "episode": ep, "ts": datetime.now().isoformat(timespec="seconds")}
                    try:
                        spans = detect(segments, ep, arm, feeds[ep])
                        row.update(score(segments, labels, spans, cut_policy.cut_labels(feeds[ep])), spans=spans)
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
    summary = summarize(rows, arms, episodes, cost, tuned)
    (out / "summary.md").write_text(summary)
    print("\n" + summary + f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
