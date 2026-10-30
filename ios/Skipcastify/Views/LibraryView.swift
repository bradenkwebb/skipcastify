import SwiftData
import SwiftUI

struct LibraryView: View {
    @Query(sort: \Podcast.title) private var podcasts: [Podcast]
    @Environment(\.modelContext) private var context
    @Environment(StripCoordinator.self) private var stripper

    var body: some View {
        NavigationStack {
            List {
                ForEach(podcasts) { podcast in
                    NavigationLink(value: podcast) { PodcastRow(podcast: podcast) }
                }
                .onDelete { offsets in
                    for index in offsets { context.delete(podcasts[index]) }
                }
            }
            .overlay {
                if podcasts.isEmpty {
                    ContentUnavailableView("No Podcasts", systemImage: "headphones",
                                           description: Text("Find a show in the Add tab."))
                }
            }
            .navigationTitle("Library")
            .navigationDestination(for: Podcast.self) { PodcastDetailView(podcast: $0) }
            .refreshable { await FeedService.refreshAll(in: context, stripper: stripper) }
        }
    }
}

private struct PodcastRow: View {
    let podcast: Podcast

    var body: some View {
        HStack(spacing: 12) {
            ArtworkView(url: podcast.artworkURL)
            VStack(alignment: .leading, spacing: 2) {
                Text(podcast.title).font(.headline).lineLimit(2)
                Text(podcast.author).font(.subheadline).foregroundStyle(.secondary).lineLimit(1)
                HStack(spacing: 6) {
                    Text("\(podcast.episodes.count) episodes")
                    if podcast.alwaysStripAds {
                        Label("Always ad-free", systemImage: "scissors").foregroundStyle(.tint)
                    }
                }
                .font(.caption)
                .foregroundStyle(.secondary)
            }
        }
    }
}
