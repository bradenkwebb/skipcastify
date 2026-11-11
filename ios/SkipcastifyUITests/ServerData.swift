import Foundation
import XCTest

/// Reads a little real data from the test server, so tests don't hard-code episode names.
enum ServerData {
    struct Recent: Decodable { let episode: String; let feed_slug: String }
    struct Stats: Decodable { let recent_episodes: [Recent] }
    struct Sub: Decodable { let slug: String; let title: String }
    struct Settings: Decodable { let subscriptions: [Sub] }

    static func stats() async throws -> Stats { try await get("api/stats.json") }
    static func settings() async throws -> Settings { try await get("api/settings.json") }

    struct LabelEpisode: Decodable { let name: String; let feed: String?; let reviewed: Bool; let has_predictions: Bool }

    /// An episode that's transcribed, has cuts, and isn't reviewed (so it can be edited).
    static func unreviewedEpisode() async throws -> LabelEpisode? {
        let all: [LabelEpisode] = try await get("api/label/episodes.json")
        return all.first { !$0.reviewed && $0.has_predictions && $0.feed != nil }
    }

    private static func get<T: Decodable>(_ path: String) async throws -> T {
        let env = AppHarness.env
        let base = try XCTUnwrap(env["SKIPCASTIFY_URL"])
        var request = URLRequest(url: try XCTUnwrap(URL(string: base + "/" + path)))
        if let user = env["SKIPCASTIFY_USER"] {
            let token = Data("\(user):\(env["SKIPCASTIFY_PASSWORD"] ?? "")".utf8).base64EncodedString()
            request.setValue("Basic \(token)", forHTTPHeaderField: "Authorization")
        }
        let (data, _) = try await URLSession.shared.data(for: request)
        return try JSONDecoder().decode(T.self, from: data)
    }
}
