# Skipcastify

Automatically removes ads from podcast episodes and serves ad-free feeds you can subscribe to in any podcast app.

## How it works

```
upstream OPML
      │
      ▼
┌─────────────┐     ┌──────────────┐     ┌─────────────────┐
│  Downloader │────▶│  Transcriber │────▶│  LLM Classifier │
│  (RSS/Atom) │     │  (Whisper)   │     │  (OpenAI/Ollama)│
└─────────────┘     └──────────────┘     └─────────────────┘
                                                  │
                                                  ▼
                                         ┌─────────────────┐
                                         │  Audio Stitcher │
                                         │  (pydub)        │
                                         └─────────────────┘
                                                  │
                          ┌───────────────────────┘
                          ▼
                 ┌─────────────────┐     ┌──────────────────┐
                 │  Feed Generator │────▶│  Flask Server    │
                 │  (feedgen)      │     │  (Basic Auth)    │
                 └─────────────────┘     └──────────────────┘
                                                  │
                                                  ▼
                                           Podcast app
                                           (e.g. Overcast)
```

1. **Download** — fetches new episodes from every feed in `upstream.opml`
2. **Transcribe** — runs Whisper locally to produce time-coded segments
3. **Classify** — sends 10-minute transcript windows to an LLM to identify ad spans
4. **Stitch** — cuts ad segments from the audio and re-encodes with pydub
5. **Serve** — Flask server exposes ad-free MP3s and regenerated RSS feeds behind HTTP Basic Auth

## Setup

### Requirements

- Python 3.10+, [uv](https://github.com/astral-sh/uv)
- ffmpeg (`apt install ffmpeg`)
- Tailscale (for public HTTPS access via Funnel)

```bash
git clone <repo>
cd skipcastify
uv sync
cp .env.example .env   # then edit
```

### Configuration

All runtime config lives in `.env` (never committed):

| Variable | Description |
|---|---|
| `DATA_DIR` | Root data directory (e.g. `data`) |
| `SUBSCRIPTIONS` | Path to upstream OPML file |
| `SERVER_BASE_URL` | Public HTTPS base URL (e.g. Tailscale Funnel URL) |
| `FEED_USERNAME` / `FEED_PASSWORD` | HTTP Basic Auth credentials for the feed server |
| `EPISODE_TOKEN` | Secret token appended to episode URLs |
| `EPISODE_LIMIT` | Max episodes to download per feed per run (default: 5) |
| `ENABLE_PROCESSING` | Set `true` to enable ad removal (default: `false`) |
| `PROCESS_SLUGS` | Comma-separated feed slugs to process; empty = all feeds |
| `LLM_PROVIDER` | `openai` or `ollama` (default: `ollama`) |
| `OPENAI_API_KEY` | Required if `LLM_PROVIDER=openai` |
| `OPENAI_MODEL` | OpenAI model to use (default: `gpt-4o-mini`) |
| `OLLAMA_MODEL` | Ollama model to use (default: `gemma4:e2b`) |
| `STORAGE_HIGH_WATER_GB` | Start deleting audio above this threshold (default: 75) |
| `STORAGE_LOW_WATER_GB` | Stop deleting once below this threshold (default: 50) |

### Subscriptions

Export your podcast subscriptions as OPML and save to `upstream.opml` (gitignored). The pipeline downloads from every feed listed there.

### Running the server

```bash
uv run python server.py
```

Expose it publicly with Tailscale Funnel:

```bash
tailscale funnel 5000
```

Then add your feeds in any podcast app as:
```
https://<your-tailscale-host>/feeds/<podcast-slug>.xml
```
with your `FEED_USERNAME` / `FEED_PASSWORD` credentials.

## Usage

### Automated pipeline (cron)

The pipeline downloads new episodes, processes them (if enabled), regenerates feeds, and enforces storage limits:

```bash
uv run python pipeline.py
```

Recommended cron (hourly):
```
0 * * * * cd /path/to/skipcastify && .venv/bin/python pipeline.py >> data/logs/cron.log 2>&1
```

### Preview a single episode

Test ad detection on one episode without touching production data:

```bash
# Full run (transcribe + LLM)
uv run python scripts/preview_episode.py path/to/episode.mp3

# Re-run LLM only (reuse cached transcript — much faster)
uv run python scripts/preview_episode.py path/to/episode.mp3 --use-cached

# Force re-transcription even if cached
uv run python scripts/preview_episode.py path/to/episode.mp3 --force-transcribe
```

Output goes to `data/podcasts/processed_preview/` and includes an annotated transcript showing exactly which segments were kept or removed.

### Regenerate feeds only

```bash
uv run python scripts/generate_feeds.py
```

### Enable processing for specific feeds

To process only one podcast (e.g. while validating):

```env
ENABLE_PROCESSING=true
PROCESS_SLUGS=global-news-podcast
```

Remove `PROCESS_SLUGS` (or leave it empty) to process all feeds.

## Ad detection

Transcription uses [OpenAI Whisper](https://github.com/openai/whisper) (`base` model by default). The transcript is split into ~10-minute overlapping windows, each sent to the configured LLM.

The LLM prompt is in `skipcastify/llm_prompts/identify_ads_prompt.txt` and can be edited independently of the code. It instructs the model to return a JSON array of ad spans. Segments classified as `ADVERTISEMENT` or `SPONSOR` are removed; `INTRO` and `OUTRO` are kept.

LLM call profiles (latency, cost, spans found) are saved to `data/llm_profiles/` after each episode.

## Storage

Raw episodes live in `data/podcasts/raw/<slug>/`. Processed episodes go to `data/podcasts/processed/<slug>/`. The pipeline enforces a storage budget:

- When total audio exceeds `STORAGE_HIGH_WATER_GB`, the oldest files are deleted until usage drops below `STORAGE_LOW_WATER_GB`
- Raw files are always deleted first when a processed counterpart exists
- Files under `data/samples/` are excluded from the retention policy

## Testing

```bash
# Standard tests (no LLM calls, fast)
uv run pytest

# LLM prompt regression tests (makes real API calls, costs money)
uv run pytest --run-llm
```

The LLM tests run the full ad-detection pipeline on a known BBC episode and assert both precision (no false positives on editorial content) and recall (all known ads detected).
