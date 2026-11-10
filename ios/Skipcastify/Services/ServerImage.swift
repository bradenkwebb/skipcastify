import SkipcastifyKit
import SwiftUI
import UIKit

/// Loads images that need the server's auth headers, which `AsyncImage` can't send.
@MainActor
final class ImageCache {
    static let shared = ImageCache()
    private let cache = NSCache<NSURL, UIImage>()
    private var inFlight: [URL: Task<UIImage?, Never>] = [:]
    /// Shows without artwork 404; don't ask again every time a row appears.
    private var failed: [URL: Date] = [:]

    func image(for url: URL, headers: [String: String]) async -> UIImage? {
        if let hit = cache.object(forKey: url as NSURL) { return hit }
        if let when = failed[url], Date().timeIntervalSince(when) < 300 { return nil }
        if let task = inFlight[url] { return await task.value }
        let task = Task<UIImage?, Never> {
            var request = URLRequest(url: url)
            headers.forEach { request.setValue($1, forHTTPHeaderField: $0) }
            guard let (data, response) = try? await URLSession.shared.data(for: request),
                  (response as? HTTPURLResponse)?.statusCode == 200 else { return nil }
            return UIImage(data: data)
        }
        inFlight[url] = task
        let image = await task.value
        inFlight[url] = nil
        if let image { cache.setObject(image, forKey: url as NSURL) } else { failed[url] = Date() }
        return image
    }
}

/// A show's artwork thumbnail from the server, with a placeholder while loading or missing.
struct ShowArtwork: View {
    let slug: String
    var size: CGFloat = 44
    @Environment(ServerStore.self) private var server
    @State private var image: UIImage?

    var body: some View {
        ZStack {
            if let image {
                Image(uiImage: image).resizable().scaledToFill()
            } else {
                Rectangle().fill(.quaternary)
                Image(systemName: "waveform").foregroundStyle(.secondary).font(.system(size: size * 0.4))
            }
        }
        .frame(width: size, height: size)
        .clipShape(.rect(cornerRadius: size * 0.18))
        .accessibilityHidden(true)
        .task(id: slug) {
            guard let client = server.client else { return }
            image = await ImageCache.shared.image(for: client.artworkURL(slug), headers: client.authHeaders)
        }
    }
}
