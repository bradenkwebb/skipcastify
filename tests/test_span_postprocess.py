"""Unit tests for code-level ad-span post-processing.

These are pure, deterministic tests — no network, no LLM, no filesystem.
"""
from collections import namedtuple

from skipcastify.services.span_postprocess import (
    merge_nearby_spans,
    extend_for_cta,
    postprocess_spans,
    _has_url_cta,
)

# Minimal stand-in for audio_processor.Segment (duck-typed: .start/.end/.text)
Seg = namedtuple("Seg", ["start", "end", "text"])


def _span(start_ms, end_ms, label="ADVERTISEMENT", rationale="r"):
    return {"start_ms": start_ms, "end_ms": end_ms, "label": label, "rationale": rationale}


# ---------------------------------------------------------------------------
# merge_nearby_spans
# ---------------------------------------------------------------------------

class TestMergeNearbySpans:
    def test_empty(self):
        assert merge_nearby_spans([]) == []

    def test_single_span_unchanged(self):
        spans = [_span(0, 10_000)]
        out = merge_nearby_spans(spans)
        assert out == spans

    def test_two_spans_within_gap_merge(self):
        # 5s gap, default threshold 15s -> merge
        out = merge_nearby_spans([_span(0, 10_000), _span(15_000, 20_000)])
        assert len(out) == 1
        assert out[0]["start_ms"] == 0
        assert out[0]["end_ms"] == 20_000

    def test_gap_exactly_threshold_merges(self):
        # gap == gap_ms must merge (boundary is <=)
        out = merge_nearby_spans([_span(0, 10_000), _span(25_000, 30_000)], gap_ms=15_000)
        assert len(out) == 1
        assert out[0]["end_ms"] == 30_000

    def test_gap_one_over_threshold_stays_split(self):
        out = merge_nearby_spans([_span(0, 10_000), _span(25_001, 30_000)], gap_ms=15_000)
        assert len(out) == 2

    def test_out_of_order_input_sorted_then_merged(self):
        out = merge_nearby_spans([_span(15_000, 20_000), _span(0, 10_000)])
        assert len(out) == 1
        assert (out[0]["start_ms"], out[0]["end_ms"]) == (0, 20_000)

    def test_overlap_where_second_ends_earlier_keeps_max_end(self):
        # span2 is fully inside span1 -> merged end must NOT shrink
        out = merge_nearby_spans([_span(0, 20_000), _span(5_000, 8_000)])
        assert len(out) == 1
        assert out[0]["end_ms"] == 20_000

    def test_three_spans_two_merge_one_separate(self):
        out = merge_nearby_spans([
            _span(0, 10_000),
            _span(12_000, 18_000),     # 2s gap -> merges with first
            _span(100_000, 110_000),   # far away -> separate
        ])
        assert len(out) == 2
        assert (out[0]["start_ms"], out[0]["end_ms"]) == (0, 18_000)
        assert (out[1]["start_ms"], out[1]["end_ms"]) == (100_000, 110_000)

    def test_custom_gap_param(self):
        # 20s gap, only merges if gap_ms raised above it
        spans = [_span(0, 10_000), _span(30_000, 40_000)]
        assert len(merge_nearby_spans(spans, gap_ms=15_000)) == 2
        assert len(merge_nearby_spans(spans, gap_ms=25_000)) == 1

    def test_merged_span_keeps_first_label_and_rationale(self):
        out = merge_nearby_spans([
            _span(0, 10_000, label="AD_A", rationale="first"),
            _span(12_000, 18_000, label="AD_B", rationale="second"),
        ])
        assert out[0]["label"] == "AD_A"
        assert out[0]["rationale"] == "first"

    def test_input_spans_not_mutated_on_merge(self):
        s1, s2 = _span(0, 10_000), _span(12_000, 18_000)
        merge_nearby_spans([s1, s2])
        assert s1["end_ms"] == 10_000  # copies are mutated, not originals


# ---------------------------------------------------------------------------
# extend_for_cta
# ---------------------------------------------------------------------------

class TestExtendForCta:
    def test_next_segment_with_cta_extends_end(self):
        spans = [_span(0, 40_000)]
        segs = [Seg(40_000, 50_000, "Go to acme.com to learn more.")]
        out = extend_for_cta(spans, segs)
        assert out[0]["end_ms"] == 50_000

    def test_next_segment_without_cta_unchanged(self):
        spans = [_span(0, 40_000)]
        segs = [Seg(40_000, 50_000, "And now back to the news.")]
        out = extend_for_cta(spans, segs)
        assert out[0]["end_ms"] == 40_000

    def test_segment_beyond_lookahead_unchanged(self):
        spans = [_span(0, 40_000)]
        # starts 6s after end, default lookahead 5s -> ignored
        segs = [Seg(46_000, 56_000, "Visit example.com now.")]
        out = extend_for_cta(spans, segs)
        assert out[0]["end_ms"] == 40_000

    def test_segment_exactly_at_lookahead_boundary_included(self):
        spans = [_span(0, 40_000)]
        segs = [Seg(45_000, 55_000, "Go to acme.com")]  # start-end == 5000 == lookahead
        out = extend_for_cta(spans, segs)
        assert out[0]["end_ms"] == 55_000

    def test_segment_starting_before_span_end_not_candidate(self):
        spans = [_span(0, 40_000)]
        segs = [Seg(39_000, 50_000, "Go to acme.com")]  # starts before end_ms
        out = extend_for_cta(spans, segs)
        assert out[0]["end_ms"] == 40_000

    def test_no_segments_unchanged(self):
        out = extend_for_cta([_span(0, 40_000)], [])
        assert out[0]["end_ms"] == 40_000

    def test_input_spans_not_mutated(self):
        spans = [_span(0, 40_000)]
        segs = [Seg(40_000, 50_000, "Go to acme.com")]
        extend_for_cta(spans, segs)
        assert spans[0]["end_ms"] == 40_000

    def test_multiple_spans_each_evaluated(self):
        spans = [_span(0, 40_000), _span(100_000, 140_000)]
        segs = [
            Seg(40_000, 50_000, "Go to acme.com"),
            Seg(140_000, 150_000, "Just talking here."),
        ]
        out = extend_for_cta(spans, segs)
        assert out[0]["end_ms"] == 50_000
        assert out[1]["end_ms"] == 140_000


# ---------------------------------------------------------------------------
# _has_url_cta
# ---------------------------------------------------------------------------

class TestHasUrlCta:
    def test_go_to_domain(self):
        assert _has_url_cta("go to acme.com")

    def test_bare_domain_variants(self):
        assert _has_url_cta("schwab.com")
        assert _has_url_cta("example.io")
        assert _has_url_cta("listen on something.fm")

    def test_cta_phrase_without_domain(self):
        assert _has_url_cta("available at your local store")
        assert _has_url_cta("subscribe at the link below")

    def test_case_insensitive(self):
        assert _has_url_cta("GO TO ACME.COM")

    def test_plain_editorial_text_is_false(self):
        assert not _has_url_cta("the meeting is at noon today")
        assert not _has_url_cta("she said i.e. the obvious thing")


# ---------------------------------------------------------------------------
# postprocess_spans (integration: merge THEN extend)
# ---------------------------------------------------------------------------

class TestPostprocessSpans:
    def test_empty(self):
        assert postprocess_spans([], []) == []

    def test_merge_then_extend_pipeline(self):
        # two nearby spans merge into [0-20000], then a CTA segment extends end
        spans = [_span(0, 10_000), _span(12_000, 20_000)]
        segs = [Seg(20_000, 25_000, "Go to acme.com to learn more")]
        out = postprocess_spans(spans, segs)
        assert len(out) == 1
        assert (out[0]["start_ms"], out[0]["end_ms"]) == (0, 25_000)

    def test_merge_runs_before_extend(self):
        # Order-dependent case. Spans A[0-10000] and B[31000-40000] are 21s
        # apart, so they do NOT merge. A CTA segment at 12000 extends A's end
        # to 30000 — which lands within 1s of B. Because merge runs FIRST
        # (before A was extended), A and B stay separate. If extend ran first,
        # the extended A would then merge with B into a single span.
        spans = [_span(0, 10_000), _span(31_000, 40_000)]
        segs = [Seg(12_000, 30_000, "go to acme.com")]
        out = postprocess_spans(spans, segs)
        assert len(out) == 2
        assert (out[0]["start_ms"], out[0]["end_ms"]) == (0, 30_000)
        assert (out[1]["start_ms"], out[1]["end_ms"]) == (31_000, 40_000)

    def test_no_changes_when_nothing_applies(self):
        spans = [_span(0, 10_000), _span(100_000, 110_000)]
        segs = [Seg(10_500, 12_000, "ordinary editorial content")]
        out = postprocess_spans(spans, segs)
        assert len(out) == 2
        assert out[0]["end_ms"] == 10_000
        assert out[1]["end_ms"] == 110_000
