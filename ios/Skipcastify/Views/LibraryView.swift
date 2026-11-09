import SwiftData
import SwiftUI

struct LibraryView: View {
    @Query(sort: \Podcast.title) private var podcasts: [Podcast]
    @Environment(\.modelContext) private var context
    @State private var showAdd = false
    @State private var showFeeds = false

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
                                           description: Text("Tap + to find a show."))
                }
            }
            .navigationTitle("Library")
            .navigationDestination(for: Podcast.self) { PodcastDetailView(podcast: $0) }
            .refreshable { await FeedService.refreshAll(in: context) }
            .toolbar {
                Menu {
                    Button { showFeeds = true } label: { Label("Ad-free Feeds from Skipcastify", systemImage: "scissors") }
                    Button { showAdd = true } label: { Label("Search All Podcasts", systemImage: "magnifyingglass") }
                } label: { Label("Add Podcast", systemImage: "plus") }
            }
            .sheet(isPresented: $showAdd) { AddPodcastView() }
            .sheet(isPresented: $showFeeds) { SkipcastifyFeedsView() }
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
                Text("\(podcast.episodes.count) episodes")
                    .font(.caption)
                .foregroundStyle(.secondary)
            }
        }
    }
}
