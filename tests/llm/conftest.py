"""Shared helpers for per-feed LLM regression tests.

Each feed gets its own test file (test_<feed_slug>.py). Import `detect`,
`covered` and `any_overlap` from here; define `segments` and `llm_spans` fixtures
in each test file directly.

Comparing two prompts: interleave the arms round-robin, never one arm after
the other. The model's dominant output mode drifts over hours on a pinned
snapshot at temperature 0, so a sequential A/B measures the clock as much as
the prompt — on 2026-11-05 it reversed the sign of two findings.

Tests assert on `detect()`, which is what the pipeline would cut: the LLM's
spans after the default cut policy and code post-processing, not raw output.
"""

import tempfile

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


def detect(segments, episode_name: str) -> list[dict]:
    """Spans the pipeline would cut for this clip, under the default policy."""
    from skipcastify.services import cut_policy, span_postprocess
    from skipcastify.services.audio_processor import AudioProcessor
    with tempfile.TemporaryDirectory() as tmp:
        spans = AudioProcessor("data")._run_section_llm(segments, tmp, episode_name=episode_name)
    spans = [s for s in spans if cut_policy.span_label(s) in cut_policy.DEFAULT_CUT]
    return span_postprocess.postprocess_spans(spans, segments) if spans else spans
