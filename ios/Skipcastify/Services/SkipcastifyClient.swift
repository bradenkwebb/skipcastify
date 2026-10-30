import Foundation

// HTTP client for your ad-stripping server.
//
// ⚠️ The endpoint paths and JSON shapes below are placeholders — I don't know your server's API.
// Adjust `requestStrip`, `job`, `setAlwaysStrip`, and `StripJob` to match it; nothing else in
// the app needs to change.
//
//   GET  /api/health                     -> 200
//   POST /api/episodes                   {feed_url, guid, audio_url, title} -> StripJob
//   GET  /api/episodes/{id}              -> StripJob
//   PUT  /api/feeds                      {feed_url, always_strip}         -> 2xx

nonisolated struct StripJob: Decodable, Sendable {
    enum Status: String, Decodable, Sendable {
        case queued, processing, ready, failed
    }

    let id: String
    let status: Status
    /// The private, ad-free audio URL. Present once `status == .ready`.
    let url: URL?
    let error: String?
}

nonisolated private struct StripRequest: Encodable {
    let feedUrl: URL
    let guid: String
    let audioUrl: URL
    let title: String
}

nonisolated private struct FeedSettingRequest: Encodable {
    let feedUrl: URL
    let alwaysStrip: Bool
}

nonisolated enum SkipcastifyError: LocalizedError {
    case notConfigured
    case http(Int, String)

    var errorDescription: String? {
        switch self {
        case .notConfigured: "Set your Skipcastify server URL in Settings first."
        case .http(let code, let body): "Server returned HTTP \(code). \(body)"
        }
    }
}

struct SkipcastifyClient {
    static let baseURLKey = "skipcastify.baseURL"
    static let tokenKey = "skipcastify.token"

    let baseURL: URL
    let token: String?

    /// Built from Settings. `nil` until a server URL is configured.
    static var current: SkipcastifyClient? {
        guard let raw = UserDefaults.standard.string(forKey: baseURLKey)?.trimmingCharacters(in: .whitespaces),
              !raw.isEmpty, let url = URL(string: raw) else { return nil }
        return SkipcastifyClient(baseURL: url, token: Keychain.string(for: tokenKey))
    }

    var authHeaders: [String: String] {
        guard let token, !token.isEmpty else { return [:] }
        return ["Authorization": "Bearer \(token)"]
    }

    func health() async throws {
        _ = try await send("GET", "api/health")
    }

    func requestStrip(for episode: Episode) async throws -> StripJob {
        let body = StripRequest(feedUrl: episode.podcast?.feedURL ?? episode.audioURL, guid: episode.guid,
                                audioUrl: episode.audioURL, title: episode.title)
        return try decode(try await send("POST", "api/episodes", body: body))
    }

    func job(_ id: String) async throws -> StripJob {
        try decode(try await send("GET", "api/episodes/\(id)"))
    }

    func setAlwaysStrip(feedURL: URL, enabled: Bool) async throws {
        _ = try await send("PUT", "api/feeds", body: FeedSettingRequest(feedUrl: feedURL, alwaysStrip: enabled))
    }

    // MARK: - Plumbing

    private func send(_ method: String, _ path: String, body: (any Encodable)? = nil) async throws -> Data {
        var request = URLRequest(url: baseURL.appending(path: path))
        request.httpMethod = method
        request.timeoutInterval = 20
        for (field, value) in authHeaders { request.setValue(value, forHTTPHeaderField: field) }
        if let body {
            let encoder = JSONEncoder()
            encoder.keyEncodingStrategy = .convertToSnakeCase
            request.httpBody = try encoder.encode(body)
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        let (data, response) = try await URLSession.shared.data(for: request)
        if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
            throw SkipcastifyError.http(http.statusCode, String(decoding: data.prefix(200), as: UTF8.self))
        }
        return data
    }

    private func decode<T: Decodable>(_ data: Data) throws -> T {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(T.self, from: data)
    }
}
