# Skipcastify (iOS client)

A native companion to the [Skipcastify](https://github.com/bradenkwebb/skipcastify) ad-stripping
server, plus a podcast player.

- **Dashboard:** ad minutes removed, pipeline health and recent runs, daily throughput (tap a
  day for its episodes), the ad-heaviness leaderboard.
- **Episodes:** recent processed episodes and every transcribed one; each episode's cuts on a
  timeline, the transcript and rationale for each cut, a preview of the audio around a cut,
  the score against your labels, show notes and processing runs.
- **Shows:** subscriptions with R2 usage; add a show (search, URL, or your Overcast export)
  after previewing its cadence and R2 cost; per-show and default cut policy; unsubscribe;
  the change log with Undo; skipped episodes with Retry.
- **Library:** a podcast player. Add your server's ad-free feeds, or any podcast.
- **Links:** `skipcastify://episode/<name>`, `skipcastify://show/<slug>`, `skipcastify://<tab>`.

The plan and progress live in [docs/PLAN.md](docs/PLAN.md).

## Run it

1. Open `Skipcastify.xcodeproj` in Xcode, pick an iPhone simulator and press ⌘R.
2. In **Settings**, enter your server's address. With Tailscale identity sign-in on the server,
   choose **None (Tailscale)**. Otherwise enter the dashboard's username and password (the
   password is stored in the Keychain, on this device only).

To run on your own phone, set your team under **Signing & Capabilities**.

## Layout

```
Packages/SkipcastifyKit/   Models, API client, formatting, deep links: no UI, tested with `swift test`
Skipcastify/
  Services/                ServerStore (connection + shared data), Router, image cache,
                           cut preview player, and the podcast player (AudioPlayer, RSS, feeds)
  Views/                   Dashboard/, Episodes/, Shows/, Library and player screens, Settings
SkipcastifyUITests/        XCUITests, run against a test server
scripts/                   build.sh, run-sim.sh, uitest.sh
```

Feed URLs from your server carry its path secret, so the library is stored locally only (no
iCloud), nothing logs URLs, and feeds leave the app only when you tap Add to… or Copy.

## Tests

```bash
cd Packages/SkipcastifyKit && swift test
```

Fixtures are trimmed copies of real responses. To also decode every endpoint from a live server:

```bash
SKIPCASTIFY_LIVE_URL=http://127.0.0.1:5055 SKIPCASTIFY_LIVE_USER=dev SKIPCASTIFY_LIVE_PASSWORD=… swift test
```

UI tests launch the app against a server. Point them only at a disposable one, since some
tests change the cut policy:

```bash
SKIPCASTIFY_URL=http://127.0.0.1:5055 SKIPCASTIFY_USER=dev SKIPCASTIFY_PASSWORD=… scripts/uitest.sh
```

A local test server is the real server code run on a copy of its data, with test credentials
(`FEED_USERNAME`/`FEED_PASSWORD`) and `CUT_POLICY`, `SUBSCRIPTIONS` and `UPSTREAM_OPML`
pointing at scratch copies.
