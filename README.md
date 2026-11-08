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

Each `pipeline.py` run walks five stages, and names the one it is in when it
alerts or writes the run log:

1. **download** — fetches new episodes from every feed in `upstream.opml`. After
   downtime only the last 7 days are fetched; older unseen entries are marked
   seen without downloading (`CATCH_UP_WINDOW_DAYS`)
2. **process** — transcribes with faster-whisper, sends ~10-minute transcript
   windows to an LLM to identify ad spans, then cuts and re-encodes with pydub
3. **cleanup** — enforces the storage budget (see [Storage](#storage))
4. **feeds** — regenerates an RSS feed per subscription
5. **publish** — mirrors feeds and audio to Cloudflare R2 when `PUBLISH_TO_R2=true`

Serving is continuous rather than a stage: the Flask server exposes ad-free MP3s
and the regenerated feeds behind HTTP Basic Auth.

## Setup

### Requirements

- Python 3.10+, [uv](https://github.com/astral-sh/uv)
- ffmpeg (`apt install ffmpeg`)
- Tailscale (for tailnet HTTPS access via `tailscale serve`)

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
| `SERVER_BASE_URL` | HTTPS base URL for this server (e.g. its `tailscale serve` URL) |
| `FEED_USERNAME` / `FEED_PASSWORD` | HTTP Basic Auth credentials for the feed server and the `/dashboard` web UI |
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

For local development:

```bash
uv run python server.py
```

In production the app is served by **gunicorn**, which imports `server:app`
and never runs that block:

```bash
uv run gunicorn --workers 2 --threads 4 --bind 127.0.0.1:5000 --timeout 60 server:app
```

`systemd/skipcastify-server.service` runs exactly that. gunicorn's access log
is off because the legacy `/episodes/<token>/` routes carry `EPISODE_TOKEN`;
`server.py` logs each request itself with secrets scrubbed:

```
INFO in server: GET /episodes/<EPISODE_TOKEN>/show/ep.mp3 -> 404 0.7ms
INFO in server: GET /dashboard -> 200 5.5ms user=bwebb
```

The server listens on **loopback only** and is reached over the tailnet via
`tailscale serve`, which terminates TLS:

```bash
sudo tailscale serve --bg --https=443 http://127.0.0.1:5000
```

Feeds served from here need `FEED_USERNAME` / `FEED_PASSWORD`:

```
https://<your-tailscale-host>/feeds/<podcast-slug>.xml
```

Overcast cannot reach a tailnet, so feeds for podcast apps are published to
Cloudflare R2 instead — see [docs/r2-publishing.md](docs/r2-publishing.md).
Tailscale Funnel is deliberately **not** used: it would accept inbound traffic
from the public internet onto this box.

## Usage

### Automated pipeline (systemd timer)

One run by hand:

```bash
uv run python pipeline.py
```

In production a systemd timer drives it — 5 minutes after boot, then every 15
minutes, as a `oneshot` service at `Nice=10`:

```bash
sudo cp systemd/skipcastify-pipeline.service systemd/skipcastify-pipeline.timer /etc/systemd/system/
sudo systemctl enable --now skipcastify-pipeline.timer
systemctl list-timers skipcastify-pipeline.timer
journalctl -u skipcastify-pipeline.service -n 50
```

The timer is deliberately **not** `Persistent`: after the box has been asleep
the next scheduled run is wanted, not an immediate catch-up burst of
transcription. Cron is not used — run one or the other, never both.

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

### Process an episode ad-hoc (into production)

To transcribe or fully process specific episodes now, without waiting for the cron run:

```bash
# Transcribe only, caching the transcript to data/transcripts/
uv run python scripts/transcribe_episode.py path/to/episode.mp3 --feed <slug>

# Full process (transcribe + LLM + stitch) into data/podcasts/processed/
uv run python scripts/process_episode.py path/to/episode.mp3 --feed <slug>
```

Transcripts are cached and keyed by a hash of the source audio, so re-runs skip Whisper unless the audio changed (`--force` to override on `transcribe_episode.py`).

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

Transcription uses [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (`base` model, int8, `WHISPER_THREADS` CPU threads); see [docs/faster-whisper.md](docs/faster-whisper.md). The transcript is split into ~10-minute overlapping windows, each sent to the configured LLM.

The LLM prompt is in `skipcastify/llm_prompts/identify_ads_prompt.txt` and can be edited independently of the code. It instructs the model to return a JSON array of ad spans. Segments classified as `ADVERTISEMENT` or `SPONSOR` are removed; `INTRO` and `OUTRO` are kept.

LLM call profiles (latency, cost, spans found) are saved to `data/llm_profiles/` after each episode.

## Storage

Raw episodes live in `data/podcasts/raw/<slug>/`. Processed episodes go to `data/podcasts/processed/<slug>/`. The pipeline enforces a storage budget:

- When total audio exceeds `STORAGE_HIGH_WATER_GB`, the oldest files are deleted until usage drops below `STORAGE_LOW_WATER_GB`
- Raw files are deleted first when a processed counterpart exists
- Raw audio of hand-labeled episodes is never deleted: the spans in
  `data/labels/ground_truth/` are timed to that exact file
- Files under `data/samples/` are excluded from the retention policy

## Testing

```bash
# Standard tests (no LLM calls, fast)
uv run pytest

# LLM prompt regression tests (makes real API calls, costs money)
uv run pytest --run-llm
```

The LLM tests run ad detection against committed transcript fixtures for several feeds (one file per feed under `tests/llm/`) and assert both recall (known ads detected) and precision (no false positives on editorial or ad-free content, e.g. the Wisdom of the Masters negative tests).
