import Foundation

/// `skipcastify://` links into the app:
///   skipcastify://dashboard | episodes | shows | library | settings
///   skipcastify://episode/<name>[?feed=<slug>]
///   skipcastify://show/<slug>
public enum DeepLink: Equatable, Sendable {
    case tab(String)
    case episode(name: String, feed: String?)
    case show(slug: String)

    public static let scheme = "skipcastify"
    static let tabs: Set<String> = ["dashboard", "episodes", "shows", "library", "settings"]

    public static func parse(_ url: URL) -> DeepLink? {
        guard url.scheme?.lowercased() == scheme, let host = url.host()?.lowercased() else { return nil }
        let parts = url.pathComponents.filter { $0 != "/" }
        switch host {
        case "episode":
            guard let name = parts.first, !name.isEmpty else { return nil }
            let feed = URLComponents(url: url, resolvingAgainstBaseURL: false)?
                .queryItems?.first { $0.name == "feed" }?.value
            return .episode(name: name, feed: feed)
        case "show":
            guard let slug = parts.first, !slug.isEmpty else { return nil }
            return .show(slug: slug)
        default:
            return tabs.contains(host) ? .tab(host) : nil
        }
    }

    public var url: URL {
        var c = URLComponents()
        c.scheme = Self.scheme
        switch self {
        case .tab(let tab):
            c.host = tab
        case .episode(let name, let feed):
            c.host = "episode"
            c.path = "/" + name
            if let feed { c.queryItems = [URLQueryItem(name: "feed", value: feed)] }
        case .show(let slug):
            c.host = "show"
            c.path = "/" + slug
        }
        return c.url!
    }
}
