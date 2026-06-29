"""Lightweight pipeline event logger — appends one JSON record per event to events.jsonl."""

import json
import os
from datetime import datetime
from pathlib import Path


def log_event(event: str, **kwargs) -> None:
    data_dir = os.environ.get("DATA_DIR", "data")
    path = Path(data_dir) / "metrics" / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"ts": datetime.now().isoformat(), "event": event, **kwargs}
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")
