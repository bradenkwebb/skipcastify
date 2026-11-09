"""AdSpan is the one place the model's free-form answer becomes a span."""

import pytest

from skipcastify.models.ad_span import AdSpan
from skipcastify.models.content import ContentType


class TestParsingOneItem:
    def test_the_ordinary_case(self):
        span = AdSpan.parse({"start_ms": 1000, "end_ms": 2000,
                             "label": "SPONSOR", "rationale": "host read"})
        assert span == AdSpan(1000, 2000, "SPONSOR", "host read")
        assert span.duration_ms == 1000

    def test_numeric_strings_are_coerced(self):
        """The model returns "1000" often enough to matter."""
        assert AdSpan.parse({"start_ms": "1000", "end_ms": "2000"}) == AdSpan(1000, 2000)

    @pytest.mark.parametrize("item", [
        {"start_ms": 5, "end_ms": 5},            # empty
        {"start_ms": 2000, "end_ms": 1000},      # inverted
        {"start_ms": "soon", "end_ms": 2000},    # unparseable
        {"start_ms": None, "end_ms": 2000},
        ["not", "a", "dict"],
        "nope",
        None,
    ])
    def test_unusable_items_are_dropped(self, item):
        assert AdSpan.parse(item) is None

    def test_a_missing_start_means_the_window_start(self):
        """Carried over from the original parse: absent start_ms reads as 0,
        which `bounds` then lifts to the start of the section."""
        assert AdSpan.parse({"end_ms": 2_000}) == AdSpan(0, 2_000)
        assert AdSpan.parse({"end_ms": 2_000}, bounds=(1_000, 60_000)).start_ms == 1_000

    def test_a_missing_label_defaults_to_advertisement(self):
        assert AdSpan.parse({"start_ms": 1, "end_ms": 2}).label == "ADVERTISEMENT"
        assert AdSpan.parse({"start_ms": 1, "end_ms": 2, "label": ""}).label == "ADVERTISEMENT"

    def test_an_unknown_label_is_kept(self):
        """The model invents labels; a span is still a span, and the stitcher
        treats anything that is not an ad as a sponsor read."""
        span = AdSpan.parse({"start_ms": 1, "end_ms": 2, "label": "PROMO"})
        assert span.label == "PROMO"
        assert span.content_type() == ContentType.SPONSOR


class TestBounds:
    """The model is shown one section and has no evidence outside it."""

    def test_endpoints_are_clamped_to_the_window(self):
        span = AdSpan.parse({"start_ms": 100, "end_ms": 99_999_999}, bounds=(1_000, 60_000))
        assert (span.start_ms, span.end_ms) == (1_000, 60_000)

    def test_a_span_entirely_outside_the_window_is_dropped(self):
        assert AdSpan.parse({"start_ms": 500_000, "end_ms": 600_000},
                            bounds=(0, 60_000)) is None

    def test_without_bounds_nothing_is_clamped(self):
        span = AdSpan.parse({"start_ms": 0, "end_ms": 99_999_999})
        assert span.end_ms == 99_999_999


class TestParseMany:
    def test_order_is_preserved_and_bad_items_skipped(self):
        spans = AdSpan.parse_many([
            {"start_ms": 3_000, "end_ms": 4_000},
            {"start_ms": 9, "end_ms": 9},          # dropped
            "garbage",                              # dropped
            {"start_ms": 1_000, "end_ms": 2_000},
        ])
        assert [(s.start_ms, s.end_ms) for s in spans] == [(3_000, 4_000), (1_000, 2_000)]

    def test_an_empty_or_none_response_is_an_empty_list(self):
        assert AdSpan.parse_many([]) == []
        assert AdSpan.parse_many(None) == []


class TestWireFormat:
    def test_round_trip_keeps_the_four_stored_keys(self):
        d = {"start_ms": 1, "end_ms": 2, "label": "SPONSOR", "rationale": "why"}
        assert AdSpan.from_dict(d).to_dict() == d

    def test_to_dict_always_has_all_four_keys(self):
        assert set(AdSpan(1, 2).to_dict()) == {"start_ms", "end_ms", "label", "rationale"}


class TestContentType:
    @pytest.mark.parametrize("label,expected", [
        ("ADVERTISEMENT", ContentType.ADVERTISEMENT),
        ("OTHER_AD", ContentType.ADVERTISEMENT),
        ("SPONSOR", ContentType.SPONSOR),
    ])
    def test_the_mapping_the_stitcher_relies_on(self, label, expected):
        assert AdSpan(1, 2, label).content_type() == expected


class TestOverlaps:
    def test_half_open_intervals_do_not_touch(self):
        span = AdSpan(1_000, 2_000)
        assert span.overlaps(1_500, 2_500)
        assert span.overlaps(0, 1_001)
        assert not span.overlaps(2_000, 3_000)   # abutting is not overlapping
        assert not span.overlaps(0, 1_000)
