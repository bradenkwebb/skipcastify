import Foundation
import Testing
@testable import SkipcastifyKit

/// Decodes every read endpoint from a real server. Opt-in:
///   SKIPCASTIFY_LIVE_URL=http://127.0.0.1:5055 SKIPCASTIFY_LIVE_USER=dev SKIPCASTIFY_LIVE_PASSWORD=… swift test
@Suite(.enabled(if: ProcessInfo.processInfo.environment["SKIPCASTIFY_LIVE_URL"] != nil))
struct LiveServerTests {
    let client: APIClient = {
        let env = ProcessInfo.processInfo.environment
        let auth: Auth = env["SKIPCASTIFY_LIVE_USER"].map {
            .basic(username: $0, password: env["SKIPCASTIFY_LIVE_PASSWORD"] ?? "")
        } ?? .none
        return APIClient(baseURL: URL(string: env["SKIPCASTIFY_LIVE_URL"]!)!, auth: auth)
    }()

    @Test func everyReadEndpointDecodes() async throws {
        let stats = try await client.stats()
        let settings = try await client.settings()
        let all = try await client.labelEpisodes()
        #expect(!stats.recentEpisodes.isEmpty && !all.isEmpty)

        // Every recent episode's detail page, so odd ones (no transcript, never labeled) are covered.
        for summary in stats.recentEpisodes {
            _ = try await client.episode(summary.episode)
        }
        // The 7-day view and the published feeds.
        _ = try await client.stats(days: 7)
        _ = try await client.publishedFeeds()
        // Labels (transcript, cuts, spans) for every transcribed recent episode.
        let transcribed = Set(all.map(\.name))
        for summary in stats.recentEpisodes where transcribed.contains(summary.episode) {
            _ = try await client.labels(summary.episode)
        }

        let first = try #require(stats.recentEpisodes.first)
        _ = try await client.episodeMeta(first.episode)
        for sub in settings.subscriptions.prefix(3) {
            _ = try await client.showMeta(sub.slug)
            _ = try await client.show(sub.slug)
        }
    }
}
