import Foundation
import SwiftData

enum FeedService {
    static func fetch(_ url: URL) async throws -> ParsedFeed {
        var request = URLRequest(url: url)
        request.setValue("Skipcastify/1.0", forHTTPHeaderField: "User-Agent")
        let (data, response) = try await URLSession.shared.data(for: request)
        if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
            throw FeedError.http(http.statusCode)
        }
        return try await Task.detached { try RSSParser.parse(data) }.value
    }

    @discardableResult
    static func subscribe(to url: URL, in context: ModelContext) async throws -> Podcast {
        let feedURL = normalize(url)
        let existing = try context.fetch(FetchDescriptor<Podcast>())
        if let podcast = existing.first(where: { $0.feedURL == feedURL }) { return podcast }

        let feed = try await fetch(feedURL)
        let podcast = Podcast(feedURL: feedURL, title: feed.title, author: feed.author,
                              summary: feed.summary, artworkURL: feed.artworkURL)
        context.insert(podcast)
        merge(feed, into: podcast, context: context)
        podcast.lastRefreshed = .now
        try context.save()
        return podcast
    }

    /// Re-fetches the feed and returns any episodes we hadn't seen before.
    @discardableResult
    static func refresh(_ podcast: Podcast, in context: ModelContext) async throws -> [Episode] {
        let feed = try await fetch(podcast.feedURL)
        if !feed.title.isEmpty { podcast.title = feed.title }
        podcast.author = feed.author
        podcast.summary = feed.summary
        podcast.artworkURL = feed.artworkURL ?? podcast.artworkURL
        let new = merge(feed, into: podcast, context: context)
        podcast.lastRefreshed = .now
        try context.save()
        return new
    }

    /// Refreshes one podcast and, if it's marked "always strip", queues its new episodes.
    static func refresh(_ podcast: Podcast, in context: ModelContext, stripper: StripCoordinator) async throws {
        let new = try await refresh(podcast, in: context)
        if podcast.alwaysStripAds {
            for episode in new { await stripper.requestStrip(episode) }
        }
    }

    static func refreshAll(in context: ModelContext, stripper: StripCoordinator) async {
        let podcasts = (try? context.fetch(FetchDescriptor<Podcast>())) ?? []
        for podcast in podcasts {
            try? await refresh(podcast, in: context, stripper: stripper)
        }
        await stripper.pollPending(in: context)
    }

    @discardableResult
    private static func merge(_ feed: ParsedFeed, into podcast: Podcast, context: ModelContext) -> [Episode] {
        let existing = Dictionary(podcast.episodes.map { ($0.guid, $0) }, uniquingKeysWith: { first, _ in first })
        var new: [Episode] = []
        for parsed in feed.episodes {
            guard let audioURL = parsed.audioURL else { continue }
            if let episode = existing[parsed.guid] {
                episode.title = parsed.title
                episode.summary = parsed.summary
                episode.duration = parsed.duration ?? episode.duration
                episode.audioURL = audioURL
                continue
            }
            let episode = Episode(feedURL: podcast.feedURL, guid: parsed.guid, title: parsed.title,
                                  summary: parsed.summary, pubDate: parsed.pubDate, duration: parsed.duration,
                                  audioURL: audioURL, artworkURL: parsed.artworkURL)
            context.insert(episode)
            episode.podcast = podcast
            new.append(episode)
        }
        return new
    }

    /// Handles `feed://`, `podcast://`, and bare hostnames.
    static func normalize(_ url: URL) -> URL {
        guard var components = URLComponents(url: url, resolvingAgainstBaseURL: false) else { return url }
        switch components.scheme?.lowercased() {
        case "feed", "podcast", "pcast", "itpc": components.scheme = "https"
        case nil: return URL(string: "https://\(url.absoluteString)") ?? url
        default: break
        }
        return components.url ?? url
    }
}
