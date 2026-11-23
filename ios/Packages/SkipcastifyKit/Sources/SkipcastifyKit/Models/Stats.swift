import Foundation

/// `GET /api/stats.json`: everything the dashboard shows.
public struct Stats: Decodable, Sendable {
    public let generatedAt: Date?
    public let overview: Overview
    public let runs: [Run]
    public let feedStatus: [FeedStatus]
    public let feeds: [FeedSummary]
    public let daily: [DailyPoint]
    public let signatures: [Signature]
    public let recentEpisodes: [EpisodeSummary]
    public let dropCandidates: [DropCandidate]
}

public struct Overview: Decodable, Sendable {
    public let episodesProcessed: Int
    public let feeds: Int
    public let firstEvent: Date?
    public let lastEvent: Date?
    public let totalAdMinutes: Double
    public let totalLlmCostUsd: Double?
}

/// One pipeline run, from `runs.jsonl`. `stage`/`error`/`episode` appear on failed or killed runs.
public struct Run: Decodable, Sendable, Identifiable {
    public let started: Date?
    public let durationS: Double?
    public let outcome: String
    public let feeds: [RunFeed]
    public let processed: [RunProcessed]
    public let published: Int?
    public let stage: String?
    public let error: String?
    public let episode: String?

    public var id: String { "\(started?.timeIntervalSince1970 ?? 0)-\(outcome)" }
    public var succeeded: Bool { outcome == "ok" }
    /// Runs that checked feeds and did nothing else; the web page hides these by default.
    public var isQuiet: Bool {
        succeeded && processed.isEmpty && feeds.allSatisfy { $0.status == "up_to_date" }
    }
}

public struct RunFeed: Decodable, Sendable {
    public let feed: String?
    public let status: String?
    public let notModified: Bool?
    public let downloaded: Int?
    public let failed: Int?
    public let skipped: Int?
}

public struct RunProcessed: Decodable, Sendable, Hashable {
    public let feed: String
    public let episode: String
}

public struct FeedStatus: Decodable, Sendable, Identifiable {
    public let feedSlug: String
    public let status: String?
    public let checked: Date?
    public let lastDownload: Date?
    public var id: String { feedSlug }
}

/// Per-show totals for the leaderboard.
public struct FeedSummary: Decodable, Sendable, Identifiable {
    public let feedSlug: String
    public let episodes: Int
    public let totalAdMinutes: Double
    public let avgAdMinutes: Double
    public let avgAdPct: Double
    public let adFreeStreak: Int?
    public let lastProcessed: Date?
    public var id: String { feedSlug }
}

public struct DailyPoint: Decodable, Sendable, Identifiable {
    /// `yyyy-MM-dd`, the server's calendar day.
    public let date: String
    public let episodes: Int
    public let feeds: Int
    public let adMinutes: Double
    public let list: [DayEpisode]?
    public var id: String { date }
    public var day: Date? { ServerDate.parse(date) }
}

public struct DayEpisode: Decodable, Sendable, Identifiable {
    public let feedSlug: String
    public let episode: String
    public let adMinutes: Double
    public let title: String?
    public let released: Date?
    public var id: String { episode }
}

/// Where ads fall in each of a show's recent episodes (positions are 0…1).
public struct Signature: Decodable, Sendable, Identifiable {
    public let feedSlug: String
    public let episodes: [SignatureEpisode]
    public var id: String { feedSlug }
}

public struct SignatureEpisode: Decodable, Sendable, Identifiable {
    public let episode: String
    public let title: String?
    public let released: Date?
    public let processed: Date?
    public let durationMin: Double
    public let adS: Double
    public let spans: [Span]
    /// Hand-reviewed episodes only: the cuts scored against the labels.
    public let reviewed: Bool?
    public let review: Review?
    public var id: String { episode }

    public struct Span: Decodable, Sendable, Hashable {
        public let start: Double
        public let end: Double
    }

    public struct Review: Decodable, Sendable {
        public let hit: [Span]
        public let missed: [Span]
        public let extra: [Span]
    }
}

/// A processed episode in the recent list.
public struct EpisodeSummary: Decodable, Sendable, Identifiable {
    public let ts: Date?
    public let feedSlug: String
    public let episode: String
    public let durationMin: Double
    public let adMinutes: Double
    public let adPct: Double
    public let title: String?
    public let released: Date?
    public var id: String { episode }
    /// The feed title when the server knows it, else a readable form of the file name.
    public var displayTitle: String { title ?? Slug.readableEpisode(episode, feed: feedSlug) }
}

public struct DropCandidate: Decodable, Sendable, Identifiable {
    public let feedSlug: String
    public let episodes: Int
    public let lastProcessed: Date?
    public var id: String { feedSlug }
}
