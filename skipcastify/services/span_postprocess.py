"""Code-level post-processing for LLM-identified ad spans.

Applied after the stage-1 LLM call to fix common boundary errors without
additional LLM calls:
  - merge_nearby_spans: collapses spans within a short gap into one
  - extend_for_cta: extends a span's end_ms when the immediately following
    segment contains a URL call-to-action that belongs to the same ad
"""

import re
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Matches URL CTAs ("go to X.com") and bare domain-like strings (X.com, X.io)
_URL_CTA_RE = re.compile(
    r'\b(?:go to|visit|check out|find us at|learn more at|sign up at|'
    r'available at|listen at|subscribe at|tune in at|find out more at)\b'
    r'|'
    r'\b[a-z]{2,}\.(?:com|io|co|net|org|fm|app|tv|me|ai)\b',
    re.IGNORECASE,
)


def _has_url_cta(text: str) -> bool:
    return bool(_URL_CTA_RE.search(text))


def merge_nearby_spans(spans: list[dict], gap_ms: int = 15_000) -> list[dict]:
    """Merge ad spans that are within gap_ms of each other.

    An 8-second gap between a Snapdragon mid-roll and an Old Gays promo, for
    example, is almost never substantive editorial content — merge them.
    """
    if not spans:
        return spans
    sorted_spans = sorted(spans, key=lambda s: s["start_ms"])
    merged = [sorted_spans[0].copy()]
    for span in sorted_spans[1:]:
        gap = span["start_ms"] - merged[-1]["end_ms"]
        if gap <= gap_ms:
            merged[-1]["end_ms"] = max(merged[-1]["end_ms"], span["end_ms"])
        else:
            merged.append(span.copy())
    if len(merged) < len(spans):
        logger.info("merge_nearby_spans: %d → %d spans (gap_ms=%d)", len(spans), len(merged), gap_ms)
    return merged


def extend_for_cta(spans: list[dict], segments: list[Any], lookahead_ms: int = 5_000) -> list[dict]:
    """Extend a span's end_ms when the immediately following segment has a URL CTA.

    Handles the pattern where the LLM correctly identifies an ad setup but
    draws the boundary before the segment containing the product URL/sign-off.
    """
    result = []
    for span in spans:
        end_ms = span["end_ms"]
        # Find the first segment that starts at or just after end_ms
        candidates = [s for s in segments if s.start >= end_ms and s.start - end_ms <= lookahead_ms]
        if candidates:
            next_seg = min(candidates, key=lambda s: s.start)
            if _has_url_cta(next_seg.text):
                logger.info(
                    "extend_for_cta: span [%d-%d] → end=%d (CTA: %r)",
                    span["start_ms"], end_ms, next_seg.end, next_seg.text[:60],
                )
                end_ms = next_seg.end
        result.append({**span, "end_ms": end_ms})
    return result


def postprocess_spans(spans: list[dict], segments: list[Any], gap_ms: int = 15_000) -> list[dict]:
    """Apply all code-level post-processing in order: merge, then extend for CTA."""
    spans = merge_nearby_spans(spans, gap_ms=gap_ms)
    spans = extend_for_cta(spans, segments)
    return spans
