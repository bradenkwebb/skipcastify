# Skipcastify (iOS client)

A small SwiftUI podcast player that's also a client for the Skipcastify ad-stripping service.

## Run it

1. Open `Skipcastify.xcodeproj` in Xcode.
2. Pick an iPhone simulator in the toolbar and press ⌘R.

To run on your own phone: select the **Skipcastify** target → **Signing & Capabilities** → pick your
Apple ID team, and change the bundle identifier from `com.example.skipcastify` to something unique.

## Layout

```
Skipcastify/
  SkipcastifyApp.swift        App entry; SwiftData container + shared player/strip coordinator
  Models/                     SwiftData models (Podcast, Episode)
  Services/
    RSSParser.swift           RSS + iTunes-namespace parsing (XMLParser)
    FeedService.swift         Subscribe / refresh / merge new episodes
    PodcastSearch.swift       iTunes Search API (no key needed)
    AudioPlayer.swift         AVPlayer, background audio, lock screen, speed, resume position
    SkipcastifyClient.swift   HTTP client for your server  ← edit endpoints here
    StripCoordinator.swift    Requests strips, polls jobs, syncs "always strip" per feed
    Keychain.swift            Stores the API token
  Views/                      SwiftUI screens
Config/Info.plist             Background audio + ATS (merged with generated Info.plist)
```

## Server integration

`SkipcastifyClient.swift` assumes this API (adjust to match the real server):

| Method | Path                  | Body                                   | Returns    |
|--------|-----------------------|----------------------------------------|------------|
| GET    | `/api/health`         |                                        | 2xx        |
| POST   | `/api/episodes`       | `{feed_url, guid, audio_url, title}`   | `StripJob` |
| GET    | `/api/episodes/{id}`  |                                        | `StripJob` |
| PUT    | `/api/feeds`          | `{feed_url, always_strip}`             | 2xx        |

`StripJob` is `{id, status: queued|processing|ready|failed, url?, error?}`. When a job is `ready`,
the episode plays from `url` instead of the original enclosure.
