"""Shared helpers for per-feed LLM regression tests.

Each feed gets its own test file (test_<feed_slug>.py). Import `covered`
and `any_overlap` from here; define `segments` and `llm_spans` fixtures
in each test file directly.
"""

import pytest
from dotenv import load_dotenv

load_dotenv()


def covered(spans: list[dict], start_ms: int, end_ms: int, tolerance_ms: int = 8000) -> bool:
    """True if spans cover at least 80% of the inner portion of [start_ms, end_ms]."""
    inner_start = start_ms + tolerance_ms
    inner_end = end_ms - tolerance_ms
    if inner_start >= inner_end:
        return any(s["end_ms"] > start_ms and s["start_ms"] < end_ms for s in spans)
    covered_ms = 0
    for s in spans:
        overlap_start = max(s["start_ms"], inner_start)
        overlap_end = min(s["end_ms"], inner_end)
        if overlap_end > overlap_start:
            covered_ms += overlap_end - overlap_start
    return covered_ms >= (inner_end - inner_start) * 0.8


def any_overlap(spans: list[dict], start_ms: int, end_ms: int) -> bool:
    """True if any span overlaps with [start_ms, end_ms]."""
    return any(s["end_ms"] > start_ms and s["start_ms"] < end_ms for s in spans)
