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
