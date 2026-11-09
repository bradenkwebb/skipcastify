# Skipcastify iOS app v2: plan

Goal: a native companion to the Skipcastify server that covers what the web dashboard does
(dashboard, episode pages, shows/subscriptions, cut policy) and keeps the existing podcast player,
fed by the server's ad-free feeds instead of the placeholder "strip job" API.

Branch: `app-v2`. Nothing depends on the app yet, so phases land as commits on this branch.

## Principles

- **The server is the source of truth.** The app reads the server's existing JSON API and
  writes through the same endpoints `/settings` uses. No app-only state on the server.
- **Logic lives in a Swift package (`Packages/SkipcastifyKit`)**: models, API client,
  formatting. It has no UIKit/SwiftUI dependency, so `swift test` runs it on macOS in
  seconds, with no simulator. The app target is views plus thin observable stores.
- **Decode against real responses.** Test fixtures are trimmed copies of what the real server
  returned (captured from a local server on a snapshot of bw-server's data), so a server
  change that breaks decoding fails a test, not the app.
- **Tolerant decoding.** Most server fields are optional or vary (dates with and without
  zones, nulls for unknown titles). Models use optionals and a lenient date parser rather
  than failing a whole screen on one odd field.
- **Auth:** optional Basic auth (password in the Keychain). With Tailscale identity auth on
  the server (in progress in a separate session), requests through `tailscale serve` need no
  credentials at all, so "none" is a first-class option.
- **Writes** send `X-Skipcastify: 1` and a JSON body, which the server's CSRF guard requires.
  Policy and subscription writes send the version they loaded and handle 409 by reloading.

## Server API used (all exist today)

| Area | Endpoint |
|---|---|
| Dashboard | `GET /api/stats.json` (overview, runs, feed status, daily, leaderboard, signatures, recent episodes) |
| Episode | `GET /api/episode/<name>.json`, `GET /api/episode/<name>/meta.json` |
| Show | `GET /api/feed/<slug>/meta.json`, `GET /feeds/art/<slug>.jpg` (96px thumbnail) |
| Settings | `GET /api/settings.json`, `PUT /api/settings/policy.json`, `POST /api/settings/undo/<id>.json`, `POST /api/settings/retry.json` |
| Subscriptions | `POST /api/settings/subscriptions/{search,preview,add,remove}.json` |
| Cut audio | `GET /api/label/<name>/audio` (raw episode audio, for previewing a cut) |

**Needs a server change (later phase, separate server PR):** `GET /api/feeds.json` listing
published R2 feeds with their URLs. The web `/feeds` page is HTML only. The player needs the
URLs to subscribe to the ad-free feeds in-app and to offer "Add to Overcast".

## Phases

### 0. Foundation
- `SkipcastifyKit` package with tests. Hook it into the Xcode project as a local package.
- `APIClient`: base URL plus auth, async/await, typed errors (`unauthorized`, `conflict`,
  `server(message)`, `transport`, `decoding`), the server's `{"error": …}` messages surfaced.
- Models for every endpoint above; lenient ISO-8601 date decoding.
- Formatting helpers: `1,235 (20h 35m)` minutes, `m:ss` / `h:mm:ss`, relative times, binary
  sizes, percentages.
- Tests: fixture decoding for every endpoint, request building (method, path, headers,
  body), error mapping (via a stub `URLProtocol`), formatting.
- Dev loop: local server (real code, snapshot data, test credentials) on `127.0.0.1:5055`;
  the simulator reaches it directly.

### 1. Connection and app shell
- Replace the placeholder `SkipcastifyClient` and `StripCoordinator`. Remove strip badges,
  "Strip Ads" actions and "always strip" (the per-show cut policy replaces it).
- Settings: server URL, auth mode (none / Basic), Test Connection (hits `/api/stats.json`).
- `ServerStore` (@Observable): config, client, cached stats and settings, refresh and errors.
- Tabs: **Dashboard · Episodes · Shows · Library (player) · Settings**.
- Authenticated image loader with an in-memory cache. `AsyncImage` can't send auth headers.

### 2. Dashboard
- Overview cards: ad minutes removed (with days/hours/minutes), episodes processed, shows,
  LLM spend.
- Pipeline status: last run (time, outcome, duration), killed and failed runs highlighted,
  recent runs list.
- Daily throughput chart (Swift Charts: episodes as bars, ad minutes as a line); tapping a
  day lists its episodes.
- Ad-heaviness leaderboard (avg ad % default, toggle to total minutes).
- Pull to refresh.

### 3. Episodes
- Recent episodes list (artwork, title, show, released, duration, ad minutes and %), filter
  by show, search.
- Episode detail:
  - header (artwork, title, show, release date) and show notes from `meta.json`;
  - summary (original and processed length, cut seconds and %, cost);
  - **timeline bar** of cuts across the episode;
  - cuts list: time range, label, first/last line, expandable to the full transcript;
  - score against labels when the episode is reviewed;
  - processing runs; "Open in browser" for the web page.
- Preview a cut: play the raw audio from a few seconds before to a few seconds after,
  with an authenticated `AVPlayer` asset.

### 4. Shows (subscriptions and policy)
- Subscriptions list with artwork, last check, last download, on-R2 state; R2 usage vs cap.
- Add: search (Apple), paste URL, or pick from the Overcast export, then a preview card
  (cadence, typical size, R2 estimate, warning over the cap), then Subscribe.
- Remove with a confirmation that explains what happens to the R2 feed.
- Per-show cut policy: "Use default" toggle plus Ad / Self-promo / Intro-outro toggles;
  default policy editor; saves send the loaded version and handle 409.
- Change log with Undo; skipped episodes with Retry.
- Show detail: metadata (author, website, description), its episodes, its signature.

### 5. Player on ad-free feeds (needs `/api/feeds.json`)
- Server PR: `GET /api/feeds.json` (slug, title, url, episodes, sizes, updated).
- Library: "Add from Skipcastify" lists published feeds and subscribes in-app with the
  existing RSS and player stack.
- "Add to Overcast" and "Copy URL" per feed, like the web picker.

### 6. Polish and hardening
- Empty, loading and error states everywhere; offline: show the last loaded data with a
  "stale" note.
- Accessibility (labels, Dynamic Type), dark mode, iPad layout check.
- UI smoke tests against the local server (launch, tabs, episode detail, policy toggle).

### Later / out of scope for now
- Labeling on the phone (the web `/label` is keyboard-driven; a touch design is its own
  project).
- Push notifications for new episodes or failures (needs a server-side push channel).
- Widgets (today's ad minutes, last run status).

## Checks per phase
- `swift test` in `Packages/SkipcastifyKit` passes.
- `xcodebuild` builds the app for the simulator with no warnings in new code.
- The feature is exercised in the simulator against the local dev server, with screenshots.
- `docs/PLAN.md` progress notes updated at the end of each phase.

## Progress
(updated as phases land)
