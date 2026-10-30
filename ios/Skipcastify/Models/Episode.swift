import Foundation
import SwiftData

enum AdStripStatus: String, Codable {
    case notRequested
    case pending
    case ready
    case failed
}

@Model
final class Episode {
    /// Feed URL + GUID; GUIDs are only guaranteed unique within a feed.
    @Attribute(.unique) var key: String
    var guid: String
    var title: String
    var summary: String
    var pubDate: Date?
    var duration: TimeInterval?
    var audioURL: URL
    var artworkURL: URL?

    var playbackPosition: TimeInterval = 0
    var isPlayed: Bool = false

    var stripStatusRaw: String = AdStripStatus.notRequested.rawValue
    var stripJobID: String?
    /// The ad-free copy hosted by the Skipcastify server, once it's ready.
    var adFreeURL: URL?

    var podcast: Podcast?

    init(feedURL: URL, guid: String, title: String, summary: String, pubDate: Date?,
         duration: TimeInterval?, audioURL: URL, artworkURL: URL?) {
        self.key = Episode.makeKey(feedURL: feedURL, guid: guid)
        self.guid = guid
        self.title = title
        self.summary = summary
        self.pubDate = pubDate
        self.duration = duration
        self.audioURL = audioURL
        self.artworkURL = artworkURL
    }

    static func makeKey(feedURL: URL, guid: String) -> String {
        "\(feedURL.absoluteString)#\(guid)"
    }

    var stripStatus: AdStripStatus {
        get { AdStripStatus(rawValue: stripStatusRaw) ?? .notRequested }
        set { stripStatusRaw = newValue.rawValue }
    }

    /// Prefer the ad-free copy whenever we have one.
    var playbackURL: URL { adFreeURL ?? audioURL }

    var displayArtworkURL: URL? { artworkURL ?? podcast?.artworkURL }
}
