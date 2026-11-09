import Foundation

/// How requests authenticate. With Tailscale identity auth on the server, `.none` works for
/// anyone on the tailnet; Basic is the fallback the server always accepts.
public enum Auth: Sendable, Equatable {
    case none
    case basic(username: String, password: String)

    var header: String? {
        guard case let .basic(user, pass) = self else { return nil }
        return "Basic " + Data("\(user):\(pass)".utf8).base64EncodedString()
    }
}

public enum APIError: LocalizedError, Equatable {
    case unauthorized
    /// The policy or subscription list changed since it was loaded (HTTP 409).
    case conflict(String)
    /// The server explained what was wrong (its `{"error": …}`), or just the status code.
    case server(status: Int, message: String?)
    case transport(String)
    case decoding(String)

    public var errorDescription: String? {
        switch self {
        case .unauthorized: "The server wants a login. Check the username and password in Settings."
        case .conflict(let message): message
        case .server(let status, let message): message ?? "The server returned HTTP \(status)."
        case .transport(let message): message
        case .decoding(let message): "Unexpected response from the server: \(message)"
        }
    }
}

/// A thin async client for the Skipcastify server's JSON API.
public struct APIClient: Sendable {
    public let baseURL: URL
    public let auth: Auth
    let session: URLSession

    public init(baseURL: URL, auth: Auth = .none, session: URLSession = .shared) {
        self.baseURL = baseURL
        self.auth = auth
        self.session = session
    }

    // MARK: Reads

    public func stats() async throws -> Stats { try await get("api/stats.json") }
    public func settings() async throws -> SettingsState { try await get("api/settings.json") }
    public func labelEpisodes() async throws -> [LabelEpisode] { try await get("api/label/episodes.json") }

    public func episode(_ name: String) async throws -> EpisodeDetail {
        try await get("api/episode/\(Self.segment(name)).json")
    }

    public func episodeMeta(_ name: String) async throws -> EpisodeMeta {
        try await get("api/episode/\(Self.segment(name))/meta.json")
    }

    public func showMeta(_ slug: String) async throws -> ShowMeta {
        try await get("api/feed/\(Self.segment(slug))/meta.json")
    }

    // MARK: Writes (the server requires X-Skipcastify and a JSON body on each)

    @discardableResult
    public func savePolicy(_ policy: CutPolicy, version: String) async throws -> Change? {
        try await write("PUT", "api/settings/policy.json", PolicyBody(policy: policy, version: version)).change
    }

    @discardableResult
    public func undo(_ changeID: Int, policyVersion: String, subscriptionsVersion: String) async throws -> Change? {
        let body = UndoBody(versions: .init(policy: policyVersion, subscriptions: subscriptionsVersion))
        return try await write("POST", "api/settings/undo/\(changeID).json", body).change
    }

    public func retry(_ episode: String) async throws {
        let _: Empty = try await send("POST", "api/settings/retry.json", body: EpisodeBody(episode: episode))
    }

    public func search(_ term: String) async throws -> [SearchResult] {
        let r: SearchResponse = try await send("POST", "api/settings/subscriptions/search.json", body: TermBody(term: term))
        return r.results
    }

    public func preview(_ url: String) async throws -> FeedPreview {
        try await send("POST", "api/settings/subscriptions/preview.json", body: URLBody(url: url))
    }

    @discardableResult
    public func subscribe(url: URL, title: String, version: String) async throws -> Change? {
        try await write("POST", "api/settings/subscriptions/add.json",
                        SubscriptionBody(url: url.absoluteString, title: title, version: version)).change
    }

    @discardableResult
    public func unsubscribe(url: URL, version: String) async throws -> Change? {
        try await write("POST", "api/settings/subscriptions/remove.json",
                        SubscriptionBody(url: url.absoluteString, title: nil, version: version)).change
    }

    // MARK: URLs other code loads itself (images, audio)

    public func artworkURL(_ slug: String) -> URL { url("feeds/art/\(Self.segment(slug)).jpg") }
    public func episodeAudioURL(_ name: String) -> URL { url("api/label/\(Self.segment(name))/audio") }
    public func episodePageURL(_ name: String) -> URL { url("dashboard/episode/\(Self.segment(name))") }

    /// Headers to attach when loading those URLs (e.g. via AVURLAsset or an image loader).
    public var authHeaders: [String: String] {
        auth.header.map { ["Authorization": $0] } ?? [:]
    }

    // MARK: Plumbing

    /// `path` is already percent-encoded, so join strings rather than append components.
    func url(_ path: String) -> URL {
        var base = baseURL.absoluteString
        while base.hasSuffix("/") { base.removeLast() }
        return URL(string: base + "/" + path)!
    }

    /// Percent-encodes one path segment (episode names can contain `!`, `'` or `&`).
    static func segment(_ s: String) -> String {
        var allowed = CharacterSet.urlPathAllowed
        allowed.remove(charactersIn: "/?#")
        return s.addingPercentEncoding(withAllowedCharacters: allowed) ?? s
    }

    func request(_ method: String, _ path: String, body: Data? = nil) -> URLRequest {
        var r = URLRequest(url: url(path))
        r.httpMethod = method
        r.setValue("application/json", forHTTPHeaderField: "Accept")
        if let h = auth.header { r.setValue(h, forHTTPHeaderField: "Authorization") }
        if let body {
            r.httpBody = body
            r.setValue("application/json", forHTTPHeaderField: "Content-Type")
            r.setValue("1", forHTTPHeaderField: "X-Skipcastify")
        }
        return r
    }

    func get<T: Decodable>(_ path: String) async throws -> T {
        try await perform(request("GET", path))
    }

    func send<T: Decodable, B: Encodable>(_ method: String, _ path: String, body: B) async throws -> T {
        try await perform(request(method, path, body: try JSONEncoder().encode(body)))
    }

    private func write<B: Encodable>(_ method: String, _ path: String, _ body: B) async throws -> ChangeResponse {
        try await send(method, path, body: body)
    }

    func perform<T: Decodable>(_ request: URLRequest) async throws -> T {
        let data: Data, response: URLResponse
        do {
            (data, response) = try await session.data(for: request)
        } catch {
            throw APIError.transport(error.localizedDescription)
        }
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard (200..<300).contains(status) else {
            let message = (try? JSONDecoder().decode(ErrorBody.self, from: data))?.error
            switch status {
            case 401: throw APIError.unauthorized
            case 409: throw APIError.conflict(message ?? "Changed elsewhere; reload and try again.")
            default: throw APIError.server(status: status, message: message)
            }
        }
        if T.self == Empty.self { return Empty() as! T }
        do {
            return try ServerDate.decoder().decode(T.self, from: data)
        } catch let error as DecodingError {
            throw APIError.decoding(Self.describe(error))
        }
    }

    static func describe(_ error: DecodingError) -> String {
        func path(_ c: DecodingError.Context) -> String {
            c.codingPath.map { $0.intValue.map(String.init) ?? $0.stringValue }.joined(separator: ".")
        }
        switch error {
        case .keyNotFound(let key, let c): return "missing \(path(c)).\(key.stringValue)"
        case .typeMismatch(_, let c), .valueNotFound(_, let c), .dataCorrupted(let c):
            return "\(path(c)): \(c.debugDescription)"
        @unknown default: return error.localizedDescription
        }
    }
}

// Request and response bodies, in the server's snake_case.
struct Empty: Decodable {}
struct ErrorBody: Decodable { let error: String? }
struct ChangeResponse: Decodable { let change: Change? }
struct SearchResponse: Decodable { let results: [SearchResult] }
struct PolicyBody: Encodable { let policy: CutPolicy; let version: String }
struct UndoBody: Encodable {
    let versions: Versions
    struct Versions: Encodable { let policy: String; let subscriptions: String }
}
struct EpisodeBody: Encodable { let episode: String }
struct TermBody: Encodable { let term: String }
struct URLBody: Encodable { let url: String }
struct SubscriptionBody: Encodable { let url: String; let title: String?; let version: String }
