"""ntfy push notifications, matching the topic monitor.sh already uses.

Alerting must never be able to break the pipeline, so every failure here is
swallowed and logged. An unset NTFY_TOPIC makes this a no-op, which is what
the test suite and any fresh checkout get.
"""

import logging
import os

import requests

logger = logging.getLogger(__name__)

_TIMEOUT_S = 10


def notify(title: str, message: str, priority: str = "default") -> bool:
    """Send one push. Returns True if it was accepted, False otherwise."""
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        logger.debug("NTFY_TOPIC unset; skipping notification: %s", title)
        return False
    try:
        resp = requests.post(
            f"https://ntfy.sh/{topic}",
            data=message.encode("utf-8"),
            headers={"Title": title, "Priority": priority},
            timeout=_TIMEOUT_S,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.warning("Could not send ntfy notification %r: %s", title, e)
        return False
