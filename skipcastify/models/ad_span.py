import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Optional

from skipcastify.models.content import ContentType

logger = logging.getLogger(__name__)

DEFAULT_LABEL = "ADVERTISEMENT"
ADVERTISEMENT_LABELS = ("ADVERTISEMENT", "OTHER_AD")


@dataclass
class AdSpan:
    start_ms: int
    end_ms: int
    label: str = DEFAULT_LABEL
    rationale: str = ""

    def overlaps(self, start_ms: int, end_ms: int) -> bool:
        return self.start_ms < end_ms and self.end_ms > start_ms

    def content_type(self) -> ContentType:
        return (ContentType.ADVERTISEMENT if self.label in ADVERTISEMENT_LABELS
                else ContentType.SPONSOR)

    def to_dict(self) -> dict:
        return {"start_ms": self.start_ms, "end_ms": self.end_ms,
                "label": self.label, "rationale": self.rationale}

    @classmethod
    def from_dict(cls, d: dict) -> "AdSpan":
        return cls(start_ms=int(d["start_ms"]), end_ms=int(d["end_ms"]),
                   label=str(d.get("label") or DEFAULT_LABEL),
                   rationale=str(d.get("rationale") or ""))

    @classmethod
    def parse(cls, item: Any,
              bounds: tuple[int, int] | None = None) -> Optional["AdSpan"]:
        if not isinstance(item, dict):
            return None
        try:
            start_ms = int(item.get("start_ms", 0))
            end_ms = int(item.get("end_ms", 0))
        except (TypeError, ValueError):
            return None

        if bounds is not None:
            start_ms = max(start_ms, bounds[0])
            end_ms = min(end_ms, bounds[1])
        if start_ms >= end_ms:
            return None

        return cls(start_ms=start_ms, end_ms=end_ms,
                   label=str(item.get("label") or DEFAULT_LABEL),
                   rationale=str(item.get("rationale") or ""))

    @classmethod
    def parse_many(cls, items: Iterable[Any],
                   bounds: tuple[int, int] | None = None,
                   context: str = "") -> list["AdSpan"]:
        spans = []
        for item in items or []:
            span = cls.parse(item, bounds=bounds)
            if span is None:
                logger.debug("Ignoring unusable span %r%s", item,
                             f" in {context}" if context else "")
                continue
            spans.append(span)
        return spans
