"""Aligning two renderings of one episode recovers the inserted ad spans."""

import pytest

from skipcastify.services import dai_diff

FRAME_MS = 1152 / 44100 * 1000
FRAME_BYTES = 417
_HEADER = b"\xff\xfb\x90\x00"


def frame(tag: int) -> bytes:
    """One 128kbps 44.1kHz MPEG1 Layer III frame, its payload tagged uniquely."""
    payload = (tag.to_bytes(4, "big") * FRAME_BYTES)[:FRAME_BYTES - len(_HEADER)]
    return _HEADER + payload


def mp3(tags) -> bytes:
    return b"".join(frame(t) for t in tags)


def id3(size: int) -> bytes:
    syncsafe = bytes([(size >> 21) & 0x7F, (size >> 14) & 0x7F,
                      (size >> 7) & 0x7F, size & 0x7F])
    return b"ID3\x04\x00\x00" + syncsafe + b"\x00" * size


class TestReadFrames:
    def test_every_frame_is_found(self):
        frames = dai_diff.read_frames(mp3(range(50)))
        assert len(frames) == 50

    def test_frame_duration_is_reported(self):
        frames = dai_diff.read_frames(mp3(range(10)))
        assert dai_diff.duration_ms(frames) == pytest.approx(10 * FRAME_MS)

    def test_distinct_payloads_get_distinct_digests(self):
        digests = [digest for digest, _ in dai_diff.read_frames(mp3(range(20)))]
        assert len(set(digests)) == 20

    def test_an_id3_header_is_skipped(self):
        frames = dai_diff.read_frames(id3(1234) + mp3(range(5)))
        assert len(frames) == 5
        assert frames == dai_diff.read_frames(mp3(range(5)))

    def test_a_truncated_trailing_frame_is_dropped(self):
        frames = dai_diff.read_frames(mp3(range(5)) + _HEADER + b"\x00" * 10)
        assert len(frames) == 5


class TestInsertedSpans:
    def _spans(self, with_tags, without_tags, **kw):
        return dai_diff.inserted_spans(dai_diff.read_frames(mp3(with_tags)),
                                       dai_diff.read_frames(mp3(without_tags)), **kw)

    def test_identical_renderings_have_no_spans(self):
        assert self._spans(range(100), range(100)) == []

    def test_a_leading_insertion_starts_at_zero(self):
        spans = self._spans(list(range(900, 980)) + list(range(100)), range(100))
        assert len(spans) == 1
        assert spans[0]["start_ms"] == 0
        assert spans[0]["end_ms"] == round(80 * FRAME_MS)
        assert spans[0]["label"] == "ad"

    def test_a_mid_roll_insertion_uses_the_monetized_timeline(self):
        with_ads = list(range(40)) + list(range(900, 980)) + list(range(40, 100))
        spans = self._spans(with_ads, range(100))
        assert len(spans) == 1
        assert spans[0]["start_ms"] == round(40 * FRAME_MS)
        assert spans[0]["end_ms"] == round(120 * FRAME_MS)

    def test_several_insertions_come_back_in_order(self):
        with_ads = (list(range(20)) + list(range(900, 980))
                    + list(range(20, 60)) + list(range(980, 1060))
                    + list(range(60, 100)))
        spans = self._spans(with_ads, range(100))
        assert len(spans) == 2
        assert [s["start_ms"] for s in spans] == sorted(s["start_ms"] for s in spans)
        assert spans[0]["end_ms"] <= spans[1]["start_ms"]

    def test_the_spans_account_for_the_whole_difference(self):
        with_ads = list(range(30)) + list(range(900, 1000)) + list(range(30, 100))
        spans = self._spans(with_ads, range(100))
        assert sum(s["end_ms"] - s["start_ms"] for s in spans) == pytest.approx(
            100 * FRAME_MS, abs=1)

    def test_an_insertion_below_the_floor_is_ignored(self):
        """A one-frame difference is an encoder artifact, not an ad."""
        with_ads = list(range(40)) + [999] + list(range(40, 100))
        assert self._spans(with_ads, range(100)) == []

    def test_the_floor_is_adjustable(self):
        with_ads = list(range(40)) + [999] + list(range(40, 100))
        assert len(self._spans(with_ads, range(100), min_span_ms=0)) == 1

    def test_unrelated_renderings_are_refused(self):
        with pytest.raises(dai_diff.NotTheSameEncode):
            self._spans(range(200, 300), range(100))

    def test_an_empty_rendering_is_refused(self):
        with pytest.raises(dai_diff.NotTheSameEncode):
            dai_diff.inserted_spans([], dai_diff.read_frames(mp3(range(10))))

    def test_a_reference_longer_than_the_monetized_copy_is_refused(self):
        """Ads are only ever added, so the reference cannot hold extra content."""
        with pytest.raises(dai_diff.NotTheSameEncode):
            self._spans(range(10), range(100))


class TestMatchRatio:
    def test_a_subsequence_matches_completely(self):
        with_ads = dai_diff.read_frames(mp3(list(range(900, 950)) + list(range(100))))
        without = dai_diff.read_frames(mp3(range(100)))
        assert dai_diff.match_ratio(with_ads, without) == 1.0

    def test_unrelated_renderings_match_poorly(self):
        a = dai_diff.read_frames(mp3(range(200, 300)))
        b = dai_diff.read_frames(mp3(range(100)))
        assert dai_diff.match_ratio(a, b) < dai_diff.FRAME_MATCH_FLOOR
