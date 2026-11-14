import Foundation
import SwiftData

@Model
final class Podcast {
    @Attribute(.unique) var feedURL: URL
    var title: String
    var author: String
    var summary: String
    var artworkURL: URL?
    var dateAdded: Date = Date.now
    var lastRefreshed: Date?

    @Relationship(deleteRule: .cascade, inverse: \Episode.podcast)
    var episodes: [Episode] = []

    init(feedURL: URL, title: String, author: String = "", summary: String = "", artworkURL: URL? = nil) {
        self.feedURL = feedURL
        self.title = title
        self.author = author
        self.summary = summary
        self.artworkURL = artworkURL
    }

    var episodesNewestFirst: [Episode] {
        episodes.sorted { ($0.pubDate ?? .distantPast) > ($1.pubDate ?? .distantPast) }
    }
}
