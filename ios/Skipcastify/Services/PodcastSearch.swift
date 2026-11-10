import Foundation

nonisolated struct PodcastSearchResult: Decodable, Identifiable, Sendable {
    let collectionId: Int
    let collectionName: String
    let artistName: String?
    let feedUrl: String?
    let artworkUrl600: String?

    var id: Int { collectionId }
    var feedURL: URL? { feedUrl.flatMap(URL.init(string:)) }
    var artworkURL: URL? { artworkUrl600.flatMap(URL.init(string:)) }
}

nonisolated private struct SearchResponse: Decodable {
    let results: [PodcastSearchResult]
}

/// Uses Apple's public iTunes Search API — no key required.
enum PodcastSearch {
    static func search(_ term: String) async throws -> [PodcastSearchResult] {
        var components = URLComponents(string: "https://itunes.apple.com/search")!
        components.queryItems = [
            URLQueryItem(name: "media", value: "podcast"),
            URLQueryItem(name: "entity", value: "podcast"),
            URLQueryItem(name: "limit", value: "25"),
            URLQueryItem(name: "term", value: term),
        ]
        let (data, _) = try await URLSession.shared.data(from: components.url!)
        return try JSONDecoder().decode(SearchResponse.self, from: data).results.filter { $0.feedURL != nil }
    }
}
