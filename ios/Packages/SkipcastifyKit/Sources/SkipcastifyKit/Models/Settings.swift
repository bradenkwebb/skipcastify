import Foundation

/// `GET /api/settings.json`: cut policy, subscriptions, change log and skipped episodes.
public struct SettingsState: Decodable, Sendable {
    /// Hash of policy.yaml as loaded; sent back on save so a stale edit gets a 409.
    public let version: String
    public let policy: CutPolicy
    /// Set when policy.yaml is invalid; editing is disabled until it's fixed by hand.
    public let error: String?
    public let labels: [String]
    public let feeds: [FeedRef]
    public let changes: [Change]
    public let skipped: [SkippedEpisode]
    public let subscriptions: [Subscription]
    public let subsVersion: String
    public let upstream: [UpstreamFeed]
    public let r2: R2Usage
}

/// Which labels get cut: a default, plus per-show overrides.
public struct CutPolicy: Codable, Sendable, Equatable {
    public var `default`: [String]
    public var feeds: [String: [String]]

    public init(default: [String], feeds: [String: [String]]) {
        self.default = `default`
        self.feeds = feeds
    }

    public func labels(for slug: String) -> [String] { feeds[slug] ?? `default` }
    public func usesDefault(_ slug: String) -> Bool { feeds[slug] == nil }

    /// Turns one label on or off for a show (or the default when `slug` is nil),
    /// copying the default into a new override first. Keeps the server's label order.
    public mutating func set(_ label: String, _ on: Bool, for slug: String?, order: [String]) {
        var current = slug.map { labels(for: $0) } ?? `default`
        if on, !current.contains(label) { current.append(label) }
        if !on { current.removeAll { $0 == label } }
        current.sort { (order.firstIndex(of: $0) ?? .max) < (order.firstIndex(of: $1) ?? .max) }
        if let slug { feeds[slug] = current } else { `default` = current }
    }

    public mutating func useDefault(_ on: Bool, for slug: String) {
        feeds[slug] = on ? nil : `default`
    }

    // The server takes `feeds` as {slug: [labels] | null}; null means "use the default".
    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(`default`, forKey: .default)
        try c.encode(feeds, forKey: .feeds)
    }
}

public struct FeedRef: Decodable, Sendable, Identifiable, Hashable {
    public let slug: String
    public let title: String
    public var id: String { slug }
}

/// One saved change to the policy or the subscription list.
public struct Change: Decodable, Sendable, Identifiable {
    public let id: Int
    public let ts: Date?
    public let kind: String?
    public let user: String?
    public let summary: [String]
    public let undoOf: Int?
}

public struct SkippedEpisode: Decodable, Sendable, Identifiable {
    /// `slug/file.mp3`, which is also what Retry sends back.
    public let episode: String
    public let attempts: Int
    public var id: String { episode }
}

public struct Subscription: Decodable, Sendable, Identifiable {
    public let title: String
    public let url: URL
    public let slug: String
    public let checked: Date?
    public let lastDownload: Date?
    public let published: Bool
    public var id: String { url.absoluteString }
}

public struct UpstreamFeed: Decodable, Sendable, Identifiable, Hashable {
    public let title: String
    public let url: URL
    public var id: String { url.absoluteString }
}

public struct R2Usage: Decodable, Sendable {
    public let usedBytes: Int64
    public let capBytes: Int64
    public var fraction: Double { capBytes > 0 ? Double(usedBytes) / Double(capBytes) : 0 }
    public var remainingBytes: Int64 { max(capBytes - usedBytes, 0) }
}

/// `POST /api/settings/subscriptions/preview.json`: what subscribing to a feed would mean.
public struct FeedPreview: Decodable, Sendable {
    public let url: URL
    public let already: Bool
    public let title: String
    public let slug: String
    public let author: String?
    public let image: URL?
    public let episodes: Int
    public let latest: Date?
    /// Episodes per 14 days (the R2 retention), from the last 90 days.
    public let perWindow: Double
    public let avgBytes: Int64
    public let estBytes: Int64
}

public struct SearchResult: Decodable, Sendable, Identifiable, Hashable {
    public let title: String?
    public let author: String?
    public let url: URL
    public var id: String { url.absoluteString }
}

/// `GET /api/label/episodes.json`: every transcribed episode, for browsing beyond "recent".
public struct LabelEpisode: Decodable, Sendable, Identifiable {
    public let name: String
    public let feed: String?
    public let date: Date?
    public let released: Date?
    public let durationS: Double?
    public let hasPredictions: Bool
    public let reviewed: Bool
    public let labeledSpans: Int?
    public var id: String { name }
}

/// `GET /api/feeds.json`: the ad-free feeds published to R2. URLs carry the path secret.
public struct PublishedFeeds: Decodable, Sendable {
    public let publishingEnabled: Bool
    public let feeds: [PublishedFeed]
}

public struct PublishedFeed: Decodable, Sendable, Identifiable {
    public let slug: String
    public let title: String
    public let url: URL
    public let episodes: Int
    public let audioBytes: Int64
    public let lastAudioUpload: Date?
    public let feedUploaded: Date?
    public let addLinks: [AddLink]
    public var id: String { slug }

    /// A podcast app's documented "subscribe to this feed" link.
    public struct AddLink: Decodable, Sendable, Hashable {
        public let app: String
        public let name: String
        public let href: URL
        public let hint: String?
    }
}
