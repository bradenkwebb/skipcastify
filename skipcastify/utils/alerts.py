"""Throttled alerting, so a failing pipeline is loud once rather than endlessly.

The pipeline runs every 15 minutes. An unguarded alert on an unhandled
exception would therefore mean ~96 identical pushes a day for one broken
thing — which trains you to ignore the channel, the opposite of the point. So
alerts are keyed by a *fingerprint* (e.g. exception type + stage) and sent at
most once per window per fingerprint.

The state is a small JSON file under the data dir. It also holds the one piece
of durable state the "recovered" alert needs: which fingerprints were failing
when we last looked, so the first clean run after a failure can say so.

Nothing here may break the pipeline: every filesystem and network failure is
swallowed and logged, and the alert path is never in a position to raise.

Alert text is **scrubbed** before it is sent. Exception messages routinely
carry the thing that failed, and around here that is often a URL with a
private token in it (the Substack feed, an R2 object key); ntfy.sh is a public
topic over the open internet, so sending one verbatim would publish it.
"""

import json
import logging
import os
import re
import time
from pathlib import Path

from skipcastify.utils.notify import notify as ntfy_notify

logger = logging.getLogger(__name__)

DEFAULT_WINDOW_S = 6 * 3600
MAX_MESSAGE_CHARS = 400

# Environment variables whose *values* must never appear in an alert. Checked
# as plain substrings, which catches them wherever they turn up — inside a URL,
# a file path, or a boto3 error string.
SECRET_ENV_VARS = (
    "OPENAI_API_KEY",
    "R2_SECRET_ACCESS_KEY",
    "R2_ACCESS_KEY_ID",
    "PUBLIC_PATH_SECRET",
    "EPISODE_TOKEN",
    "FEED_PASSWORD",
    "NTFY_TOPIC",
)

# Tokens that live in a URL rather than in our environment — chiefly the
# private Substack feed token, which arrives from upstream.opml and is not an
# env var we could match on.
# `<` is excluded from both value patterns so that scrubbing is idempotent:
# without it, a second pass over an already-scrubbed string matches the
# `<REDACTED` inside its own replacement and appends another `>` each time.
# That happens in practice — server.py scrubs a request path at the point of
# logging, and a logging filter scrubs the formatted record again.
_QUERY_TOKEN = re.compile(
    r"\b(private|token|api[_-]?key|key|auth|signature|sig)=[^\s&\"'><]+", re.I)
_PATH_TOKEN = re.compile(r"/(private|s)/[^\s/\"'><]+", re.I)
_USERINFO = re.compile(r"(https?://)[^/\s:@]+:[^/\s@]+@")


def scrub_secrets(text: str) -> str:
    """Remove credentials from text that is about to leave the machine."""
    out = str(text)
    for name in SECRET_ENV_VARS:
        value = os.environ.get(name, "").strip()
        # A short value would match far too much ("5000", "data"); a real
        # credential is never that short.
        if len(value) >= 8 and value in out:
            out = out.replace(value, f"<{name}>")
    out = _USERINFO.sub(r"\1<REDACTED>@", out)
    out = _QUERY_TOKEN.sub(lambda m: f"{m.group(1)}=<REDACTED>", out)
    out = _PATH_TOKEN.sub(lambda m: f"/{m.group(1)}/<REDACTED>", out)
    if len(out) > MAX_MESSAGE_CHARS:
        out = out[:MAX_MESSAGE_CHARS - 1] + "…"
    return out


class Alerter:
    """Sends pushes, deduplicated per fingerprint over a time window."""

    def __init__(self, data_dir: str, path: str | None = None,
                 notifier=ntfy_notify, window_s: float = DEFAULT_WINDOW_S) -> None:
        self.path = Path(path) if path else Path(data_dir) / "alert_state.json"
        self.window_s = window_s
        self.notifier = notifier
        self._doc = self._load()

    # --- state --------------------------------------------------------------

    def _load(self) -> dict:
        try:
            doc = json.loads(self.path.read_text())
            if not isinstance(doc, dict):
                raise ValueError("not an object")
        except FileNotFoundError:
            return {"sent": {}, "failing": {}}
        except Exception as e:
            # Losing this file costs one duplicate alert, so unlike the publish
            # manifest there is nothing here worth preserving or shouting about.
            logger.warning("Unreadable alert state (%s); starting fresh", e)
            return {"sent": {}, "failing": {}}
        doc.setdefault("sent", {})
        doc.setdefault("failing", {})
        return doc

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._doc, indent=2, sort_keys=True))
            os.replace(tmp, self.path)
        except Exception as e:
            logger.warning("Could not save alert state: %s", e)

    # --- sending ------------------------------------------------------------

    def throttled(self, fingerprint: str, now: float | None = None) -> bool:
        """True if this fingerprint already alerted inside the window."""
        last = self._doc["sent"].get(fingerprint)
        if last is None:
            return False
        return (now if now is not None else time.time()) - last < self.window_s

    def send(self, fingerprint: str, title: str, message: str,
             priority: str = "default", now: float | None = None) -> bool:
        """Alert unless this fingerprint is still inside its window.

        Returns True if a push was attempted. The window is recorded either
        way, so a delivery failure cannot turn into a retry storm.
        """
        now = now if now is not None else time.time()
        if self.throttled(fingerprint, now):
            logger.debug("Alert %s throttled: %s", fingerprint, title)
            return False
        self._doc["sent"][fingerprint] = now
        self._prune(now)
        self._save()
        try:
            self.notifier(scrub_secrets(title), scrub_secrets(message), priority)
        except Exception as e:
            logger.warning("Alert %s could not be sent: %s", fingerprint, e)
        return True

    def _prune(self, now: float) -> None:
        """Forget windows that have expired, so the file cannot grow forever.

        Kept at four windows rather than one: a fingerprint is cheap to hold
        and dropping it the instant it expires makes the file churn.
        """
        cutoff = now - self.window_s * 4
        for key in [k for k, t in self._doc["sent"].items() if t < cutoff]:
            del self._doc["sent"][key]

    # --- failure / recovery -------------------------------------------------
    #
    # Separate from the send windows on purpose. A window expires after 6h, but
    # a pipeline that has been broken for two days and then recovers should
    # still say so — "recovered" has to outlive the throttle.

    def mark_failing(self, fingerprint: str, detail: str = "",
                     now: float | None = None) -> None:
        self._doc["failing"][fingerprint] = {
            "since": self._doc["failing"].get(fingerprint, {}).get(
                "since", now if now is not None else time.time()),
            "detail": scrub_secrets(detail),
        }
        self._save()

    def failing(self) -> dict:
        return dict(self._doc["failing"])

    def clear_failing(self, now: float | None = None) -> bool:
        """Announce recovery if anything was failing. True if an alert was sent.

        Not throttled: a recovery is a state change, not a repeated condition,
        and it can only fire once per failure because the flags are cleared
        here.
        """
        failing = self._doc["failing"]
        if not failing:
            return False
        now = now if now is not None else time.time()
        worst = min(v.get("since", now) for v in failing.values())
        what = ", ".join(sorted(failing))
        self._doc["failing"] = {}
        # Drop the send windows too, so a *later* recurrence of the same
        # failure alerts immediately instead of being silenced by the window
        # left over from the episode that just ended.
        for fingerprint in failing:
            self._doc["sent"].pop(fingerprint, None)
        self._save()
        try:
            self.notifier(
                "Pipeline recovered",
                scrub_secrets(f"First clean run after {(now - worst) / 3600:.1f}h "
                              f"of failures ({what})."),
                "default")
        except Exception as e:
            logger.warning("Recovery alert could not be sent: %s", e)
        return True
