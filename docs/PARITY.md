# Client parity: web and iOS

Both clients are maintained together. They should offer the same concepts, under the same
names, with the same rules. When a user-facing feature changes, update this table in the
same PR. Either the other client gets it too, or the gap is listed below with why.

The server owns logic wherever possible; clients render it. Where a rule has to live in
both clients, it's called out in "Shared rules".

✓ = has it · — = deliberately not applicable · **gap** = should exist, doesn't yet

## Features

| Area | Feature | Web | iOS | Notes |
|---|---|---|---|---|
| Dashboard | Totals: ad minutes, episodes, shows, LLM spend | ✓ | ✓ | |
| | 7-day / 30-day / all-time range | ✓ | ✓ | `/api/stats.json?days=` |
| | Pipeline status, recent runs | ✓ | ✓ | |
| | Daily throughput, a day's episodes | ✓ | ✓ | web: click a bar; iOS: tap a bar |
| | Ad-heaviness leaderboard | ✓ | ✓ | |
| | Leaderboard opens the show | **gap** | ✓ | web has no show pages yet |
| | Drop candidates (ad-free streaks) | ✓ | **gap** | |
| Episodes | Recent processed episodes | ✓ | ✓ | |
| | Every transcribed episode, searchable | ✓ (`/label`) | ✓ | |
| Episode page | Timeline of cuts vs labels | ✓ | ✓ | |
| | Cuts with rationale and full transcript | ✓ | ✓ | |
| | Preview the raw audio around a cut | ✓ | ✓ | |
| | Play the episode (ad-free or original, labeled) | ✓ | ✓ | `playback` in `/api/episode/<name>.json` (#73) |
| | Show notes | ✓ | ✓ | |
| | Score against labels, processing runs | ✓ | ✓ | |
| Shows | A page per show (metadata, status, policy, ad pattern, episodes) | **gap** | ✓ | web shows ad patterns on the dashboard only |
| | Subscriptions with status and R2 usage | ✓ (`/settings`) | ✓ | |
| | Add a show: search, URL, Overcast export, preview | ✓ | ✓ | |
| | Unsubscribe | ✓ | ✓ | |
| | Cut policy: default and per show | ✓ | ✓ | |
| | Change log with Undo; skipped episodes with Retry | ✓ | ✓ | |
| Feeds | Published ad-free feed URLs, Add to… / Copy | ✓ (`/feeds`) | ✓ (Library) | |
| Labeling | Label spans, mark reviewed | ✓ (keyboard) | ✓ (touch) | iOS in #75 |
| | Start from the pipeline's cuts | ✓ | ✓ | |
| Listening | Built-in player, library, resume | — | ✓ | app-only by nature |
| Sign-in | Tailscale identity; Basic as fallback | ✓ | ✓ | |

## Shared rules

- **Label span editing** (take the silences around lines, split overlaps, merge same-label
  neighbours across silence, drop slivers) lives in `templates/label.html` and in
  `ios/Packages/SkipcastifyKit/Sources/SkipcastifyKit/Models/Labeling.swift`. Change both
  together. A shared set of test cases for both is a known gap.
- **Playback captions** ("Ad-free", "Original audio · nothing to cut", "Original audio · ads
  not removed") come from the same `playback` fields in both clients.

## Guards

- `client-contract` CI runs the iOS app's decoders (SkipcastifyKit's live test) against the
  real server on made-up data (`scripts/client_contract_data.py`) whenever the server or the
  package changes.
