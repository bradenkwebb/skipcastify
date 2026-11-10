import Foundation
import Testing
@testable import SkipcastifyKit

/// Request building and error mapping, against a stub instead of a server.
/// Serialized because the stub's recorded requests are shared state.
@Suite(.serialized) struct ClientTests {
    @Test func readsSendAuthAndNoWriteHeaders() async throws {
        StubProtocol.reset(body: String(decoding: try Fixture.data("stats"), as: UTF8.self))
        let client = StubProtocol.client(auth: .basic(username: "dev", password: "secret"))
        _ = try await client.stats()

        let r = try #require(StubProtocol.requests.first)
        #expect(r.httpMethod == "GET")
        #expect(r.url?.absoluteString == "https://dash.example.com/api/stats.json")
        #expect(r.value(forHTTPHeaderField: "Authorization") == "Basic " + Data("dev:secret".utf8).base64EncodedString())
        #expect(r.value(forHTTPHeaderField: "X-Skipcastify") == nil)
    }

    @Test func noAuthSendsNoAuthorizationHeader() async throws {
        StubProtocol.reset(body: String(decoding: try Fixture.data("settings"), as: UTF8.self))
        _ = try await StubProtocol.client().settings()
        #expect(StubProtocol.requests.first?.value(forHTTPHeaderField: "Authorization") == nil)
    }

    @Test func writesCarryTheCSRFHeaderAndJSONBody() async throws {
        StubProtocol.reset(body: #"{"change": null}"#)
        let policy = CutPolicy(default: ["ad"], feeds: ["odd-lots": ["ad", "self_promo"]])
        try await StubProtocol.client().savePolicy(policy, version: "abc123")

        let r = try #require(StubProtocol.requests.first)
        #expect(r.httpMethod == "PUT")
        #expect(r.url?.path == "/api/settings/policy.json")
        #expect(r.value(forHTTPHeaderField: "X-Skipcastify") == "1")
        #expect(r.value(forHTTPHeaderField: "Content-Type") == "application/json")
        let body = try JSONSerialization.jsonObject(with: StubProtocol.bodies[0]) as? [String: Any]
        #expect(body?["version"] as? String == "abc123")
        let sent = body?["policy"] as? [String: Any]
        #expect(sent?["default"] as? [String] == ["ad"])
        // Slugs keep their hyphens: no key conversion on the way out.
        #expect((sent?["feeds"] as? [String: [String]])?["odd-lots"] == ["ad", "self_promo"])
    }

    @Test func episodeNamesArePercentEncodedOnce() async throws {
        StubProtocol.reset(body: String(decoding: try Fixture.data("episode"), as: UTF8.self))
        _ = try await StubProtocol.client(base: "https://dash.example.com/").episode("show-what's_new?_&_more")
        let url = try #require(StubProtocol.requests.first?.url?.absoluteString)
        #expect(url == "https://dash.example.com/api/episode/show-what's_new%3F_&_more.json")
    }

    @Test func undoSendsBothVersions() async throws {
        StubProtocol.reset(body: #"{"change": null}"#)
        try await StubProtocol.client().undo(42, policyVersion: "p1", subscriptionsVersion: "s1")
        #expect(StubProtocol.requests.first?.url?.path == "/api/settings/undo/42.json")
        let body = try JSONSerialization.jsonObject(with: StubProtocol.bodies[0]) as? [String: [String: String]]
        #expect(body?["versions"] == ["policy": "p1", "subscriptions": "s1"])
    }

    @Test func retryIgnoresTheResponseBody() async throws {
        StubProtocol.reset(body: #"{"ok": true}"#)
        try await StubProtocol.client().retry("show/ep.mp3")
        let body = try JSONSerialization.jsonObject(with: StubProtocol.bodies[0]) as? [String: String]
        #expect(body == ["episode": "show/ep.mp3"])
    }

    @Test(arguments: [
        (401, "{}", APIError.unauthorized),
        (409, #"{"error": "policy.yaml changed"}"#, APIError.conflict("policy.yaml changed")),
        (400, #"{"error": "enter an http(s) feed URL"}"#, APIError.server(status: 400, message: "enter an http(s) feed URL")),
        (502, "not json", APIError.server(status: 502, message: nil)),
    ])
    func errorsAreMapped(status: Int, body: String, expected: APIError) async throws {
        StubProtocol.reset(status: status, body: body)
        await #expect(throws: expected) { try await StubProtocol.client().preview("x") }
    }

    @Test func badJSONIsADecodingErrorWithAPath() async throws {
        StubProtocol.reset(body: #"{"results": [{"title": "x"}]}"#)  // url missing
        do {
            _ = try await StubProtocol.client().search("x")
            Issue.record("expected a decoding error")
        } catch let APIError.decoding(message) {
            #expect(message.contains("results.0") && message.contains("url"))
        }
    }

    @Test func assetURLsAndHeaders() {
        let c = APIClient(baseURL: URL(string: "https://dash.example.com")!, auth: .basic(username: "u", password: "p"))
        #expect(c.artworkURL("odd-lots").absoluteString == "https://dash.example.com/feeds/art/odd-lots.jpg")
        #expect(c.episodeAudioURL("a-b").path == "/api/label/a-b/audio")
        #expect(c.authHeaders["Authorization"]?.hasPrefix("Basic ") == true)
        #expect(APIClient(baseURL: URL(string: "https://x")!).authHeaders.isEmpty)
    }
}
