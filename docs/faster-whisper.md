# Transcription: faster-whisper

Transcription uses [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
(CTranslate2) rather than the reference `openai-whisper` package. Same Whisper
`base` weights, different runtime.

## Why

This runs on a repurposed laptop (i7-1065G7, 4c/8t, no discrete GPU) with no
thermal governor — `thermald` is disabled because removing the screen broke the
sensors it relied on. Transcription is ~85% of pipeline wall time and
effectively all of its heat, so the runtime choice is a thermal decision as
much as a speed one.

## Measured

One real episode (`global-news-podcast-bangkok_bar_fire_kills_at_least_27`,
1972s / 33min, 64kbps), `base` model, `WHISPER_THREADS=2`, turbo disabled:

| | openai-whisper 20250625 | faster-whisper 1.2.1 (int8, greedy) |
|---|---|---|
| Transcribe | 342.1s | **162.4s** |
| Realtime factor | 5.77x | **12.14x** |
| Peak package temp | 53.0C | **50.0C** |
| Mean package temp | 44.9C | 44.0C |
| Time at >=85C | 0s | 0s |
| Segments | 321 | 359 |

**2.11x faster and 3C cooler.** A 1-hour episode goes from ~10min of Whisper to
~5min.

Accuracy against the cached openai-whisper transcript for byte-identical audio
(sha256-verified): **95.9% word-level similarity**, 0 degenerate segments, 0
empty segments, coverage to 1965.0s of 1972.2s.

Note that openai-whisper is not deterministic: two runs over the same audio
produced 301 and 321 segments (29451 vs 30106 characters). Part of any
engine-to-engine transcript difference is that sampling noise, not the engine.
The meaningful regression check is the `--run-llm` suite, which asserts on
detected ad spans rather than transcript text.

## Thermals are about turbo, not threads

`WHISPER_THREADS=2` was originally added for thermal safety, but it is not what
controls peak temperature. With turbo enabled, any sustained 2-thread load
drives the package to 90-99C within ~10-25s; an openai-whisper benchmark run
aborted at 99C after 13 seconds. With turbo disabled, the same work peaks in
the low 50s.

Turbo is held off persistently via `/etc/tmpfiles.d/no-turbo.conf`
(`w /sys/devices/system/cpu/intel_pstate/no_turbo - - - - 1`). **If that file is
removed, transcription will run hot again** and no automatic governor will
intervene.

## Settings and why

```python
WhisperModel("base", device="cpu", compute_type="int8", cpu_threads=WHISPER_THREADS)
model.transcribe(path, language="en", beam_size=1, word_timestamps=True)
```

- `compute_type="int8"` — quantized inference; the main speedup on CPU.
- `beam_size=1` (greedy) — the library default is 5. Beam search cost ~50% more
  time in benchmarking for no measured gain in ad detection.
- **No `initial_prompt`** and **`word_timestamps=True`** — see below.
- `cpu_threads` replaces the old `torch.set_num_threads()` call.
- VAD (`vad_filter`) is left off. It would drop silence and shift segment
  timings, which the ad-span cutting depends on.
- `transcribe()` returns a lazy generator — the work happens as segments are
  consumed, not when the call returns.

## Segment boundaries: no prompt, word timestamps

Ad cutting works on segment boundaries, so segment *shape* matters as much as
transcript text. Two problems showed up when the first version of this change
passed the old `initial_prompt` ("This podcast may contain advertisements...")
through to faster-whisper:

1. **The prompt can suppress timestamp tokens.** The decoder then emits the
   whole 30s window as one segment, and text after it gets misaligned. On the
   Ezra Klein / Chris Rufo opening, the first 75s came back as three blobs
   (0–30, 30–59, 59–75s), with the ad's brand name spliced into the interview
   ("Amaloid is the most successful activist..."). On the 33-minute BBC
   episode, the opening ad block collapsed into 0–30s and 30–57s.
2. **Without the prompt, segment ends can still snap to the window edge.** On
   the Ezra opening, "Visit amyloid.com to learn more." ran 28.1–57.2s, which
   swallowed ~27s of intro music (about −20 dBFS throughout) that openai-whisper
   had left outside every segment. An ad cut would have removed the music.
   `vad_filter=True` did not help: it treats the music as possible speech.

`word_timestamps=True` fixes both. Each segment ends at its last word, so the
ad ends at 29.8s and the interview starts at 56.5s, with the music in neither.
The text is also cleaner than openai-whisper's on this clip ("amyloid", not
"Amaloid"; "Chris Rufo is the most successful activist").

Cost on the same 33-minute BBC episode (2 threads, turbo off):

| | prompt, no word timestamps | **no prompt, word timestamps** |
|---|---|---|
| Transcribe | 161.9s | **173.3s** (+7%) |
| Segments | 320 | 295 |
| Segments >= 25s | 3 (two are the opening ad) | 1 (continuous news speech) |
| Peak package temp | 50C | 48C |

Still ~2x faster than openai-whisper (342.1s).

The `--run-llm` tests can't catch this class of problem on their own: they
read saved transcripts, so they never exercise the transcription engine. To
check a transcription change, re-transcribe a test episode's audio first and
then run the suite against that output.

## Dependency notes

Dropping `openai-whisper` also removed `torch`, `triton` and 14 `nvidia-cuda-*`
packages: ~3GB of CUDA libraries that could never be used on this hardware.

Two version constraints came with the swap:

- `requires-python` is `>=3.10`. It previously claimed `>=3.8`, which was never
  true (`thermal.py` uses `float | None`), and faster-whisper requires `>=3.9`.
- `onnxruntime` is held `<1.24` in `[tool.uv] constraint-dependencies`.
  faster-whisper pulls it in for the Silero VAD we don't enable, and 1.24
  dropped cp310 wheels. **This pin can be removed once the box runs Python
  3.11+.**
