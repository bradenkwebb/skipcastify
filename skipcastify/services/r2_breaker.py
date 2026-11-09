"""A persistent kill switch for R2 publishing, on Class A operation counts.

The alerting in this package is aimed at *errors*, but the likelier way to get
an unexpected Cloudflare bill is runaway **success**: a loop that re-uploads
the same three feeds, a fingerprint that stops matching, a retry that never
backs off. Each of those spends Class A operations while every log line says
the upload worked.

So the counts themselves are a tripwire. A normal pilot run performs single
digits of Class A operations — three feed PUTs at most, plus an episode or two.
Hundreds means something is wrong in a way no error path will catch.

Tripping is **persistent and manual to reset**, deliberately. An automatic
reset after an hour, or at midnight, would let a genuine runaway spend its
budget in daily instalments. Clearing it is::

    uv run python -m skipcastify.tools.r2ctl clear-breaker

Only *publishing* stops. Downloading and processing keep running, so the
episodes pile up locally and publish once the breaker is cleared.
"""

import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# A pilot run is single digits, so 200 is ~20x headroom and still two orders
# of magnitude below anything that could cost money.
DEFAULT_MAX_CLASS_A_PER_RUN = 200
# 2,000/day against a 1M monthly allowance: a month at this rate is 60k ops,
# 6% of the free tier.
DEFAULT_MAX_CLASS_A_PER_DAY = 2000


class R2Breaker:
    """Reads and writes the "R2 publishing disabled" marker."""

    def __init__(self, data_dir: str, path: str | None = None,
                 max_per_run: int = DEFAULT_MAX_CLASS_A_PER_RUN,
                 max_per_day: int = DEFAULT_MAX_CLASS_A_PER_DAY) -> None:
        self.path = Path(path) if path else Path(data_dir) / "r2_breaker.json"
        self.max_per_run = max_per_run
        self.max_per_day = max_per_day

    @classmethod
    def from_env(cls, data_dir: str) -> "R2Breaker":
        return cls(
            data_dir,
            max_per_run=int(os.environ.get("R2_MAX_CLASS_A_PER_RUN",
                                           DEFAULT_MAX_CLASS_A_PER_RUN)),
            max_per_day=int(os.environ.get("R2_MAX_CLASS_A_PER_DAY",
                                           DEFAULT_MAX_CLASS_A_PER_DAY)),
        )

    # --- state --------------------------------------------------------------

    def state(self) -> dict | None:
        """The trip record, or None if the breaker is closed.

        A marker that exists but cannot be parsed counts as tripped. Failing
        closed is the right way round for something whose whole job is to stop
        spending money: the cost of a wrong "tripped" is stale feeds, the cost
        of a wrong "fine" is the bill this exists to prevent.
        """
        try:
            raw = self.path.read_text()
        except FileNotFoundError:
            return None
        except OSError as e:
            logger.error("Could not read the R2 breaker marker (%s); treating "
                         "publishing as disabled", e)
            return {"reason": "breaker marker unreadable"}
        try:
            doc = json.loads(raw)
            if not isinstance(doc, dict):
                raise ValueError("not an object")
            return doc
        except Exception as e:
            logger.error("Corrupt R2 breaker marker (%s); treating publishing "
                         "as disabled", e)
            return {"reason": "breaker marker corrupt"}

    @property
    def tripped(self) -> bool:
        return self.state() is not None

    @property
    def reason(self) -> str | None:
        state = self.state()
        return state.get("reason") if state else None

    # --- transitions --------------------------------------------------------

    def trip(self, reason: str, detail: dict | None = None,
             now: float | None = None) -> None:
        now = now if now is not None else time.time()
        doc = {
            "reason": reason,
            "tripped_at": now,
            "tripped_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
            **(detail or {}),
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(doc, indent=2, sort_keys=True))
            os.replace(tmp, self.path)
        except OSError as e:
            # The in-run halt still applies; only persistence across runs is
            # lost, so say so loudly rather than pretending it stuck.
            logger.error("Could not persist the R2 breaker marker (%s). "
                         "Publishing is off for this run only.", e)
        logger.error("R2 publishing disabled: %s", reason)

    def clear(self) -> bool:
        """Re-enable publishing. True if the breaker was actually tripped."""
        try:
            self.path.unlink()
            logger.warning("R2 breaker cleared; publishing is enabled again")
            return True
        except FileNotFoundError:
            return False
