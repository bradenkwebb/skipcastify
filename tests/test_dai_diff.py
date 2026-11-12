"""Aligning two renderings of one episode recovers the inserted ad spans."""

import pytest

from skipcastify.services import dai_diff

FRAME_MS = 1152 / 44100 * 1000
FRAME_BYTES = 417
_HEADER = b"\xff\xfb\x90\x00"
_LAYER_III_BITS = 0b01


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


def align(with_tags, without_tags, **kw) -> dai_diff.Alignment:
    return dai_diff.align(dai_diff.read_frames(mp3(with_tags)),
                          dai_diff.read_frames(mp3(without_tags)), **kw)


CONTENT = range(400)


class TestReadFrames:
    def test_every_frame_is_found(self):
        assert len(dai_diff.read_frames(mp3(range(50)))) == 50

    def test_frame_duration_is_reported(self):
        frames = dai_diff.read_frames(mp3(range(10)))
        assert dai_diff.duration_ms(frames) == pytest.approx(10 * FRAME_MS)

    def test_distinct_payloads_get_distinct_digests(self):
        digests = [digest for digest, _ in dai_diff.read_frames(mp3(range(20)))]
        assert len(set(digests)) == 20

    def test_an_id3_header_is_skipped(self):
        assert dai_diff.read_frames(id3(1234) + mp3(range(5))) == \
            dai_diff.read_frames(mp3(range(5)))

    def test_a_truncated_trailing_frame_is_dropped(self):
        assert len(dai_diff.read_frames(mp3(range(5)) + _HEADER + b"\x00" * 10)) == 5


class TestInsertion:
    """A feed that inserts ads into empty space: the reference loses nothing."""

    def test_identical_renderings_have_no_ads(self):
        result = align(CONTENT, CONTENT)
        assert result.inserted == [] and result.replaced == []
        assert result.match_ratio == 1.0

    def test_a_leading_insertion_starts_at_zero(self):
        result = align(list(range(900, 980)) + list(CONTENT), CONTENT)
        assert len(result.inserted) == 1
        assert result.inserted[0]["start_ms"] == 0
        assert result.inserted[0]["end_ms"] == round(80 * FRAME_MS)
        assert result.inserted[0]["label"] == "ad"
        assert result.replaced == []

    def test_a_mid_roll_uses_the_monetized_timeline(self):
        with_ads = list(range(40)) + list(range(900, 980)) + list(range(40, 400))
        result = align(with_ads, CONTENT)
        assert len(result.inserted) == 1
        assert result.inserted[0]["start_ms"] == round(40 * FRAME_MS)
        assert result.inserted[0]["end_ms"] == round(120 * FRAME_MS)

    def test_several_breaks_come_back_in_order(self):
        with_ads = (list(range(20)) + list(range(900, 980))
                    + list(range(20, 60)) + list(range(980, 1060))
                    + list(range(60, 400)))
        starts = [s["start_ms"] for s in align(with_ads, CONTENT).inserted]
        assert len(starts) == 2 and starts == sorted(starts)

    def test_the_spans_account_for_the_whole_difference(self):
        with_ads = list(range(30)) + list(range(900, 1000)) + list(range(30, 400))
        assert align(with_ads, CONTENT).inserted_ms == pytest.approx(100 * FRAME_MS, abs=1)


class TestReplacement:
    """A feed that swaps a fixed-length house spot for a paid one."""

    def test_a_replaced_slot_reports_only_the_paid_ad_as_inserted(self):
        filler, paid = list(range(700, 740)), list(range(900, 980))
        reference = list(range(40)) + filler + list(range(40, 400))
        monetized = list(range(40)) + paid + list(range(40, 400))
        result = align(monetized, reference)
        assert len(result.inserted) == 1
        assert result.inserted[0]["start_ms"] == round(40 * FRAME_MS)
        assert result.inserted[0]["end_ms"] == round(120 * FRAME_MS)

    def test_the_replaced_run_is_reported_in_the_reference_timeline(self):
        filler, paid = list(range(700, 740)), list(range(900, 980))
        reference = list(range(40)) + filler + list(range(40, 400))
        monetized = list(range(40)) + paid + list(range(40, 400))
        result = align(monetized, reference)
        assert len(result.replaced) == 1
        assert result.replaced[0]["start_ms"] == round(40 * FRAME_MS)
        assert result.replaced[0]["end_ms"] == round(80 * FRAME_MS)
        assert "label" not in result.replaced[0]

    def test_an_unsold_slot_leaves_the_filler_unlabelled(self):
        """Nothing was inserted, so the episode on disk holds no ad there."""
        filler = list(range(700, 740))
        reference = list(range(40)) + filler + list(range(40, 400))
        result = align(list(CONTENT), reference)
        assert result.inserted == []
        assert len(result.replaced) == 1

    def test_replacement_does_not_drag_the_ratio_under_the_floor(self):
        filler, paid = list(range(700, 740)), list(range(900, 980))
        reference = list(range(40)) + filler + list(range(40, 400))
        monetized = list(range(40)) + paid + list(range(40, 400))
        assert align(monetized, reference).match_ratio >= dai_diff.MATCH_FLOOR


class TestArtifacts:
    def test_a_one_frame_difference_is_ignored(self):
        """The Xing header differs because it records the file's own length."""
        with_ads = list(range(40)) + [999] + list(range(40, 400))
        assert align(with_ads, CONTENT).inserted == []

    def test_the_floor_is_adjustable(self):
        with_ads = list(range(40)) + [999] + list(range(40, 400))
        assert len(align(with_ads, CONTENT, min_span_ms=0).inserted) == 1


class TestRefusals:
    def test_a_re_encode_shares_no_frames(self):
        with pytest.raises(dai_diff.NotComparable, match="same encode"):
            align(range(5000, 5400), CONTENT)

    def test_an_empty_rendering_is_refused(self):
        with pytest.raises(dai_diff.NotComparable):
            dai_diff.align([], dai_diff.read_frames(mp3(range(10))))

    def test_a_reference_longer_than_the_episode_is_refused(self):
        with pytest.raises(dai_diff.NotComparable):
            align(range(10), CONTENT)

    def test_scattered_differences_are_refused(self):
        """Interleaved mismatch is not ad placement, whatever its total."""
        monetized = []
        for n in range(400):
            monetized.append(n)
            if n % 4 == 0:
                monetized.append(5000 + n)
        with pytest.raises(dai_diff.NotComparable, match="differ in"):
            align(monetized, CONTENT, min_span_ms=0)

    def test_the_break_budget_is_adjustable(self):
        monetized = (list(range(20)) + list(range(900, 980)) + list(range(20, 60))
                     + list(range(980, 1060)) + list(range(60, 400)))
        with pytest.raises(dai_diff.NotComparable, match="differ in"):
            align(monetized, CONTENT, max_breaks=1)


class TestHeaderVersions:
    """Only MPEG 1 Layer III at 44.1kHz appears in these feeds, but a
    misparsed header would invent frame sizes and desynchronise the scan."""

    @staticmethod
    def _frame(version_bits: int, rate_index: int = 0, bitrate_index: int = 9) -> bytes:
        byte1 = 0b1110_0000 | (version_bits << 3) | (_LAYER_III_BITS << 1) | 1
        byte2 = (bitrate_index << 4) | (rate_index << 2)
        return bytes([0xFF, byte1, byte2, 0x00])

    def test_the_reserved_version_is_not_read_as_mpeg_1(self):
        assert dai_diff.read_frames(self._frame(0b01) + b"\x00" * 600) == []

    def test_mpeg_1_frames_are_417_bytes_at_128kbps(self):
        body = self._frame(0b11) + bytes(413)
        frames = dai_diff.read_frames(body * 3)
        assert len(frames) == 3
        assert frames[0][1] == pytest.approx(1152 / 44100 * 1000)

    def test_mpeg_2_halves_the_sample_rate(self):
        """80kbps over 22050Hz gives a 261-byte frame; a wrong rate desyncs."""
        frames = dai_diff.read_frames((self._frame(0b10) + bytes(257)) * 3)
        assert len(frames) == 3
        assert frames[0][1] == pytest.approx(576 / 22050 * 1000)

    def test_mpeg_2_5_quarters_the_sample_rate(self):
        """The version field's low bit alone cannot tell 2.5 from 2, and
        reading 2.5 as 2 halves every frame size from here on."""
        frames = dai_diff.read_frames((self._frame(0b00) + bytes(518)) * 3)
        assert len(frames) == 3
        assert frames[0][1] == pytest.approx(576 / 11025 * 1000)
