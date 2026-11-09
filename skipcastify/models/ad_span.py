"""One advertisement span on an episode's timeline — the detector's output.

The LLM answers in free-form JSON, so coercion and bounding live here, at the
single place that answer enters the pipeline. The wire format is unchanged:
`to_dict()` round-trips the same four keys the event log, predictions.json and
the label files have always carried.
"""

import logging
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from skipcastify.models.content import ContentType

logger = logging.getLogger(__name__)

# What the prompt asks the model to emit. Unknown labels are kept as given:
# the model occasionally invents one, and a span is still a span.
LABELS = ("ADVERTISEMENT", "OTHER_AD", "SPONSOR")
DEFAULT_LABEL = "ADVERTISEMENT"


@dataclass
class AdSpan:
    """A half-open interval [start_ms, end_ms) the detector wants removed."""

    start_ms: int
    end_ms: int
    label: str = DEFAULT_LABEL
    rationale: str = ""

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms

    def overlaps(self, start_ms: int, end_ms: int) -> bool:
        """True if this span shares any time with [start_ms, end_ms)."""
        return self.start_ms < end_ms and self.end_ms > start_ms

    def content_type(self) -> ContentType:
        """How the stitcher should treat this span."""
        return (ContentType.ADVERTISEMENT
                if self.label in ("ADVERTISEMENT", "OTHER_AD")
                else ContentType.SPONSOR)

    def to_dict(self) -> dict:
        return {"start_ms": self.start_ms, "end_ms": self.end_ms,
                "label": self.label, "rationale": self.rationale}

    @classmethod
    def parse(cls, item: Any,
              bounds: Optional[tuple[int, int]] = None) -> Optional["AdSpan"]:
        """One span from one LLM response item, or None if unusable.

        `bounds` is the window the model was shown; endpoints outside it are
        clamped to it, because the model saw no evidence beyond it.
        """
        if not isinstance(item, dict):
            return None
        try:
            start_ms = int(item.get("start_ms", 0))
            end_ms = int(item.get("end_ms", 0))
        except (TypeError, ValueError):
            return None

        if bounds is not None:
            low, high = bounds
            start_ms = max(start_ms, low)
            end_ms = min(end_ms, high)

        if start_ms >= end_ms:
            return None

        label = item.get("label") or DEFAULT_LABEL
        rationale = item.get("rationale") or ""
        return cls(start_ms=start_ms, end_ms=end_ms,
                   label=str(label), rationale=str(rationale))

    @classmethod
    def parse_many(cls, items: Iterable[Any],
                   bounds: Optional[tuple[int, int]] = None,
                   context: str = "") -> list["AdSpan"]:
        """Every usable span in an LLM response, in the order returned."""
        spans = []
        for item in items or []:
            span = cls.parse(item, bounds=bounds)
            if span is None:
                logger.debug("Ignoring unusable span %r%s", item,
                             f" in {context}" if context else "")
                continue
            spans.append(span)
        return spans

    @classmethod
    def from_dict(cls, d: dict) -> "AdSpan":
        """A span from our own stored JSON, which is already well-formed."""
        return cls(start_ms=int(d["start_ms"]), end_ms=int(d["end_ms"]),
                   label=str(d.get("label") or DEFAULT_LABEL),
                   rationale=str(d.get("rationale") or ""))
