import Foundation

/// `GET /api/show/<slug>.json`: one show as the web show page draws it.
public struct ShowDetail: Decodable, Sendable {
    public let slug: String
    public let title: String?
    public let subscribed: Bool
    public let status: Status
    public let stats: Stats?
    public let policy: Policy
    public let signature: [SignatureEpisode]
    public let history: [HistoryPoint]
    public let recentEpisodes: [EpisodeSummary]

    public struct Status: Decodable, Sendable {
        public let checked: Date?
        public let lastDownload: Date?
        public let published: Bool
        public let publishedEpisodes: Int
        public let publishedBytes: Int
    }

    public struct Stats: Decodable, Sendable {
        public let episodes: Int
        public let totalAdMinutes: Double
        public let avgAdMinutes: Double
        public let avgAdPct: Double
        public let adFreeStreak: Int?
        public let lastProcessed: Date?
    }

    /// `cut` is nil when policy.yaml doesn't parse; `error` says why.
    public struct Policy: Decodable, Sendable {
        public let cut: [String]?
        public let usesDefault: Bool?
        public let error: String?
    }

    /// One processed episode's timing, for the cadence and processing charts.
    public struct HistoryPoint: Decodable, Sendable, Identifiable {
        public let episode: String
        public let released: Date?
        public let started: Date?
        public let completed: Date?
        public let processingS: Double?
        public let waitS: Double?
        public let transcribeS: Double?
        public let durationMin: Double
        public var id: String { episode }
    }
}
