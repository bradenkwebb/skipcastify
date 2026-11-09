"""Ad spans recovered by diffing two renderings of one episode.

Server-side ad insertion stitches pre-encoded MP3 frames into the episode
rather than re-encoding it, so two renderings of one episode share their
content frames exactly and differ only where the ads sit. Aligning them
recovers every inserted span to about 26ms.

Some feeds insert into empty space and some replace a fixed-length house spot,
so a rendering may both gain and lose frames. The ads are what the monetized
rendering gained; what it lost belongs to the reference and never appears in
the episode on disk.

Spans are in the timeline of the rendering that carries the ads, which is the
file the pipeline downloads and transcribes.
"""

import difflib
import hashlib
from dataclasses import dataclass, field

# MPEG audio frame header tables, indexed as the header stores them.
_BITRATES_V1 = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0)
_BITRATES_V2 = (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0)
_SAMPLE_RATES = (44100, 48000, 32000)
_LAYER_III = 1

# The header's version field: 0 is MPEG 2.5, 1 is reserved, 2 is MPEG 2, 3 is
# MPEG 1. Each halves the sample rate of the one above it.
_VERSION_RESERVED = 1
_VERSION_1 = 3
_RATE_SHIFT = {0: 2, 2: 1, 3: 0}

# Re-encoding changes every frame, so two renderings of one episode that share
# less than this are not the same encode and their diff means nothing.
MATCH_FLOOR = 0.80

# Ad breaks are few and contiguous. Many scattered runs mean the renderings
# differ for some reason other than ad placement.
MAX_BREAKS = 24

# A difference this short is an encoder artifact, such as the Xing header frame
# that records the file's own length.
MIN_SPAN_MS = 1000.0


class NotComparable(Exception):
    """The two renderings cannot be read as the same episode."""


@dataclass
class Alignment:
    inserted: list[dict] = field(default_factory=list)
    replaced: list[dict] = field(default_factory=list)
    match_ratio: float = 0.0

    @property
    def inserted_ms(self) -> int:
        return sum(s["end_ms"] - s["start_ms"] for s in self.inserted)


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
        version = (header[0] >> 3) & 3
        layer = (header[0] >> 1) & 3
        bitrate_index = (header[1] >> 4) & 0xF
        rate_index = (header[1] >> 2) & 3
        padding = (header[1] >> 1) & 1

        if layer != _LAYER_III or version == _VERSION_RESERVED \
                or bitrate_index in (0, 0xF) or rate_index == 3:
            offset += 1
            continue

        is_v1 = version == _VERSION_1
        bitrate = (_BITRATES_V1 if is_v1 else _BITRATES_V2)[bitrate_index] * 1000
        sample_rate = _SAMPLE_RATES[rate_index] >> _RATE_SHIFT[version]
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


def _starts(frames: list[tuple[bytes, float]]) -> list[float]:
    """Start time of each frame, plus the end of the last one."""
    out, elapsed = [], 0.0
    for _, length in frames:
        out.append(elapsed)
        elapsed += length
    out.append(elapsed)
    return out


def _runs(starts: list[float], gaps: list[tuple[int, int]], min_span_ms: float,
          label: str | None) -> list[dict]:
    spans = []
    for begin, end in gaps:
        if starts[end] - starts[begin] < min_span_ms:
            continue
        span = {"start_ms": round(starts[begin]), "end_ms": round(starts[end])}
        if label:
            span["label"] = label
        spans.append(span)
    return spans


def align(with_ads: list[tuple[bytes, float]], without_ads: list[tuple[bytes, float]],
          min_span_ms: float = MIN_SPAN_MS, max_breaks: int = MAX_BREAKS) -> Alignment:
    """What one rendering carries and the other does not.

    Raises NotComparable when the two cannot be read as renderings of the same
    episode, which a bad download or a re-encode both cause.
    """
    if not with_ads or not without_ads:
        raise NotComparable("a rendering has no audio frames")

    reference = [digest for digest, _ in without_ads]
    monetized = [digest for digest, _ in with_ads]
    blocks = difflib.SequenceMatcher(None, reference, monetized,
                                     autojunk=False).get_matching_blocks()
    ratio = sum(size for _, _, size in blocks) / len(reference)
    if ratio < MATCH_FLOOR:
        raise NotComparable(
            f"the renderings share {ratio:.1%} of their frames, below the "
            f"{MATCH_FLOOR:.0%} that means the same encode")

    added, dropped = [], []
    ref_cursor = mon_cursor = 0
    for ref_at, mon_at, size in blocks:
        if mon_at > mon_cursor:
            added.append((mon_cursor, mon_at))
        if ref_at > ref_cursor:
            dropped.append((ref_cursor, ref_at))
        ref_cursor, mon_cursor = ref_at + size, mon_at + size

    if max(len(added), len(dropped)) > max_breaks:
        raise NotComparable(
            f"the renderings differ in {max(len(added), len(dropped))} places, "
            f"more than the {max_breaks} that ad breaks would explain")

    return Alignment(
        inserted=_runs(_starts(with_ads), added, min_span_ms, "ad"),
        replaced=_runs(_starts(without_ads), dropped, min_span_ms, None),
        match_ratio=ratio,
    )
