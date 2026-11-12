import Foundation

/// `GET /api/episode/<name>.json`: one processed episode, its cuts and how it was processed.
public struct EpisodeDetail: Decodable, Sendable {
    public let name: String
    public let feed: String?
    public let title: String?
    public let released: Date?
    public let hasAudio: Bool
    public let hasTranscript: Bool
    public let cutLabels: [String]
    public let cuts: [Cut]
    public let kept: [Cut]?
    public let labels: Labels?
    public let score: Score?
    public let runs: [ProcessingRun]
    public let latest: ProcessingRun?
    /// What a podcast app subscribed to the published feed would play; nil when unpublished.
    public let playback: Playback?

    public var displayTitle: String { title ?? Slug.readableEpisode(name, feed: feed ?? "") }
    /// Original length in ms, from the latest run; nil when never processed.
    public var durationMs: Int? { latest?.originalDurationS.map { Int($0 * 1000) } }
}

/// Where an episode plays from. Both URLs carry the server's path secret.
public struct Playback: Decodable, Sendable, Hashable {
    public let url: URL
    public let feedUrl: URL
    /// Our cut copy on R2; otherwise the publisher's original audio.
    public let adFree: Bool
    /// Processing cut nothing, so the original is already ad-free.
    public let uncut: Bool

    public var description: String {
        if adFree { return "Ad-free" }
        return uncut ? "Original audio · nothing to cut" : "Original audio · ads not removed"
    }
}

/// A span that was cut (or, in `kept`, one the policy kept).
public struct Cut: Decodable, Sendable, Identifiable, Hashable {
    public let startMs: Int
    public let endMs: Int
    public let label: String
    public let firstLine: String?
    public let lastLine: String?
    public let lines: [Line]?
    public let rationales: [String]?
    public var id: Int { startMs }
    public var durationMs: Int { endMs - startMs }

    public struct Line: Decodable, Sendable, Hashable, Identifiable {
        public let startMs: Int
        public let text: String
        public var id: Int { startMs }
    }
}

/// Hand labels from /label, when the episode has been reviewed.
public struct Labels: Decodable, Sendable {
    public let reviewed: Bool
    public let spans: [Span]

    public struct Span: Decodable, Sendable, Hashable {
        public let startMs: Int
        public let endMs: Int
        public let label: String
    }
}

/// Cuts compared with the labels, in ms of spoken audio.
public struct Score: Decodable, Sendable {
    public let spokenMs: Int
    public let truthMs: Int
    public let cutMs: Int
    public let missedMs: Int
    public let wrongMs: Int

    /// Share of labeled ad time that was cut.
    public var recall: Double? { truthMs > 0 ? Double(truthMs - missedMs) / Double(truthMs) : nil }
    /// Share of cut time that was really an ad.
    public var precision: Double? { cutMs > 0 ? Double(cutMs - wrongMs) / Double(cutMs) : nil }
}

/// One processing attempt.
public struct ProcessingRun: Decodable, Sendable, Identifiable {
    public let started: Date?
    public let completed: Date?
    /// Missing on attempts that failed before the audio was measured.
    public let originalDurationS: Double?
    public let processedDurationS: Double?
    public let adsRemovedS: Double?
    public let adsPct: Double?
    public let spanCount: Int?
    public let costUsd: Double?
    public let llmCalls: Int?
    public let promptTokens: Int?
    public let transcribeS: Double?
    public let whisperModel: String?
    public var id: Date { started ?? completed ?? .distantPast }
}

/// `GET /api/episode/<name>/meta.json` and `/api/feed/<slug>/meta.json`: fetched from the
/// show's own RSS on demand, so either half may be missing.
public struct EpisodeMeta: Decodable, Sendable {
    public let show: ShowInfo?
    public let episode: EpisodeInfo?
    public let error: String?
}

public struct ShowMeta: Decodable, Sendable {
    public let show: ShowInfo?
    public let error: String?
}

public struct ShowInfo: Decodable, Sendable {
    public let title: String?
    public let author: String?
    public let website: URL?
    public let image: URL?
    public let description: [[NoteRun]]?
}

public struct EpisodeInfo: Decodable, Sendable {
    public let title: String?
    public let link: URL?
    public let image: URL?
    public let episode: String?
    public let season: String?
    /// Show notes as paragraphs of text and link runs; the server strips all markup.
    public let notes: [[NoteRun]]?
}

public struct NoteRun: Decodable, Sendable, Hashable {
    public let text: String
    public let href: URL?
}
