import Foundation

nonisolated struct ParsedFeed: Sendable {
    var title = ""
    var author = ""
    var summary = ""
    var artworkURL: URL?
    var episodes: [ParsedEpisode] = []
}

nonisolated struct ParsedEpisode: Sendable {
    var guid = ""
    var title = ""
    var summary = ""
    var pubDate: Date?
    var duration: TimeInterval?
    var audioURL: URL?
    var artworkURL: URL?
}

nonisolated enum FeedError: LocalizedError {
    case invalidFeed(String?)
    case http(Int)

    var errorDescription: String? {
        switch self {
        case .invalidFeed(let detail): "That doesn't look like a podcast feed. \(detail ?? "")"
        case .http(let code): "The feed server returned HTTP \(code)."
        }
    }
}

/// Minimal RSS 2.0 + iTunes namespace parser. Runs off the main actor.
nonisolated final class RSSParser: NSObject, XMLParserDelegate {
    private var feed = ParsedFeed()
    private var item: ParsedEpisode?
    private var text = ""
    private var stack: [String] = []

    static func parse(_ data: Data) throws -> ParsedFeed {
        let delegate = RSSParser()
        let parser = XMLParser(data: data)
        parser.delegate = delegate
        guard parser.parse() else {
            throw FeedError.invalidFeed(parser.parserError?.localizedDescription)
        }
        guard !delegate.feed.title.isEmpty || !delegate.feed.episodes.isEmpty else {
            throw FeedError.invalidFeed(nil)
        }
        return delegate.feed
    }

    func parser(_ parser: XMLParser, didStartElement elementName: String, namespaceURI: String?,
                qualifiedName qName: String?, attributes: [String: String] = [:]) {
        stack.append(elementName)
        text = ""
        switch elementName {
        case "item":
            item = ParsedEpisode()
        case "enclosure":
            if let url = attributes["url"].flatMap(Self.url) { item?.audioURL = url }
        case "itunes:image":
            guard let url = attributes["href"].flatMap(Self.url) else { break }
            if item != nil { item?.artworkURL = url } else { feed.artworkURL = url }
        default:
            break
        }
    }

    func parser(_ parser: XMLParser, foundCharacters string: String) {
        text += string
    }

    func parser(_ parser: XMLParser, foundCDATA CDATABlock: Data) {
        text += String(decoding: CDATABlock, as: UTF8.self)
    }

    func parser(_ parser: XMLParser, didEndElement elementName: String, namespaceURI: String?,
                qualifiedName qName: String?) {
        let value = text.trimmingCharacters(in: .whitespacesAndNewlines)
        stack.removeLast()
        let parent = stack.last

        if var current = item {
            switch elementName {
            case "item":
                if current.guid.isEmpty { current.guid = current.audioURL?.absoluteString ?? current.title }
                if current.audioURL != nil { feed.episodes.append(current) }
                item = nil
            case "title" where parent == "item":
                item?.title = value
            case "guid":
                item?.guid = value
            case "pubDate":
                item?.pubDate = Self.parseDate(value)
            case "itunes:duration":
                item?.duration = Self.parseDuration(value)
            case "description", "itunes:summary", "content:encoded":
                if current.summary.isEmpty { item?.summary = value }
            default:
                break
            }
        } else {
            switch (elementName, parent) {
            case ("title", "channel"): feed.title = value
            case ("itunes:author", "channel"): feed.author = value
            case ("description", "channel"): feed.summary = value
            case ("url", "image"):
                // <itunes:image> is usually higher resolution, so only fall back to <image><url>.
                if feed.artworkURL == nil { feed.artworkURL = Self.url(value) }
            default: break
            }
        }
        text = ""
    }

    // MARK: - Helpers

    private static func url(_ string: String) -> URL? {
        URL(string: string.trimmingCharacters(in: .whitespacesAndNewlines))
    }

    private static let dateFormatters: [DateFormatter] = [
        "EEE, dd MMM yyyy HH:mm:ss Z",
        "EEE, dd MMM yyyy HH:mm:ss zzz",
        "EEE, dd MMM yyyy HH:mm Z",
        "dd MMM yyyy HH:mm:ss Z",
        "EEE, d MMM yyyy HH:mm:ss Z",
        "yyyy-MM-dd'T'HH:mm:ssZ",
    ].map { format in
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = format
        return formatter
    }

    static func parseDate(_ string: String) -> Date? {
        for formatter in dateFormatters {
            if let date = formatter.date(from: string) { return date }
        }
        return nil
    }

    /// Accepts "HH:MM:SS", "MM:SS", or plain seconds.
    static func parseDuration(_ string: String) -> TimeInterval? {
        let parts = string.split(separator: ":").compactMap { Double($0) }
        guard !parts.isEmpty else { return nil }
        return parts.reduce(0) { $0 * 60 + $1 }
    }
}
