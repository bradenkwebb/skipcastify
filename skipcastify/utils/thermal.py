"""CPU temperature utilities for thermal-aware processing on low-TDP hardware."""

import glob
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def cpu_temp_celsius() -> float | None:
    """Return the highest thermal zone reading in °C, or None if unreadable."""
    try:
        readings = []
        for path in glob.glob("/sys/class/thermal/thermal_zone*/temp"):
            try:
                readings.append(int(Path(path).read_text().strip()) / 1000)
            except Exception:
                pass
        return max(readings) if readings else None
    except Exception:
        return None


def wait_for_cool_cpu(
    threshold_c: float | None = None,
    poll_s: int = 30,
    max_wait_s: int = 600,
) -> None:
    """Block until CPU temp drops below threshold_c, then return.

    threshold_c defaults to the CPU_TEMP_THRESHOLD env var (or 72°C).
    Gives up and logs a warning after max_wait_s seconds.
    """
    if threshold_c is None:
        threshold_c = float(os.environ.get("CPU_TEMP_THRESHOLD", "72"))
    waited = 0
    while waited < max_wait_s:
        temp = cpu_temp_celsius()
        if temp is None or temp < threshold_c:
            return
        logger.info(
            "CPU at %.0f°C (threshold %.0f°C) — waiting %ds to cool",
            temp, threshold_c, poll_s,
        )
        time.sleep(poll_s)
        waited += poll_s
    logger.warning(
        "CPU did not cool below %.0f°C after %ds — proceeding anyway",
        threshold_c, max_wait_s,
    )
