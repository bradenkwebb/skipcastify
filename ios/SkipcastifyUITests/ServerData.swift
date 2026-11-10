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
