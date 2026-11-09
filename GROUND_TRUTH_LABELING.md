# Ground-Truth Labeling & Evaluation System

This system lets you evaluate ad detection accuracy against manually-labeled ground-truth, and iterate quickly with cached transcriptions.

## Quick Start

### 1. Run preview (first time - transcribes)
```bash
python scripts/preview_episode.py "data/podcasts/raw/.../episode.mp3"
```

This will:
- Transcribe the episode (takes 4+ mins for long episodes)
- Run LLM ad detection
- Display predictions and full transcript
- Cache transcript for fast iteration

### 2. Annotate ads from transcript

Instead of listening to the whole episode, use the interactive annotation tool:

```bash
python scripts/annotate_ads.py <episode_name>
```

Example:
```bash
python scripts/annotate_ads.py philosophize-this-episode_237_the_stoics_are_wrong_-_nietzsche_schopenhauer
```

The tool will:
- Display the full cached transcript with segment numbers
- Let you interactively mark segments that are ads
- Save ground-truth labels to JSON

**Annotation input format:**
- Single segment: `15`
- Multiple segments: `15,16,17`
- Range: `15-20`
- Mixed: `15-20,25,30-35`

**Commands:**
- `done` - Finish and save annotations
- `show` - Display transcript again
- `clear` - Clear all selections
- `undo` - Remove last selection
- `help` - Show help

### 3. Evaluate predictions

```bash
python scripts/evaluate_ad_detection.py <episode_name>
```

This shows:
- Precision: what % of detected ads were actually ads?
- Recall: what % of real ads did we detect?
- F1: harmonic mean of precision and recall
- Detailed breakdown of TP/FP/FN

### 4. Iterate fast with cached transcription

After tweaking the LLM prompt or keywords, re-run without re-transcribing:

```bash
python scripts/preview_episode.py "data/podcasts/raw/.../episode.mp3" --use-cached
```

This uses the cached transcript and re-runs LLM + predictions in seconds.

To force re-transcription:
```bash
python scripts/preview_episode.py <episode> --force-transcribe
```

## Derived labels, for feeds that insert ads server-side

Hand labels decay on a feed with dynamic ad insertion: the ads are stitched in
per request, so labels timed to one download are wrong for the next. For those
feeds you can derive exact labels instead of annotating.

```bash
uv run python scripts/dai_ground_truth.py --feed the-ezra-klein-show --limit 3
uv run python scripts/dai_ground_truth.py --feed odd-lots --dry-run
```

The script fetches a second rendering of an episode already on disk, aligns the
two at the MP3 frame level, and writes the difference as labels. Insertion does
not re-encode the episode, so the renderings share their content frames exactly
and the ads are recoverable to about 26ms. It makes no LLM calls and no
transcription, so a labelled episode costs one download.

**The labels cover inserted ads only.** A sponsor the host reads aloud is part
of the master and appears in both renderings, so it is in neither the diff nor
the labels. They are written with `"source": "dai_diff"`, and `eval_ads.py`
reports them in their own group for that reason: averaged in with hand labels, a
recall figure would describe neither set.

The two metrics are not equally sound on a derived set. Recall is exact, since
every inserted ad is labelled. Precision is a floor: a host-read sponsor the
detector cut correctly counts against it, because the labels do not know the
sponsor is there.

Three outcomes other than success, all of them useful:

- *renderings are identical* — the feed inserts nothing, so it needs hand
  labels. Mormon Stories and the Dwarkesh Podcast are both like this.
- *below the match floor* — the host re-encodes on insertion rather than
  stitching frames, or the episode on disk predates a re-encode of the master.
  Recent episodes are the ones that align.
- *truncated reference* — the second rendering came back short, which would
  otherwise align as a prefix and read the missing tail as a single ad.

Feeds vary in how they insert. Some leave the slot empty and add to it; others
leave a fixed-length house spot and swap a paid ad in, so the reference is not
necessarily ad-free. Either way the labels mark what the episode on disk carries
and the reference does not.

## File Locations

```
data/
├── labels/
│   └── ground_truth/
│       └── <episode_name>_labels.json       # Ground-truth labels you create
├── transcripts/
│   └── <episode_name>_transcript.json       # Auto-cached transcriptions
└── podcasts/
    └── processed_preview/
        ├── <episode_name>_preview.mp3       # Ad-removed audio
        ├── <episode_name>_preview_transcript.txt  # Full transcript with markers
        └── <episode_name>_predictions.json  # LLM ad span predictions
```

## Annotation Format (JSON)

Ground-truth labels are saved as JSON with this format:

```json
{
  "episode_name": "episode_name_here",
  "ads": [
    {
      "start_ms": 871760,
      "end_ms": 875520,
      "label": "ADVERTISEMENT"
    },
    {
      "start_ms": 909920,
      "end_ms": 916880,
      "label": "SPONSOR"
    }
  ]
}
```

Each ad entry must have:
- `start_ms`: Start time in milliseconds
- `end_ms`: End time in milliseconds  
- `label`: `"ADVERTISEMENT"`, `"SPONSOR"`, or `"CONTENT"` (for marking false positives)
- `description`: Optional short description (not required)

## Metrics Explained

- **Precision**: `TP / (TP + FP)` — of detected ads, how many were correct?
  - High precision = few false alarms
  - Formula: (# correct detections) / (# total detections)

- **Recall**: `TP / (TP + FN)` — of actual ads, how many did we find?
  - High recall = few missed ads
  - Formula: (# found) / (# total real ads)

- **F1**: Harmonic mean — balances precision and recall
  - Formula: 2 × (precision × recall) / (precision + recall)
  - Good F1 means balanced detection

- **True Positives (TP)**: Detected ads that match ground-truth (overlap >= 1000ms)
- **False Positives (FP)**: Detected ads that don't match any ground-truth
- **False Negatives (FN)**: Ground-truth ads that weren't detected

## Tips

- **Use segment indices, not timestamps** — The annotation tool uses Whisper segment numbers (0-indexed), which are shown in the transcript display
- **Description is optional** — You don't need to add descriptions to ad labels
- **Fast iteration cycle** — Transcribe once, annotate, evaluate, tweak prompt, re-run (cached), evaluate again
- **Overlap threshold** — A detected ad matches ground-truth if they overlap by >= 1000ms (1 second)
