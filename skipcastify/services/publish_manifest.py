"""Local record of everything published to R2.

This file is the **source of truth** for the storage cap and for retention,
because the alternative — asking R2 what it holds — means ListObjectsV2, a
Class A operation we are trying not to spend (see r2_client).

Stored as one JSON document in the data dir and written atomically, so an
interrupted run can't leave a half-written manifest behind. The volume is tiny:
three pilot feeds with 14-day retention is a few hundred entries.

Keys embed PUBLIC_PATH_SECRET, so this file is as sensitive as .env — it lives
under data/, which is gitignored.
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)

VERSION = 1


class PublishManifest:
    def __init__(self, data_dir: str, path: str | None = None) -> None:
        self.path = Path(path) if path else Path(data_dir) / "publish_manifest.json"
        self._doc = self._load()

    # --- persistence --------------------------------------------------------

    def _blank(self) -> dict:
        return {"version": VERSION, "objects": {}, "ops": {}, "notified": []}

    def _load(self) -> dict:
        if not self.path.exists():
            return self._blank()
        try:
            doc = json.loads(self.path.read_text())
        except Exception as e:
            # A corrupt manifest must not wedge the pipeline, but losing it
            # silently would strand objects in R2 with nothing tracking them,
            # so make the damage loud and move the file aside for inspection.
            backup = self.path.with_suffix(".corrupt")
            logger.error("Unreadable publish manifest (%s); moved to %s and "
                         "starting fresh. Orphaned R2 objects may need a manual "
                         "reconcile.", e, backup.name)
            try:
                self.path.rename(backup)
            except OSError:
                pass
            return self._blank()
        for field in ("objects", "ops", "notified"):
            doc.setdefault(field, {} if field != "notified" else [])
        return doc

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._doc, indent=2, sort_keys=True))
        os.replace(tmp, self.path)

    # --- objects ------------------------------------------------------------

    def record_put(self, key: str, size: int, kind: str,
                   sha256: str | None = None, now: float | None = None) -> None:
        self._doc["objects"][key] = {
            "size": int(size),
            "kind": kind,
            "uploaded_at": float(now if now is not None else time.time()),
            "sha256": sha256,
        }

    def record_delete(self, keys: Iterable[str], now: float | None = None) -> None:
        """Tombstone keys rather than forgetting them.

        Dropping the entry outright would be a bug: the processed .mp3 is still
        on local disk, so the next pipeline run would see "processed file
        exists, nothing in the manifest" and upload it again — re-publishing
        every episode retention just expired, forever. A tombstone records that
        we deliberately removed it.
        """
        now = now if now is not None else time.time()
        for key in keys:
            entry = self._doc["objects"].get(key)
            if entry is None:
                continue
            entry["deleted_at"] = float(now)
            entry["size"] = 0

    def _live(self) -> dict:
        return {k: v for k, v in self._doc["objects"].items()
                if not v.get("deleted_at")}

    def is_tombstoned(self, key: str) -> bool:
        """True if this key was published and then deliberately removed."""
        entry = self._doc["objects"].get(key)
        return bool(entry and entry.get("deleted_at"))

    def prune_tombstones(self, max_age_s: float, now: float | None = None) -> int:
        """Forget old tombstones so the manifest cannot grow without bound.

        Safe once the local .mp3 is long gone; keep the window comfortably
        longer than retention so it never races the re-upload guard above.
        """
        now = now if now is not None else time.time()
        stale = [k for k, v in self._doc["objects"].items()
                 if v.get("deleted_at") and now - v["deleted_at"] > max_age_s]
        for key in stale:
            del self._doc["objects"][key]
        return len(stale)

    def get(self, key: str) -> dict | None:
        return self._doc["objects"].get(key)

    def has(self, key: str) -> bool:
        """True only for an object currently live in the bucket."""
        entry = self._doc["objects"].get(key)
        return bool(entry and not entry.get("deleted_at"))

    def sha256(self, key: str) -> str | None:
        entry = self._doc["objects"].get(key)
        return entry.get("sha256") if entry and not entry.get("deleted_at") else None

    def keys_of_kind(self, kind: str) -> list[str]:
        return [k for k, v in self._live().items() if v.get("kind") == kind]

    def total_bytes(self, kind: str | None = None) -> int:
        return sum(v.get("size", 0) for v in self._live().values()
                   if kind is None or v.get("kind") == kind)

    def oldest_first(self, kind: str) -> list[str]:
        """Live keys of one kind, oldest upload first — the cap's eviction order."""
        entries = [(k, v) for k, v in self._live().items()
                   if v.get("kind") == kind]
        entries.sort(key=lambda kv: kv[1].get("uploaded_at", 0))
        return [k for k, _ in entries]

    def expired(self, kind: str, max_age_s: float,
                now: float | None = None) -> list[str]:
        now = now if now is not None else time.time()
        return [k for k, v in self._live().items()
                if v.get("kind") == kind
                and now - v.get("uploaded_at", now) > max_age_s]

    # --- operation accounting ----------------------------------------------

    # The month is the billing period; the day is what the circuit breaker
    # watches, because 1M ops a month is 32k a day and a runaway loop would
    # burn through the month's allowance long before the monthly figure
    # looked alarming. Both are UTC, matching Cloudflare's own reset.

    @staticmethod
    def _month(now: float | None = None) -> str:
        return time.strftime("%Y-%m", time.gmtime(now if now is not None else time.time()))

    @staticmethod
    def _day(now: float | None = None) -> str:
        return time.strftime("%Y-%m-%d", time.gmtime(now if now is not None else time.time()))

    def bump_class_a(self, n: int = 1, now: float | None = None) -> None:
        month, day = self._month(now), self._day(now)
        ops = self._doc["ops"]
        # Only the current month is kept; last month's figure has no decisions
        # riding on it and keeping it would grow the file forever.
        if ops.get("month") != month:
            ops.clear()
            ops["month"] = month
            ops["class_a"] = 0
        if ops.get("day") != day:
            ops["day"] = day
            ops["class_a_day"] = 0
        ops["class_a"] = ops.get("class_a", 0) + n
        ops["class_a_day"] = ops.get("class_a_day", 0) + n

    def class_a_this_month(self, now: float | None = None) -> int:
        ops = self._doc["ops"]
        if ops.get("month") != self._month(now):
            return 0
        return ops.get("class_a", 0)

    def class_a_today(self, now: float | None = None) -> int:
        ops = self._doc["ops"]
        if ops.get("day") != self._day(now):
            return 0
        return ops.get("class_a_day", 0)

    # --- one-shot alert de-duplication -------------------------------------

    def was_notified(self, tag: str) -> bool:
        return tag in self._doc["notified"]

    def mark_notified(self, tag: str) -> None:
        if tag not in self._doc["notified"]:
            self._doc["notified"].append(tag)

    def clear_notified(self, tag: str) -> None:
        if tag in self._doc["notified"]:
            self._doc["notified"].remove(tag)
