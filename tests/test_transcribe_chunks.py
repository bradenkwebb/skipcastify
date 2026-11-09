import numpy as np

from skipcastify.services.audio_processor import SAMPLE_RATE, chunk_bounds


def test_short_audio_is_one_chunk():
    audio = np.ones(SAMPLE_RATE * 60, dtype="float32")
    assert chunk_bounds(audio, chunk_s=600) == [(0, len(audio))]


def test_chunks_cover_the_audio_without_gaps():
    audio = np.random.default_rng(0).standard_normal(SAMPLE_RATE * 3500).astype("float32")
    bounds = chunk_bounds(audio, chunk_s=600, snap_s=10)
    assert bounds[0][0] == 0 and bounds[-1][1] == len(audio)
    assert all(a[1] == b[0] for a, b in zip(bounds, bounds[1:]))
    assert all(abs(e - s - 600 * SAMPLE_RATE) <= 20 * SAMPLE_RATE for s, e in bounds[:-1])


def test_split_snaps_to_the_quietest_point():
    audio = np.ones(SAMPLE_RATE * 1300, dtype="float32")
    quiet = 603 * SAMPLE_RATE
    audio[quiet:quiet + SAMPLE_RATE // 10] = 0
    (_, cut), *_ = chunk_bounds(audio, chunk_s=600, snap_s=10)
    assert quiet <= cut < quiet + SAMPLE_RATE // 10
