import SkipcastifyKit
import SwiftData
import SwiftUI

/// Your server's ad-free feeds: subscribe in this app's player, or hand one to another app.
struct SkipcastifyFeedsView: View {
    @Environment(ServerStore.self) private var server
    @Environment(\.modelContext) private var context
    @Environment(\.dismiss) private var dismiss
    @Query private var library: [Podcast]
    @State private var feeds: PublishedFeeds?
    @State private var error: String?
    @State private var working: String?
    @State private var copied: String?

    var body: some View {
        NavigationStack {
            List {
                if let feeds {
                    if !feeds.publishingEnabled {
                        Text("Publishing is off on the server, so there are no ad-free feeds yet.")
                            .foregroundStyle(.secondary)
                    }
                    Section {
                        ForEach(feeds.feeds) { row($0) }
                    } footer: {
                        Text("Each URL works like a password: anyone holding it can play the audio. Only share it with apps you use.")
                    }
                } else if let error {
                    ContentUnavailableView("Couldn't Load Feeds", systemImage: "exclamationmark.triangle",
                                           description: Text(error))
                } else {
                    HStack { Spacer(); ProgressView(); Spacer() }
                }
            }
            .navigationTitle("Ad-free Feeds")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Done") { dismiss() } }
            }
            .task { await load() }
            .refreshable { await load() }
        }
    }

    private func row(_ feed: PublishedFeed) -> some View {
        let subscribed = library.contains { $0.feedURL == feed.url }
        return HStack(spacing: 12) {
            ShowArtwork(slug: feed.slug, size: 44)
            VStack(alignment: .leading, spacing: 2) {
                Text(title(feed)).font(.subheadline.weight(.medium)).lineLimit(2)
                Text(feed.episodes > 0
                     ? "\(feed.episodes) ad-free episode\(feed.episodes == 1 ? "" : "s") · \(Format.bytes(feed.audioBytes))"
                     : "No ad-free episodes yet")
                    .font(.caption).foregroundStyle(.secondary)
                if copied == feed.slug {
                    Text("URL copied").font(.caption).foregroundStyle(.green)
                }
            }
            Spacer(minLength: 4)
            if working == feed.slug {
                ProgressView()
            } else if subscribed {
                Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
                    .accessibilityLabel("In your library")
            } else {
                Button("Add") { Task { await subscribe(feed) } }
                    .buttonStyle(.bordered)
            }
        }
        .contextMenu {
            ForEach(feed.addLinks, id: \.self) { link in
                Link(destination: link.href) { Label("Add to \(link.name)", systemImage: "arrow.up.forward.app") }
            }
            Button {
                UIPasteboard.general.url = feed.url
                copied = feed.slug
            } label: { Label("Copy Feed URL", systemImage: "doc.on.doc") }
        }
    }

    /// The server falls back to the slug when it lacks a feed's local copy; prefer a real title.
    private func title(_ feed: PublishedFeed) -> String {
        feed.title == feed.slug ? server.showTitle(feed.slug) : feed.title
    }

    private func load() async {
        guard let client = server.client else { error = ServerStore.notConfigured; return }
        if server.settings == nil { await server.loadSettings() }  // for titles
        do {
            feeds = try await client.publishedFeeds()
            error = nil
        } catch APIError.server(status: 404, _) {
            error = "This server doesn't list its feeds yet. It needs the /api/feeds.json update."
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func subscribe(_ feed: PublishedFeed) async {
        working = feed.slug
        defer { working = nil }
        do {
            try await FeedService.subscribe(to: feed.url, in: context)
        } catch {
            self.error = "Couldn't add \(title(feed)): \(error.localizedDescription)"
        }
    }
}
