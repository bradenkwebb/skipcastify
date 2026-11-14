import Foundation

/// Readable fallbacks for when the server doesn't know a title.
public enum Slug {
    /// `odd-lots` → `Odd Lots`.
    public static func readableShow(_ slug: String) -> String {
        slug.split(separator: "-").map { $0.prefix(1).uppercased() + $0.dropFirst() }.joined(separator: " ")
    }

    /// `odd-lots-the_macro_implications` → `The macro implications`: episode file names are
    /// the feed slug, a dash, then the title with underscores.
    public static func readableEpisode(_ name: String, feed: String) -> String {
        var rest = Substring(name)
        if !feed.isEmpty, rest.hasPrefix(feed + "-") { rest = rest.dropFirst(feed.count + 1) }
        let words = rest.replacingOccurrences(of: "_", with: " ").trimmingCharacters(in: .whitespaces)
        guard let first = words.first else { return name }
        return first.uppercased() + words.dropFirst()
    }
}
