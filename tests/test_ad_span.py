import pytest

from skipcastify.models.ad_span import AdSpan
from skipcastify.models.content import ContentType


class TestParse:
    def test_reads_the_four_fields(self):
        assert AdSpan.parse({"start_ms": 1000, "end_ms": 2000,
                             "label": "SPONSOR", "rationale": "host read"}) \
            == AdSpan(1000, 2000, "SPONSOR", "host read")

    def test_coerces_numeric_strings(self):
        assert AdSpan.parse({"start_ms": "1000", "end_ms": "2000"}) == AdSpan(1000, 2000)

    @pytest.mark.parametrize("item", [
        {"start_ms": 5, "end_ms": 5},
        {"start_ms": 2000, "end_ms": 1000},
        {"start_ms": "soon", "end_ms": 2000},
        {"start_ms": None, "end_ms": 2000},
        ["not", "a", "dict"],
        "nope",
        None,
    ])
    def test_returns_none_for_an_unusable_item(self, item):
        assert AdSpan.parse(item) is None

    def test_an_absent_start_reads_as_zero(self):
        assert AdSpan.parse({"end_ms": 2_000}) == AdSpan(0, 2_000)

    @pytest.mark.parametrize("item", [{"start_ms": 1, "end_ms": 2},
                                      {"start_ms": 1, "end_ms": 2, "label": ""}])
    def test_an_absent_label_reads_as_advertisement(self, item):
        assert AdSpan.parse(item).label == "ADVERTISEMENT"

    def test_keeps_a_label_it_does_not_know(self):
        span = AdSpan.parse({"start_ms": 1, "end_ms": 2, "label": "PROMO"})
        assert span.label == "PROMO"
        assert span.content_type() == ContentType.SPONSOR


class TestBounds:
    def test_clamps_both_endpoints_into_the_window(self):
        span = AdSpan.parse({"start_ms": 100, "end_ms": 99_999_999},
                            bounds=(1_000, 60_000))
        assert (span.start_ms, span.end_ms) == (1_000, 60_000)

    def test_drops_a_span_that_falls_outside_the_window(self):
        assert AdSpan.parse({"start_ms": 500_000, "end_ms": 600_000},
                            bounds=(0, 60_000)) is None

    def test_clamps_nothing_without_a_window(self):
        assert AdSpan.parse({"start_ms": 0, "end_ms": 99_999_999}).end_ms == 99_999_999


class TestParseMany:
    def test_keeps_the_order_returned_and_skips_unusable_items(self):
        spans = AdSpan.parse_many([
            {"start_ms": 3_000, "end_ms": 4_000},
            {"start_ms": 9, "end_ms": 9},
            "garbage",
            {"start_ms": 1_000, "end_ms": 2_000},
        ])
        assert [(s.start_ms, s.end_ms) for s in spans] == [(3_000, 4_000), (1_000, 2_000)]

    @pytest.mark.parametrize("items", [[], None])
    def test_returns_an_empty_list_for_an_empty_response(self, items):
        assert AdSpan.parse_many(items) == []


class TestStoredForm:
    def test_round_trips_through_a_dict(self):
        d = {"start_ms": 1, "end_ms": 2, "label": "SPONSOR", "rationale": "why"}
        assert AdSpan.from_dict(d).to_dict() == d

    def test_to_dict_always_carries_all_four_fields(self):
        assert set(AdSpan(1, 2).to_dict()) == {"start_ms", "end_ms", "label", "rationale"}


class TestContentType:
    @pytest.mark.parametrize("label,expected", [
        ("ADVERTISEMENT", ContentType.ADVERTISEMENT),
        ("OTHER_AD", ContentType.ADVERTISEMENT),
        ("SPONSOR", ContentType.SPONSOR),
    ])
    def test_maps_a_label_to_what_the_stitcher_removes(self, label, expected):
        assert AdSpan(1, 2, label).content_type() == expected


class TestOverlaps:
    @pytest.mark.parametrize("window,expected", [
        ((1_500, 2_500), True),
        ((0, 1_001), True),
        ((2_000, 3_000), False),
        ((0, 1_000), False),
    ])
    def test_treats_spans_as_half_open(self, window, expected):
        assert AdSpan(1_000, 2_000).overlaps(*window) is expected
