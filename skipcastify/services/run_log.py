"""Pipeline run history: one JSON line per run in data/metrics/runs.jsonl."""

import json
import time
from collections import deque
from datetime import datetime
from pathlib import Path

KEEP_RUNS = 3000  # ~1 month at one run per 15 minutes


def _path(data_dir) -> Path:
    return Path(data_dir) / "metrics" / "runs.jsonl"


class RunRecord:
    def __init__(self) -> None:
        self._t0 = time.monotonic()
        self.started = datetime.now().astimezone().isoformat(timespec="seconds")
        self.feeds: list[dict] = []
        self.processed: list[dict] = []
        self.published: int | None = None

    def to_dict(self, outcome: str, stage: str | None = None, error: str | None = None) -> dict:
        d = {
            "started": self.started,
            "duration_s": round(time.monotonic() - self._t0, 1),
            "outcome": outcome,
            "feeds": self.feeds,
            "processed": self.processed,
            "published": self.published,
        }
        if stage:
            d["stage"] = stage
        if error:
            d["error"] = error
        return d


def _inflight_path(data_dir) -> Path:
    return Path(data_dir) / "metrics" / "run_inflight.json"


def write_inflight(data_dir, run: RunRecord, stage: str, episode: str | None = None) -> None:
    """Where the current run is. Left behind only if the process dies without
    unwinding (SIGKILL, OOM, power loss), which the next run then reports."""
    path = _inflight_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"started": run.started, "stage": stage, "episode": episode,
                               "duration_s": round(time.monotonic() - run._t0, 1)}))
    tmp.replace(path)


def clear_inflight(data_dir) -> None:
    _inflight_path(data_dir).unlink(missing_ok=True)


def take_inflight(data_dir) -> dict | None:
    """The marker a dead run left, removed; call only while holding the pipeline lock."""
    path = _inflight_path(data_dir)
    try:
        marker = json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except ValueError:
        marker = {}
    path.unlink(missing_ok=True)
    return marker


def append_run(data_dir, record: dict) -> None:
    path = _path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")
    # Trim occasionally rather than on every write.
    if path.stat().st_size > KEEP_RUNS * 600:
        lines = path.read_text().splitlines(keepends=True)[-KEEP_RUNS:]
        tmp = path.with_suffix(".tmp")
        tmp.write_text("".join(lines))
        tmp.replace(path)


def recent_runs(data_dir, n: int = 50) -> list[dict]:
    """Newest first; malformed lines skipped."""
    path = _path(data_dir)
    if not path.exists():
        return []
    with open(path) as f:
        tail = deque(f, maxlen=n)
    runs = []
    for line in reversed(tail):
        try:
            runs.append(json.loads(line))
        except ValueError:
            continue
    return runs
