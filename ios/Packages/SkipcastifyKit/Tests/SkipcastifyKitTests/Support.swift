import Foundation
import Testing
@testable import SkipcastifyKit

enum Fixture {
    static func data(_ name: String) throws -> Data {
        let url = try #require(Bundle.module.url(forResource: name, withExtension: "json", subdirectory: "Fixtures"))
        return try Data(contentsOf: url)
    }

    static func decode<T: Decodable>(_ type: T.Type, _ name: String) throws -> T {
        try ServerDate.decoder().decode(T.self, from: data(name))
    }
}

/// Serves canned responses and records requests, for testing the client without a server.
final class StubProtocol: URLProtocol, @unchecked Sendable {
    struct Reply { var status = 200; var body = Data("{}".utf8) }
    nonisolated(unsafe) static var reply = Reply()
    nonisolated(unsafe) static var requests: [URLRequest] = []
    nonisolated(unsafe) static var bodies: [Data] = []

    static func reset(status: Int = 200, body: String = "{}") {
        reply = Reply(status: status, body: Data(body.utf8))
        requests = []
        bodies = []
    }

    static func client(auth: Auth = .none, base: String = "https://dash.example.com") -> APIClient {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [StubProtocol.self]
        return APIClient(baseURL: URL(string: base)!, auth: auth, session: URLSession(configuration: config))
    }

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        Self.requests.append(request)
        Self.bodies.append(Self.readBody(request))
        let response = HTTPURLResponse(url: request.url!, statusCode: Self.reply.status,
                                       httpVersion: "HTTP/1.1", headerFields: ["Content-Type": "application/json"])!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Self.reply.body)
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}

    /// URLSession moves bodies into a stream before protocols see them.
    private static func readBody(_ r: URLRequest) -> Data {
        if let body = r.httpBody { return body }
        guard let stream = r.httpBodyStream else { return Data() }
        stream.open(); defer { stream.close() }
        var data = Data()
        var buffer = [UInt8](repeating: 0, count: 4096)
        while stream.hasBytesAvailable {
            let n = stream.read(&buffer, maxLength: buffer.count)
            if n <= 0 { break }
            data.append(buffer, count: n)
        }
        return data
    }
}
