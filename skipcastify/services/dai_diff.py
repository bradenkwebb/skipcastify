"""Ad spans recovered by diffing two renderings of one episode.

Server-side ad insertion stitches pre-encoded MP3 frames into an episode rather
than re-encoding it, so a rendering served without ads is a frame-exact
subsequence of one served with them. Aligning the two recovers every inserted
span to the frame, which is roughly 26ms at 44.1kHz.

The spans are in the timeline of the rendering that carries the ads, which is
the file the pipeline downloads and transcribes.
"""

import difflib
import hashlib

# MPEG audio frame header tables, indexed as the header stores them.
_BITRATES_V1 = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0)
_BITRATES_V2 = (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0)
_SAMPLE_RATES = (44100, 48000, 32000)
_LAYER_III = 1

# Below this share of matching frames the two files are not the same encode of
# the same content, and their differences are not ad insertions.
FRAME_MATCH_FLOOR = 0.99

# An insertion this short is an encoder artifact, such as the Xing header frame
# that differs because it records the file's own length.
MIN_SPAN_MS = 1000.0


class NotTheSameEncode(Exception):
    """The two renderings do not share enough frames to be aligned."""


def read_frames(data: bytes) -> list[tuple[bytes, float]]:
    """Per-frame (digest, duration in ms) for an MPEG audio file."""
    offset = 0
    if data[:3] == b"ID3" and len(data) >= 10:
        size = (data[6] << 21) | (data[7] << 14) | (data[8] << 7) | data[9]
        offset = 10 + size

    frames = []
    while offset + 4 <= len(data):
        if data[offset] != 0xFF or (data[offset + 1] & 0xE0) != 0xE0:
            offset += 1
            continue

        header = data[offset + 1:offset + 3]
        is_v1 = (header[0] >> 3) & 1
        layer = (header[0] >> 1) & 3
        bitrate_index = (header[1] >> 4) & 0xF
        rate_index = (header[1] >> 2) & 3
        padding = (header[1] >> 1) & 1

        if layer != _LAYER_III or bitrate_index in (0, 0xF) or rate_index == 3:
            offset += 1
            continue

        bitrate = (_BITRATES_V1 if is_v1 else _BITRATES_V2)[bitrate_index] * 1000
        sample_rate = _SAMPLE_RATES[rate_index] >> (0 if is_v1 else 1)
        samples = 1152 if is_v1 else 576
        size = (samples // 8) * bitrate // sample_rate + padding
        if size <= 4 or offset + size > len(data):
            break

        frames.append((hashlib.blake2b(data[offset:offset + size], digest_size=8).digest(),
                       samples / sample_rate * 1000))
        offset += size

    return frames


def duration_ms(frames: list[tuple[bytes, float]]) -> float:
    return sum(length for _, length in frames)


def inserted_spans(with_ads: list[tuple[bytes, float]],
                   without_ads: list[tuple[bytes, float]],
                   min_span_ms: float = MIN_SPAN_MS) -> list[dict]:
    """Spans present in one rendering and absent from the other.

    Raises NotTheSameEncode when too few frames match for the difference to
    mean anything.
    """
    if not with_ads or not without_ads:
        raise NotTheSameEncode("one rendering has no audio frames")

    reference = [digest for digest, _ in without_ads]
    monetized = [digest for digest, _ in with_ads]
    blocks = difflib.SequenceMatcher(None, reference, monetized,
                                     autojunk=False).get_matching_blocks()
    matched = sum(size for _, _, size in blocks)
    ratio = matched / len(reference)
    if ratio < FRAME_MATCH_FLOOR:
        raise NotTheSameEncode(
            f"only {ratio:.1%} of the reference frames appear in the other "
            f"rendering; expected at least {FRAME_MATCH_FLOOR:.0%}")

    starts = []
    elapsed = 0.0
    for _, length in with_ads:
        starts.append(elapsed)
        elapsed += length
    starts.append(elapsed)

    spans = []
    cursor = 0
    for _, begin, size in blocks:
        if begin > cursor and starts[begin] - starts[cursor] >= min_span_ms:
            spans.append({"start_ms": round(starts[cursor]),
                          "end_ms": round(starts[begin]),
                          "label": "ad"})
        cursor = begin + size
    return spans


def match_ratio(with_ads: list[tuple[bytes, float]],
                without_ads: list[tuple[bytes, float]]) -> float:
    """Share of reference frames found in the other rendering."""
    reference = [digest for digest, _ in without_ads]
    monetized = [digest for digest, _ in with_ads]
    if not reference:
        return 0.0
    blocks = difflib.SequenceMatcher(None, reference, monetized,
                                     autojunk=False).get_matching_blocks()
    return sum(size for _, _, size in blocks) / len(reference)
